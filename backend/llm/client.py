import os, time
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=_KEY) if _KEY else None
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
FALLBACKS = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "").split(",") if m.strip()]
EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")


class LLMBusyError(Exception):
    pass


class LLMNotConfigured(RuntimeError):
    pass


def configured() -> bool:
    return client is not None


def _need_client():
    if client is None:
        raise LLMNotConfigured("GEMINI_API_KEY is not set. Add it to backend/.env and restart.")


_cooldown = {}   # model -> time until which we skip it (it is gone, or its quota is used up)


def _cool(model: str, error: str):
    """Remember a model that cannot answer, so the next requests do not waste time on it."""
    e = error.lower()
    if "404" in error or "no longer available" in e or "not found" in e:
        secs = 3600                               # model is gone for this key
    elif "429" in error or "resource_exhausted" in e:
        secs = 1800 if ("perday" in e or "per day" in e) else 60
    else:
        return                                    # 503 etc. are temporary: just retry normally
    _cooldown[model] = time.time() + secs


def ask_llm(prompt: str, rounds: int = 3, json_mode: bool = False) -> str:
    _need_client()
    config = types.GenerateContentConfig(
        response_mime_type="application/json" if json_mode else "text/plain",
        temperature=0,
    )
    models = [MODEL] + FALLBACKS
    last = None
    for r in range(rounds):                 # go through all models, then wait
        now = time.time()
        usable = [m for m in models if _cooldown.get(m, 0) <= now] or models   # never end up with none
        for model in usable:
            try:
                resp = client.models.generate_content(model=model, contents=prompt, config=config)
                if not resp.text:
                    raise RuntimeError("Model returned empty text")
                return resp.text
            except Exception as e:
                last = e
                _cool(model, str(e))
                print(f"[LLM] {model} failed (round {r + 1}): {str(e)[:150]}")
        time.sleep(3 * (r + 1))             # wait 3s, 6s, 9s
    raise LLMBusyError(f"All models failed: {last}")


def embed(texts: list[str], retries: int = 4) -> list[list[float]]:
    _need_client()
    out = []
    for i in range(0, len(texts), 50):
        batch = texts[i:i + 50]
        for attempt in range(retries):
            try:
                r = client.models.embed_content(model=EMBED_MODEL, contents=batch)
                out += [e.values for e in r.embeddings]
                break
            except Exception as e:
                if attempt == retries - 1:
                    raise RuntimeError(f"Embedding failed: {e}")
                time.sleep(2 ** attempt)
    return out
