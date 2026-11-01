import json
import os

import pytest
from fastapi.testclient import TestClient

from ingestion.chunker import chunk_pages
from ingestion.pdf import extract_pages
from ingestion.web import FetchError, fetch, html_to_text

PDF = os.path.join(os.path.dirname(__file__), "..", "a1990-20.pdf")


@pytest.fixture(scope="module")
def client():
    from main import app
    return TestClient(app)


def _upload(client):
    with open(PDF, "rb") as f:
        return client.post("/ingest", files={"file": ("act.pdf", f, "application/pdf")})


def test_chunker_finds_sections():
    with open(PDF, "rb") as f:
        pages = extract_pages(f.read())
    chunks = chunk_pages("t", pages)
    assert len(chunks) > 5
    assert any(c["clause"].startswith("Section") for c in chunks)


def test_health_and_languages(client):
    assert client.get("/health").json()["status"] == "ok"
    assert {"name": "Hindi", "code": "hi-IN"} in client.get("/languages").json()


def test_ingest_ask_explain_delete(client):
    r = _upload(client)
    assert r.status_code == 200, r.text
    doc_id = r.json()["doc_id"]
    assert any(d["doc_id"] == doc_id for d in client.get("/documents").json())

    a = client.post("/ask", json={"doc_id": doc_id, "question": "What are the functions of the commission?"})
    assert a.status_code == 200 and a.json()["found"] and a.json()["citations"]
    lib = client.post("/ask", json={"doc_id": "all", "question": "commission functions"})
    assert lib.status_code == 200

    e = client.post("/explain", json={"doc_id": doc_id, "language": "Hindi"})
    assert e.status_code == 200 and e.json()["explainer"]["what_is_this"]
    # second call comes from the on-disk cache
    assert client.post("/explain", json={"doc_id": doc_id, "language": "Hindi"}).json() == e.json()

    assert client.delete(f"/documents/{doc_id}").status_code == 200
    assert client.get(f"/documents/{doc_id}").status_code == 404


def test_bad_inputs(client):
    assert client.post("/ingest", files={"file": ("x.txt", b"hi", "text/plain")}).status_code == 400
    assert client.post("/ingest", files={"file": ("x.pdf", b"not a pdf", "application/pdf")}).status_code == 400
    assert client.post("/ask", json={"doc_id": "all", "question": "  "}).status_code == 400
    assert client.post("/ask", json={"doc_id": "missing1", "question": "hi"}).status_code == 404
    assert client.post("/explain", json={"doc_id": "missing1"}).status_code == 404
    assert client.post("/agent/run", json={}).status_code == 400
    assert client.post("/ingest/url", json={"url": "ftp://x"}).status_code == 400


def test_ssrf_blocked():
    for u in ("http://127.0.0.1/", "http://localhost:8000/", "http://169.254.169.254/"):
        with pytest.raises(FetchError):
            fetch(u)


def test_html_to_text_uses_main_container():
    html = """<html><head><title>T</title></head><body><nav>menu</nav>
    <div id="innner-page-main-about-us-content-right-part"><h2>Heading</h2><p>First para.</p>
    <p>Second para.</p></div><footer>foot</footer></body></html>"""
    title, text = html_to_text(html)
    assert title == "T" and "First para." in text and "menu" not in text and "foot" not in text


def _events(resp):
    return [json.loads(l[6:]) for l in resp.text.split("\n\n") if l.startswith("data: ")]


def test_agent_full_run_and_guardrails(client):
    doc_id = _upload(client).json()["doc_id"]
    r = client.post("/agent/run", json={"text": "I am a 30 year old farmer in Bihar, OBC, income 1.5 lakh",
                                        "live": False, "language": "English"})
    ev = _events(r)
    assert [e["id"] for e in ev if e["type"] == "step" and e["status"] == "done"][:3] == \
        ["profile", "plan", "retrieve"]
    res = ev[-1]
    assert res["type"] == "result" and res["results"]
    top = next(x for x in res["results"] if x["doc_id"] == doc_id)
    # the made-up criterion (bogus citation) and the uncited benefit were dropped
    assert [c["criterion"] for c in top["criteria"]] == ["Must be a woman"]
    assert [b["point"] for b in top["benefits"]] == ["Gets help"]
    # model said "eligible" but a criterion is unknown -> verdict corrected to "likely"
    assert top["verdict"] == "likely"
    assert res["checklist"][0]["item"] == "Aadhaar card"


def test_agent_asks_for_missing_info(client):
    r = client.post("/agent/run", json={"profile": {"age": 20}, "live": False})
    ev = _events(r)
    assert ev[-1]["type"] == "needs_info"
    assert set(ev[-1]["missing"]) == {"state", "occupation", "annual_income_inr", "category"}
    forced = _events(client.post("/agent/run", json={"profile": {"age": 20}, "live": False, "force": True}))
    assert forced[-1]["type"] == "result"


def test_agent_reports_missing_api_key(client, monkeypatch):
    import agent.graph, llm.client

    def boom(*a, **k):
        raise llm.client.LLMNotConfigured("GEMINI_API_KEY is not set.")
    monkeypatch.setattr(agent.graph, "ask_llm", boom)
    ev = _events(client.post("/agent/run", json={"text": "farmer from Bihar", "live": False}))
    assert ev[-1]["type"] == "error" and ev[-1]["code"] == "not_configured"


RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Scheme &amp; benefits for farmers</title><link>https://example.gov.in/a</link>
<description>&lt;p&gt;Income support&lt;/p&gt;</description></item>
<item><title>No link here</title></item></channel></rss>"""


def test_feeds_one_blocked_source_does_not_break_others(monkeypatch):
    import httpx
    from sources import feeds
    feeds._cache.clear()

    def fake_get(url, **kw):
        req = httpx.Request("GET", url)
        if "pib.gov.in" in url:
            return httpx.Response(403, request=req)
        if "rbi.org.in" in url:
            raise httpx.ConnectError("down", request=req)
        return httpx.Response(200, content=RSS, request=req)
    monkeypatch.setattr(feeds.httpx, "get", fake_get)

    items, status = feeds.latest_all(per_source=5)
    by = {s["key"]: s for s in status}
    assert by["pib"]["ok"] is False and "does not allow automated access" in by["pib"]["error"]
    assert by["rbi"]["ok"] is False
    assert by["mygov"]["ok"] and by["digitalindia"]["ok"]
    assert items[0]["title"] == "Scheme & benefits for farmers" and items[0]["summary"] == "Income support"
    assert len(items) == 2 and all(i["source"] in ("mygov", "digitalindia") for i in items)


def test_live_sources_endpoint(client, monkeypatch):
    from sources import feeds
    monkeypatch.setattr(feeds, "latest_all", lambda per_source=10, only=None: (
        [{"title": "T", "url": "https://x.gov.in/1", "source": "mygov", "summary": ""}],
        [{"key": "mygov", "label": "MyGov", "ok": True, "count": 1},
         {"key": "pib", "label": "PIB", "ok": False, "error": "blocked"}]))
    r = client.get("/sources/live").json()
    assert r["items"][0]["doc_id"] is None and r["sources"][1]["ok"] is False
    assert client.get("/sources/live?source=nope").status_code == 400


def test_agent_fetches_official_catalog_document(client, monkeypatch):
    """A person with no matching document in the library: the agent fetches one from the catalog."""
    import agent.graph as g
    import conftest
    from ingestion.pdf import extract_pages

    with open(PDF, "rb") as f:
        pages = extract_pages(f.read())
    monkeypatch.setattr(g, "load_url", lambda url: ("Doc", pages, url))
    orig = conftest.fake_llm

    def llm(prompt, rounds=3, json_mode=False):
        if "official government scheme documents" in prompt:
            return json.dumps({"picks": [0, 99, "x"]})        # one valid pick, two junk ones
        return orig(prompt, rounds, json_mode)
    monkeypatch.setattr(g, "ask_llm", llm)

    ev = _events(client.post("/agent/run", json={"text": "farmer in Bihar", "live": True}))
    cat = next(e for e in ev if e.get("id") == "catalog" and e["status"] == "done")
    assert len(cat["imported"]) == 1 and "PM-KISAN" in cat["detail"]
    items = client.get("/sources/catalog").json()
    assert items[0]["doc_id"] == cat["imported"][0] and items[1]["doc_id"] is None


def test_tts_endpoint_caches_and_validates(client, monkeypatch):
    import llm.tts as tts
    calls = []

    class Part:
        inline_data = type("D", (), {"data": b"\x00\x01" * 2400})()

    class Resp:
        candidates = [type("C", (), {"content": type("K", (), {"parts": [Part()]})()})()]

    class Models:
        def generate_content(self, model, contents, config):
            calls.append(model)
            return Resp()
    monkeypatch.setattr(tts.llm, "client", type("Cl", (), {"models": Models()})())

    r = client.post("/tts", json={"text": "नमस्ते यह एक परीक्षण है", "language": "Hindi"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF"
    n = len(calls)
    assert client.post("/tts", json={"text": "नमस्ते यह एक परीक्षण है", "language": "Hindi"}).content == r.content
    assert len(calls) == n                                  # second time came from the disk cache
    assert client.post("/tts", json={"text": "  ", "language": "Hindi"}).status_code == 400
    assert client.post("/tts", json={"text": "x" * 2000, "language": "Hindi"}).status_code == 422


def test_tts_falls_back_to_next_model_when_one_returns_nothing(monkeypatch):
    import llm.tts as tts

    class Empty:
        candidates = [type("C", (), {"content": None})()]

    class Good:
        class P:
            inline_data = type("D", (), {"data": b"\x00\x01" * 100})()
        candidates = [type("C", (), {"content": type("K", (), {"parts": [P()]})()})()]
    tried = []

    class Models:
        def generate_content(self, model, contents, config):
            tried.append(model)
            return Empty() if len(tried) == 1 else Good()
    monkeypatch.setattr(tts.llm, "client", type("Cl", (), {"models": Models()})())
    wav = tts.synthesize("unique text for fallback test 123", "English")
    assert wav[:4] == b"RIFF" and len(tried) == 2 and tried[0] != tried[1]


def test_llm_skips_models_that_are_gone_or_out_of_quota(monkeypatch):
    import llm.client as lc
    lc._cooldown.clear()
    monkeypatch.setattr(lc, "MODEL", "dead-model")
    monkeypatch.setattr(lc, "FALLBACKS", ["good-model"])
    seen = []

    class Resp:
        text = "hi"

    class Models:
        def generate_content(self, model, contents, config):
            seen.append(model)
            if model == "dead-model":
                raise RuntimeError("404 NOT_FOUND no longer available to new users")
            return Resp()
    monkeypatch.setattr(lc, "client", type("Cl", (), {"models": Models()})())
    assert lc.ask_llm("a") == "hi" and lc.ask_llm("b") == "hi"
    assert seen == ["dead-model", "good-model", "good-model"]       # dead model tried only once
    lc._cooldown.clear()


def test_explainer_business_mode_and_tolerant_points(client):
    doc = _upload(client).json()["doc_id"]
    import rag.explain as ex

    def llm(prompt, rounds=3, json_mode=False):
        assert "small businesses" in prompt and "obligations" in prompt
        cid = [l for l in prompt.split("SOURCES:")[1].split("\n") if l.startswith("[")][0][1:].split("]")[0]
        return json.dumps({"what_is_this": ["plain string point"],            # wrong shape on purpose
                           "obligations": [{"text": "File a return", "sources": [cid]}],
                           "penalties": [{"point": "Fine applies", "cited_ids": [cid]}]})
    old = ex.ask_llm
    ex.ask_llm = llm
    try:
        r = client.post("/explain", json={"doc_id": doc, "language": "English", "audience": "business"}).json()
    finally:
        ex.ask_llm = old
    e = r["explainer"]
    assert r["audience"] == "business" and "obligations" in e and "penalties" in e
    assert [p["point"] for p in e["obligations"]] == ["File a return"]      # tolerant shape accepted + cited
    assert e["what_is_this"] == []                                            # uncited string dropped
    citizen = client.post("/explain", json={"doc_id": doc, "language": "English"}).json()
    assert citizen["audience"] == "citizen" and "obligations" not in citizen["explainer"]


HINDI = """ग्रामीण महिला स्वरोजगार योजना – अधिसूचना
1. उद्देश्य. यह योजना ग्रामीण क्षेत्रों की महिलाओं को छोटे व्यवसाय शुरू करने के लिए आर्थिक सहायता देने के लिए बनाई गई है।
2. पात्रता. आवेदक की आयु 18 वर्ष से 55 वर्ष के बीच होनी चाहिए। परिवार की वार्षिक आय 3 लाख रुपये से अधिक नहीं होनी चाहिए।
3. सहायता राशि. पात्र महिलाओं को व्यवसाय शुरू करने के लिए 50,000 रुपये तक का अनुदान एक बार दिया जाएगा।"""


def test_chunker_splits_hindi_sections_but_not_english_list_items():
    chunks = chunk_pages("h", [{"page": 1, "text": HINDI, "ocr": False}])
    assert [c["clause"] for c in chunks if c["clause"].startswith("Section")] == ["Section 1", "Section 2", "Section 3"]
    items = "\n".join(f"{i}. the item number {i} is written in lower case here" for i in range(1, 6))
    labels = {c["clause"] for c in chunk_pages("e", [{"page": 1, "text": items, "ocr": False}])}
    assert not any(label.startswith("Section") for label in labels)


def test_pdf_text_has_no_invisible_spaces():
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Hello" + chr(160) + "world" + chr(0x200b) + "again", fontsize=12)
    pages = extract_pages(doc.tobytes())
    assert chr(160) not in pages[0]["text"] and chr(0x200b) not in pages[0]["text"]
    assert "Hello world" in pages[0]["text"]


def test_explainer_handles_a_very_large_document(client):
    """Documents over ~9000 words use topic-based selection instead of the whole text."""
    words = " ".join(f"clause{i} applicants shall provide documents" for i in range(1800))   # ~10,800 words
    pages = [{"page": p + 1, "text": f"{p + 1}. Provision. " + words[p * 3000:(p + 1) * 3000], "ocr": False} for p in range(12)]
    from services.documents import ingest_pages
    doc = ingest_pages(pages, "Big Act", "upload")
    r = client.post("/explain", json={"doc_id": doc["doc_id"], "language": "English"})
    assert r.status_code == 200 and r.json().get("error") is None
    client.delete(f"/documents/{doc['doc_id']}")


def test_a_condition_that_needs_missing_information_can_never_make_someone_not_eligible(client, monkeypatch):
    """Regression: the AI once marked 'income tax payers are excluded' as UNMET while admitting it did not know
    whether the farmer pays tax, and the person was told 'not eligible'."""
    import agent.graph as g
    import conftest
    doc_id = _upload(client).json()["doc_id"]
    orig = conftest.fake_llm

    def llm(prompt, rounds=3, json_mode=False):
        if "You check whether ONE citizen" in prompt:
            cid = conftest.first_id(prompt)
            return json.dumps({
                "is_scheme": True, "name": "Test Scheme", "summary": "A test.", "verdict": "not_eligible",
                "criteria": [
                    {"criterion": "Does not pay income tax", "status": "unmet", "needs_missing_info": True,
                     "reason": "not stated", "cited_ids": [cid]},
                    {"criterion": "Is at least 18 years old", "status": "met", "needs_missing_info": "false",
                     "reason": "age 30", "cited_ids": [cid]}],
                "benefits": [{"point": "Gets help", "cited_ids": [cid]}]})
        return orig(prompt, rounds, json_mode)
    monkeypatch.setattr(g, "ask_llm", llm)
    res = _events(client.post("/agent/run", json={"text": "farmer in Bihar", "live": False, "doc_ids": [doc_id]}))[-1]
    top = next(x for x in res["results"] if x["doc_id"] == doc_id)
    assert [c["status"] for c in top["criteria"]] == ["unknown", "met"]      # the guess became "unknown"
    assert top["verdict"] == "likely"                                         # not "not_eligible"
    # a genuine violation (no missing information) must still count
    def violated(prompt, rounds=3, json_mode=False):
        return llm(prompt, rounds, json_mode).replace('"needs_missing_info": true', '"needs_missing_info": false')
    monkeypatch.setattr(g, "ask_llm", violated)
    res = _events(client.post("/agent/run", json={"text": "farmer in Bihar", "live": False, "doc_ids": [doc_id]}))[-1]
    assert next(x for x in res["results"] if x["doc_id"] == doc_id)["verdict"] == "not_eligible"


def test_ai_client_turns_thinking_off_learns_what_each_model_accepts(monkeypatch):
    """The AI's default 'thinking' made calls take 10-40 s. It is switched off (or LOW for models that refuse 'off'),
    and what a model accepts is remembered so later calls do not retry."""
    import llm.client as lc
    lc._cooldown.clear()
    lc._think_mode.clear()
    monkeypatch.setattr(lc, "THINKING", "fast")
    monkeypatch.setattr(lc, "MODEL", "picky-model")
    monkeypatch.setattr(lc, "FALLBACKS", [])
    seen = []

    class Resp:
        text = "hi"

    class Models:
        def generate_content(self, model, contents, config):
            tc = config.thinking_config
            seen.append("off" if tc is not None and tc.thinking_budget == 0 else "low" if tc is not None else "default")
            if tc is not None and tc.thinking_budget == 0:
                raise RuntimeError("400 INVALID_ARGUMENT: Request contains an invalid argument.")
            return Resp()
    monkeypatch.setattr(lc, "client", type("Cl", (), {"models": Models()})())
    assert lc.ask_llm("a") == "hi" and seen == ["off", "low"]          # 'off' refused, 'low' accepted
    assert lc._think_mode == {"picky-model": "low"}
    seen.clear()
    assert lc.ask_llm("b") == "hi" and seen == ["low"]                  # remembered: no more retrying 'off'
    lc._think_mode.clear()
    monkeypatch.setattr(lc, "THINKING", "default")                      # the escape hatch keeps the models' own thinking
    seen.clear()
    lc.ask_llm("c")
    assert seen == ["default"]
    lc._cooldown.clear()
    lc._think_mode.clear()
