"""Server-side text to speech (Gemini). Used when the visitor's device has no voice installed
for the chosen language, so the browser would read it in the wrong (English) voice."""
import hashlib
import io
import os
import time
import wave

from google.genai import types

from config import DATA_DIR
from llm import client as llm
from llm.audio import clean_wav
from llm.client import LLMBusyError

# Each model has its own free daily quota, so try them in turn.
TTS_MODELS = [m.strip() for m in os.getenv(
    "GEMINI_TTS_MODELS",
    "gemini-3.8-flash-tts,gemini-3.8-flash-lite-tts,gemini-3.1-flash-tts-preview,gemini-2.5-flash-preview-tts",
).split(",") if m.strip()]
TTS_VOICE = os.getenv("GEMINI_TTS_VOICE", "Kore")
MAX_CHARS = 1500
_DIR = os.path.join(DATA_DIR, "tts")
os.makedirs(_DIR, exist_ok=True)


def _wav(pcm: bytes, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)           # 16-bit
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def synthesize(text: str, language: str) -> bytes:
    """WAV bytes for `text`. The speech model detects the language from the text itself."""
    llm._need_client()
    text = text.strip()[:MAX_CHARS]
    key = hashlib.sha256(f"{TTS_VOICE}|{text}".encode("utf-8")).hexdigest()
    path = os.path.join(_DIR, key + ".wav")
    if os.path.isfile(path):                      # already spoken once: free
        with open(path, "rb") as f:
            saved = f.read()
        cleaned = clean_wav(saved)                # clips saved before the cleaner existed still have the noise
        if cleaned != saved:
            with open(path, "wb") as f:
                f.write(cleaned)
        return cleaned

    config = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=TTS_VOICE))),
    )
    last = None
    for rnd in range(2):
        for model in TTS_MODELS:
            try:
                r = llm.client.models.generate_content(model=model, contents=text, config=config)
                content = r.candidates[0].content if r.candidates else None
                if content is None or not content.parts or not content.parts[0].inline_data:
                    raise RuntimeError("model returned no audio")     # flaky preview models do this
                wav = clean_wav(_wav(content.parts[0].inline_data.data))     # remove the click and end-noise
                with open(path, "wb") as f:
                    f.write(wav)
                return wav
            except Exception as e:
                last = e
                print(f"[TTS] {model} failed (round {rnd + 1}): {str(e)[:120]}")
        time.sleep(2)
    raise LLMBusyError(f"Speech failed: {last}")
