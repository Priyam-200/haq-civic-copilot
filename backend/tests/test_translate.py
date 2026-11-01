"""Titles shown to the user are translated, cached, and never break the page if the AI fails."""
import json

import pytest
from fastapi.testclient import TestClient

import llm.translate as tr


@pytest.fixture(autouse=True)
def fresh_cache():
    import db
    with db._lock, db._conn() as c:
        c.execute("DELETE FROM title_cache")


def numbered(prompt):
    return [line.split(". ", 1)[1] for line in prompt.split("\n") if line[:1].isdigit() and ". " in line]


def test_english_is_returned_unchanged_without_calling_the_ai(monkeypatch):
    monkeypatch.setattr(tr, "ask_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no AI call for English")))
    assert tr.translate_texts(["Cybercrime Awareness Campaign 2026"], "English") == ["Cybercrime Awareness Campaign 2026"]


def test_translates_once_then_serves_from_the_cache(monkeypatch):
    calls = []

    def fake(prompt, rounds=3, json_mode=False):
        calls.append(prompt)
        return json.dumps({"translations": [f"हिंदी: {t}" for t in numbered(prompt)]})
    monkeypatch.setattr(tr, "ask_llm", fake)
    out = tr.translate_texts(["Truth Always Triumphs", "Ask Our Experts", "Truth Always Triumphs"], "Hindi")
    assert out == ["हिंदी: Truth Always Triumphs", "हिंदी: Ask Our Experts", "हिंदी: Truth Always Triumphs"]
    assert len(calls) == 1                                  # duplicates are sent once
    assert tr.translate_texts(["Ask Our Experts"], "Hindi") == ["हिंदी: Ask Our Experts"]
    assert len(calls) == 1                                  # second time: no AI call at all


def test_a_mixed_script_translation_is_retried(monkeypatch):
    answers = iter([["சत्यமே ஜெயதே"], ["சத்யமேவ ஜயதே"]])        # first answer has Devanagari inside Tamil
    monkeypatch.setattr(tr, "ask_llm", lambda *a, **k: json.dumps({"translations": next(answers)}))
    assert tr.translate_texts(["Truth Always Triumphs"], "Tamil") == ["சத்யமேவ ஜயதே"]


def test_still_mixed_after_retry_falls_back_to_the_original(monkeypatch):
    monkeypatch.setattr(tr, "ask_llm", lambda *a, **k: json.dumps({"translations": ["சत्यமே"]}))
    assert tr.translate_texts(["Truth Always Triumphs"], "Tamil") == ["Truth Always Triumphs"]


def test_a_bad_cached_translation_is_ignored(monkeypatch):
    import db
    db.put_translations("Tamil", {"Hello there": "சत्यமே"})
    monkeypatch.setattr(tr, "ask_llm", lambda *a, **k: json.dumps({"translations": ["வணக்கம்"]}))
    assert tr.translate_texts(["Hello there"], "Tamil") == ["வணக்கம்"]


@pytest.mark.parametrize("failure", ["boom", "not json", '{"translations": ["only one"]}'])
def test_ai_failures_never_raise(monkeypatch, failure):
    def fake(*a, **k):
        if failure == "boom":
            raise RuntimeError("busy")
        return failure
    monkeypatch.setattr(tr, "ask_llm", fake)
    assert tr.translate_texts(["A title", "Another title"], "Hindi") == ["A title", "Another title"]


@pytest.fixture(scope="module")
def client():
    from main import app
    return TestClient(app)


def test_endpoints_return_translated_titles(client, monkeypatch):
    from sources import feeds
    monkeypatch.setattr(tr, "ask_llm", lambda p, *a, **k: json.dumps({"translations": [f"[हि] {t}" for t in numbered(p)]}))
    monkeypatch.setattr(feeds, "latest_all", lambda per_source=10, only=None: (
        [{"title": "Cybercrime Awareness Campaign 2026", "url": "https://x.gov.in/1", "source": "mygov", "summary": ""}],
        [{"key": "mygov", "label": "MyGov", "ok": True, "count": 1}]))
    live = client.get("/sources/live", params={"language": "Hindi"}).json()["items"][0]
    assert live["title"] == "Cybercrime Awareness Campaign 2026" and live["title_local"] == "[हि] Cybercrime Awareness Campaign 2026"
    assert client.get("/sources/live").json()["items"][0]["title_local"] == "Cybercrime Awareness Campaign 2026"   # English default

    from services.documents import ingest_pages
    doc = ingest_pages([{"page": 1, "text": "1. Purpose. This scheme helps farmers with income support every year.", "ocr": False}],
                       "PM Test Scheme", "upload")
    listed = [d for d in client.get("/documents", params={"language": "Hindi"}).json() if d["doc_id"] == doc["doc_id"]][0]
    assert listed["title_local"] == "[हि] PM Test Scheme"
    one = client.get(f"/documents/{doc['doc_id']}", params={"language": "Hindi"}).json()
    assert one["title"] == "PM Test Scheme" and one["title_local"] == "[हि] PM Test Scheme"
    client.delete(f"/documents/{doc['doc_id']}")
