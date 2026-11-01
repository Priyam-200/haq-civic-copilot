# name (sent to the LLM) -> BCP-47 code (used by the browser for voice in / out)
LANGUAGES = {
    "English": "en-IN", "Hindi": "hi-IN", "Bengali": "bn-IN", "Tamil": "ta-IN",
    "Telugu": "te-IN", "Marathi": "mr-IN", "Gujarati": "gu-IN", "Kannada": "kn-IN",
    "Malayalam": "ml-IN", "Punjabi": "pa-IN", "Odia": "or-IN", "Urdu": "ur-IN",
}


def normalize(language: str) -> str:
    for name in LANGUAGES:
        if name.lower() == (language or "").strip().lower():
            return name
    return "English"
