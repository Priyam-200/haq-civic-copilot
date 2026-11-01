"""The interface must be fully translated in every language, and the code must only use keys that exist."""
import glob
import json
import os
import re

SRC = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "src")
I18N = os.path.join(SRC, "i18n")
LANGUAGES = ["Hindi", "Bengali", "Tamil", "Telugu", "Marathi", "Gujarati", "Kannada", "Malayalam", "Punjabi", "Odia", "Urdu"]
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def load(name):
    with open(os.path.join(I18N, f"{name}.json"), encoding="utf-8") as f:
        return json.load(f)


def test_every_language_is_complete_and_keeps_placeholders():
    en = load("English")
    for lang in LANGUAGES:
        tr = load(lang)
        assert set(tr) == set(en), f"{lang}: missing {sorted(set(en) - set(tr))[:5]} extra {sorted(set(tr) - set(en))[:5]}"
        for key, text in en.items():
            assert tr[key].strip(), f"{lang}/{key} is empty"
            assert set(PLACEHOLDER.findall(text)) == set(PLACEHOLDER.findall(tr[key])), f"{lang}/{key}: placeholders changed"


def test_translations_are_really_translated():
    """Catches a language file that is just a copy of the English one."""
    en = load("English")
    long_keys = [k for k, v in en.items() if len(v) > 40]
    for lang in LANGUAGES:
        tr = load(lang)
        same = [k for k in long_keys if tr[k] == en[k]]
        assert len(same) < len(long_keys) * 0.1, f"{lang}: {len(same)} long texts are still English"


def test_every_key_used_in_the_code_exists():
    en = load("English")
    files = glob.glob(os.path.join(SRC, "**", "*.jsx"), recursive=True) + glob.glob(os.path.join(SRC, "*.js"))
    used = set()
    for path in files:
        code = open(path, encoding="utf-8").read()
        used |= set(re.findall(r'\bt\(\s*"([^"$]+)"\s*[,)]', code))
        for m in re.finditer(r'(?:label|ph):\s*"([\w.]+)"', code):
            used.add(m.group(1))
    missing = sorted(k for k in used if k not in en)
    assert not missing, f"keys used but not defined: {missing}"
    # keys built at run time: every possible value must exist
    for prefix, parts in {
        "verdict.": ["eligible", "likely", "unclear", "not_eligible"],
        "crit.": ["met", "unmet", "unknown"],
        "sec.": ["what_is_this", "who_is_it_for", "rights_benefits", "eligibility", "obligations", "penalties",
                 "documents_needed", "deadlines", "real_world_impact"],
        "step.": ["profile", "plan", "catalog", "live", "retrieve", "eval", "verify", "checklist"],
        "src.": ["upload", "web", "pib"],
        "catalog.": ["pm-kisan", "mudra", "pmay-urban", "pm-svanidhi", "stand-up-india", "pmuy", "pm-vishwakarma"],
    }.items():
        for p in parts:
            assert prefix + p in en, f"missing {prefix}{p}"
    for k in ("farmer", "woman", "vendor", "student"):
        assert f"ex.{k}.label" in en and f"ex.{k}.text" in en
    for n in (1, 2, 3):
        assert f"how.{n}.t" in en and f"how.{n}.d" in en
    for k in ("doc", "ask", "scheme", "live"):
        assert f"tile.{k}.t" in en and f"tile.{k}.d" in en


def test_server_codes_have_texts():
    en = load("English")
    for code in ("needs_info", "catalog_done", "catalog_none", "live_none", "live_done", "live_nomatch", "retrieve_done",
                 "eval_done", "eval_skipped", "verify_done", "checklist_done"):
        assert f"detail.{code}" in en, code
