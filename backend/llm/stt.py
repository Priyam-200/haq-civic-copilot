"""Speech to text with Gemini, so voice input works in every browser and every supported language
(the browser's own recogniser depends on a Google web service that often fails or lacks Indian languages).

Speed matters here: a person is waiting for their words to appear. Measured on real clips, the models'
default "thinking" made a 6-second recording take 15-40 s; with thinking switched off the same call takes
about 2.5 s. So this module has its own fast path: thinking off, the quickest models first, and a hedge -
if a model has not answered within HEDGE_AFTER seconds the next one is started in parallel and the first
good answer wins."""
import concurrent.futures as cf
import io
import os
import re
import time
import wave

import numpy as np
from google.genai import types

from llm import client as llm
from llm.client import LLMBusyError, _cool, _cooldown

MAX_SECONDS = 90
MAX_BYTES = 6 * 1024 * 1024
QUIET = 0.008          # a recording whose loudest 50 ms never passes this has no speech in it
STT_MODELS = [m.strip() for m in os.getenv(
    "GEMINI_STT_MODELS", "gemini-3.6-flash,gemini-3.5-flash,gemini-3-flash-preview,gemini-3.1-flash-lite"
).split(",") if m.strip()]
HEDGE_AFTER = float(os.getenv("STT_HEDGE_AFTER", "4"))     # seconds before the next model starts in parallel
DEADLINE = float(os.getenv("STT_DEADLINE", "25"))
NO_SPEECH = "[NO_SPEECH]"


class AudioError(ValueError):
    pass


def _pcm(wav: bytes):
    try:
        with wave.open(io.BytesIO(wav)) as w:
            rate, width, channels, n = w.getframerate(), w.getsampwidth(), w.getnchannels(), w.getnframes()
            frames = w.readframes(n)
    except (wave.Error, EOFError):
        raise AudioError("Please send a WAV recording.")
    if width != 2:
        raise AudioError("Please send 16-bit audio.")
    x = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    return x, rate


def has_speech(x: np.ndarray, rate: int) -> bool:
    win = max(1, int(rate * 0.05))
    k = len(x) // win
    if k == 0:
        return False
    return float(np.sqrt((x[: k * win].reshape(k, win) ** 2).mean(axis=1)).max()) > QUIET


def _ask(model: str, wav: bytes, prompt: str) -> str:
    """One model, thinking switched off. Falls back to default settings if a model rejects that option."""
    llm._need_client()
    audio = types.Part.from_bytes(data=wav, mime_type="audio/wav")
    http = types.HttpOptions(timeout=int(DEADLINE * 1000))
    try:
        cfg = types.GenerateContentConfig(temperature=0, http_options=http,
                                          thinking_config=types.ThinkingConfig(thinking_budget=0))
        return llm.client.models.generate_content(model=model, contents=[audio, prompt], config=cfg).text or ""
    except Exception as e:
        if "INVALID_ARGUMENT" not in str(e):
            raise
        cfg = types.GenerateContentConfig(temperature=0, http_options=http)
        return llm.client.models.generate_content(model=model, contents=[audio, prompt], config=cfg).text or ""


def _race(wav: bytes, prompt: str) -> str:
    """Quickest model first; start the next one in parallel if the first is slow; first good answer wins."""
    now = time.time()
    models = [m for m in STT_MODELS if _cooldown.get(m, 0) <= now] or list(STT_MODELS)
    ex = cf.ThreadPoolExecutor(max_workers=len(models))
    pending, nxt, last, start = {}, 0, None, time.time()

    def launch():
        nonlocal nxt
        if nxt < len(models):
            pending[ex.submit(_ask, models[nxt], wav, prompt)] = models[nxt]
            nxt += 1

    try:
        launch()
        while pending:
            left = DEADLINE - (time.time() - start)
            if left <= 0:
                break
            done, _ = cf.wait(list(pending), timeout=min(HEDGE_AFTER, left), return_when=cf.FIRST_COMPLETED)
            if not done:
                launch()                                   # slow: hedge with the next model
                continue
            for f in done:
                model = pending.pop(f)
                try:
                    return f.result()
                except Exception as e:                     # busy, gone, out of quota...
                    last = e
                    _cool(model, str(e))
                    print(f"[STT] {model} failed: {str(e)[:120]}")
                    launch()
        raise LLMBusyError(f"Speech recognition failed: {last}")
    finally:
        ex.shutdown(wait=False, cancel_futures=True)       # never make the user wait for a slow loser


def transcribe(wav: bytes, language: str = "English") -> str:
    """The words spoken in a WAV recording, or "" if there is no speech."""
    if len(wav) > MAX_BYTES:
        raise AudioError("The recording is too long.")
    x, rate = _pcm(wav)
    if len(x) / rate > MAX_SECONDS:
        raise AudioError("The recording is too long.")
    if not has_speech(x, rate):                     # never ask the model about silence: it may invent words
        return ""
    prompt = (f"Transcribe the speech in this audio recording exactly as it was spoken. The speaker is most likely "
              f"speaking {language}. Write the words in the script normally used for the language they actually speak. "
              f"Do not translate, summarise, correct or add anything. If there is no clear human speech, return exactly "
              f"{NO_SPEECH}. Return ONLY the transcription text, nothing else.")
    text = _race(wav, prompt)
    text = re.sub(r"\s+", " ", text or "").strip().strip('"“”')
    return "" if NO_SPEECH in text else text
