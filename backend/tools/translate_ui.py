"""Translate the interface text (frontend/src/i18n/English.json) into the other Indian languages.

    python tools/translate_ui.py                 translate every language that has no file yet
    python tools/translate_ui.py --only Hindi    one language
    python tools/translate_ui.py --force         redo all of them
    python tools/translate_ui.py --missing       only texts added to English.json since the last run

Output: frontend/src/i18n/<Language>.json. Every result is checked: all keys present, every {placeholder}
kept, nothing empty. These are machine translations - ask a native speaker to review them before launch."""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llm.client import ask_llm  # noqa: E402

I18N = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "src", "i18n")
LANGUAGES = ["Hindi", "Bengali", "Tamil", "Telugu", "Marathi", "Gujarati", "Kannada", "Malayalam", "Punjabi", "Odia", "Urdu"]
KEEP = ("Haq, हक़, PM-KISAN, MUDRA, PMAY, PMAY-Urban, PM SVANidhi, Stand-Up India, PM Ujjwala Yojana, PM Vishwakarma, "
        "MyGov, RBI, PIB, Digital India, Aadhaar, OBC, SC, ST, EWS, UT, WhatsApp, PDF, OCR, AI, GEMINI_API_KEY, "
        "NCW, http(s), MB, A−, A+")
PLACEHOLDER = re.compile(r"\{(\w+)\}")
BATCH = 120


def problems(src: dict, out: dict):
    bad = []
    for k, v in src.items():
        if k not in out or not isinstance(out[k], str) or not out[k].strip():
            bad.append(f"{k}: missing or empty")
        elif set(PLACEHOLDER.findall(v)) != set(PLACEHOLDER.findall(out[k])):
            bad.append(f"{k}: placeholders {sorted(PLACEHOLDER.findall(v))} not kept in {out[k]!r}")
    return bad


def translate_batch(language: str, batch: dict) -> dict:
    prompt = f"""Translate these user-interface texts of a government-scheme information website for Indian citizens
from English into {language}.

Rules:
- Use simple, clear, respectful everyday words that an ordinary citizen understands. Formal register (not slang).
- Keep every {{placeholder}} exactly as written (for example {{n}}, {{title}}) and put it where it fits the grammar.
- Keep these names/terms unchanged: {KEEP}.
- Keep numbers, punctuation such as … · ” “ and symbols such as ₹ and ↗ and ✓ as they are. Do not add arrows.
- Sentences like "I am a 34 year old farmer in Bihar..." are examples a person would say about themselves: translate them as
  natural first-person speech in {language}.
- Keep the same JSON keys. Translate only the values. Do not add explanations.
Return ONLY a JSON object with exactly the same keys.

{json.dumps(batch, ensure_ascii=False, indent=1)}"""
    last_problem = ""
    for attempt in range(4):
        text = prompt if not last_problem else prompt + f"\n\nYour previous answer had problems, fix them:\n{last_problem}"
        raw = ask_llm(text, json_mode=True)
        try:
            out = json.loads(re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip())
        except Exception as e:
            last_problem = f"not valid JSON ({e})"
            continue
        bad = problems(batch, out)
        if not bad:
            return {k: out[k].strip() for k in batch}
        last_problem = "\n".join(bad[:15])
        print(f"    retry {attempt + 1}: {len(bad)} problem(s), e.g. {bad[0][:100]}")
    raise RuntimeError(f"{language}: could not get a clean translation ({last_problem[:200]})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--missing", action="store_true", help="translate only the texts a language file does not have yet")
    args = ap.parse_args()

    with open(os.path.join(I18N, "English.json"), encoding="utf-8") as f:
        src = json.load(f)
    keys = list(src)
    for lang in ([args.only] if args.only else LANGUAGES):
        path = os.path.join(I18N, f"{lang}.json")
        if args.missing and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                have = json.load(f)
            todo = [k for k in keys if k not in have]
            if not todo:
                print(f"{lang}: complete")
                continue
            print(f"{lang}: translating {len(todo)} missing text(s)...")
            have.update(translate_batch(lang, {k: src[k] for k in todo}))
            ordered = {k: have[k] for k in keys}
            assert not problems(src, ordered)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(ordered, f, ensure_ascii=False, indent=1)
            print(f"{lang}: saved")
            continue
        if os.path.exists(path) and not args.force:
            print(f"{lang}: exists, skipping (use --force)")
            continue
        print(f"{lang}: translating {len(keys)} texts...")
        result = {}
        for i in range(0, len(keys), BATCH):
            result.update(translate_batch(lang, {k: src[k] for k in keys[i:i + BATCH]}))
        assert not problems(src, result)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
        print(f"{lang}: saved {len(result)} texts -> {os.path.normpath(path)}")


if __name__ == "__main__":
    main()
