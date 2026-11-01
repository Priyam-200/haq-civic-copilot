"""Speech to text: the endpoint, its guards, that silence never reaches the AI, and that it stays fast."""
import io
import time
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

import llm.stt as stt


def wav(samples, rate=16000):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return out.getvalue()


def tone(seconds, level=0.3, rate=16000):
    t = np.arange(int(rate * seconds)) / rate
    return (level * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


@pytest.fixture(scope="module")
def client():
    from main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    from llm.client import _cooldown
    _cooldown.clear()
    monkeypatch.setattr(stt, "STT_MODELS", ["fast-a", "fast-b", "fast-c"])
    yield
    _cooldown.clear()


def test_stt_returns_the_spoken_words_and_asks_for_the_right_language(client, monkeypatch):
    seen = {}

    def fake(model, audio, prompt):
        seen.update(model=model, prompt=prompt, audio=len(audio))
        return '  मैं बिहार का किसान हूं  '
    monkeypatch.setattr(stt, "_ask", fake)
    r = client.post("/stt", files={"file": ("v.wav", wav(tone(2)), "audio/wav")}, data={"language": "Hindi"})
    assert r.status_code == 200 and r.json() == {"text": "मैं बिहार का किसान हूं"}
    assert "Hindi" in seen["prompt"] and seen["audio"] > 1000 and seen["model"] == "fast-a"   # quickest model first


def test_silence_is_never_sent_to_the_ai(client, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the AI must not be called for a silent recording")
    monkeypatch.setattr(stt, "_ask", boom)
    quiet = (np.random.randn(16000 * 3) * 0.0005).astype(np.float32)
    r = client.post("/stt", files={"file": ("v.wav", wav(quiet), "audio/wav")}, data={"language": "English"})
    assert r.status_code == 200 and r.json() == {"text": ""}


def test_the_model_saying_there_is_no_speech_gives_empty_text(client, monkeypatch):
    monkeypatch.setattr(stt, "_ask", lambda m, a, p: "[NO_SPEECH]")
    r = client.post("/stt", files={"file": ("v.wav", wav(tone(1)), "audio/wav")}, data={"language": "English"})
    assert r.json() == {"text": ""}


def test_stt_rejects_bad_input(client):
    assert client.post("/stt", files={"file": ("v.wav", b"not audio", "audio/wav")}, data={"language": "Hindi"}).status_code == 400
    too_long = wav(tone(100))                                    # over the 90 second limit
    assert client.post("/stt", files={"file": ("v.wav", too_long, "audio/wav")}, data={"language": "Hindi"}).status_code == 400
    assert client.post("/stt", data={"language": "Hindi"}).status_code == 422          # no file at all


def test_a_failing_model_falls_through_to_the_next_one(client, monkeypatch):
    tried = []

    def fake(model, audio, prompt):
        tried.append(model)
        if model == "fast-a":
            raise RuntimeError("429 RESOURCE_EXHAUSTED per day")
        return "hello from " + model
    monkeypatch.setattr(stt, "_ask", fake)
    r = client.post("/stt", files={"file": ("v.wav", wav(tone(1)), "audio/wav")}, data={"language": "English"})
    assert r.json() == {"text": "hello from fast-b"} and tried[:2] == ["fast-a", "fast-b"]
    # the exhausted model is remembered, so the next request does not waste time on it
    tried.clear()
    client.post("/stt", files={"file": ("v.wav", wav(tone(1)), "audio/wav")}, data={"language": "English"})
    assert tried == ["fast-b"]


def test_a_slow_model_is_hedged_so_the_answer_is_not_delayed(monkeypatch):
    monkeypatch.setattr(stt, "HEDGE_AFTER", 0.1)

    def fake(model, audio, prompt):
        if model == "fast-a":
            time.sleep(2.0)                                       # a model having a bad day
            return "slow answer"
        return "quick answer"
    monkeypatch.setattr(stt, "_ask", fake)
    t = time.time()
    assert stt.transcribe(wav(tone(1)), "English") == "quick answer"
    assert time.time() - t < 1.0                                  # did NOT wait for the slow model


def test_all_models_failing_is_reported_as_busy(client, monkeypatch):
    def fake(model, audio, prompt):
        raise RuntimeError("503 UNAVAILABLE")
    monkeypatch.setattr(stt, "_ask", fake)
    r = client.post("/stt", files={"file": ("v.wav", wav(tone(1)), "audio/wav")}, data={"language": "English"})
    assert r.status_code == 503
