"""Translate short texts (titles of announcements, documents and schemes) into the chosen language.

Results are saved in the database, so a text is translated by the AI only once. If the AI is busy or not
configured, the original texts are returned: a title in English is better than an error."""
import json
import re

import db
from llm.client import ask_llm

BATCH = 40

# Unicode block of each language's own script. A translation that contains letters of ANOTHER Indian script
# (an AI glitch such as Devanagari inside Tamil) is rejected and translated again.
SCRIPT = {
    "Hindi": (0x0900, 0x097F), "Marathi": (0x0900, 0x097F), "Bengali": (0x0980, 0x09FF), "Punjabi": (0x0A00, 0x0A7F),
    "Gujarati": (0x0A80, 0x0AFF), "Odia": (0x0B00, 0x0B7F), "Tamil": (0x0B80, 0x0BFF), "Telugu": (0x0C00, 0x0C7F),
    "Kannada": (0x0C80, 0x0CFF), "Malayalam": (0x0D00, 0x0D7F), "Urdu": (0x0600, 0x06FF),
}
ALLOWED_ANYWHERE = {0x0964, 0x0965}          # the danda "।" is used by several scripts


def mixed_script(text: str, language: str) -> bool:
    own = SCRIPT.get(language)
    if not own:
        return False
    for ch in text:
        o = ord(ch)
        if 0x0600 <= o <= 0x0DFF and not (own[0] <= o <= own[1]) and o not in ALLOWED_ANYWHERE:
            return True
        if language == "Urdu" and (0x0750 <= o <= 0x077F or 0xFB50 <= o <= 0xFDFF or 0xFE70 <= o <= 0xFEFF):
            continue
    return False


def _translate_batch(language: str, texts: list[str]):
    listing = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    prompt = f"""Translate each of these short titles from Indian government announcements, schemes and documents into {language}.
Rules:
- Use natural, simple wording that an ordinary citizen understands.
- Keep official scheme, programme and organisation names recognisable (for example PM-KISAN, MUDRA, MyGov, RBI,
  Mann Ki Baat). You may write them in {language} script, but they must stay recognisable.
- Keep numbers, dates and years. Do not add notes or explanations.
Return ONLY JSON: {{"translations": ["...", ...]}} with exactly {len(texts)} items in the same order.

{listing}"""
    for attempt in range(2):
        try:
            raw = ask_llm(prompt, json_mode=True)
            data = json.loads(re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip())
            out = data["translations"] if isinstance(data, dict) else data
            if isinstance(out, list) and len(out) == len(texts) and all(isinstance(x, str) and x.strip() for x in out):
                out = [x.strip() for x in out]
                bad = [i for i, x in enumerate(out) if mixed_script(x, language)]
                if bad and attempt == 0:
                    print(f"[TRANSLATE] {len(bad)} text(s) mixed scripts, translating again")
                    continue
                return [texts[i] if i in bad else x for i, x in enumerate(out)]   # still mixed: keep the original
        except Exception as e:                      # busy, not configured, bad JSON...
            print(f"[TRANSLATE] {str(e)[:120]}")
    return None


def translate_texts(texts: list[str], language: str) -> list[str]:
    """Same order as `texts`. English is returned unchanged; failures fall back to the original text."""
    if language == "English" or not texts:
        return list(texts)
    unique = list(dict.fromkeys(t for t in texts if t and t.strip()))
    known = {t: v for t, v in db.get_translations(language, unique).items() if not mixed_script(v, language)}
    missing = [t for t in unique if t not in known]
    for i in range(0, len(missing), BATCH):
        chunk = missing[i:i + BATCH]
        done = _translate_batch(language, chunk)
        if done:
            fresh = dict(zip(chunk, done))
            db.put_translations(language, fresh)
            known.update(fresh)
    return [known.get(t, t) for t in texts]
