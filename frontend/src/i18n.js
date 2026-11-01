/* Interface translations.
   English.json is the master. The other files (Hindi.json, Tamil.json, ...) are generated from it
   by backend/tools/translate_ui.py. A missing key falls back to English, never to a blank. */
const files = import.meta.glob("./i18n/*.json", { eager: true, import: "default" });
const DICTS = {};
for (const [path, dict] of Object.entries(files)) DICTS[path.split("/").pop().replace(".json", "")] = dict;

export const RTL_LANGUAGES = new Set(["Urdu"]);
let current = "English";

export function setUiLanguage(name, code) {
  current = DICTS[name] ? name : "English";
  if (typeof document !== "undefined") {
    document.documentElement.lang = (code || "en-IN").split("-")[0];
    document.documentElement.dir = RTL_LANGUAGES.has(current) ? "rtl" : "ltr";
  }
}

export function t(key, vars) {
  let s = (DICTS[current] && DICTS[current][key]) ?? DICTS.English[key] ?? key;
  if (vars) s = s.replace(/\{(\w+)\}/g, (_, k) => (vars[k] === undefined || vars[k] === null ? "" : String(vars[k])));
  return s;
}

/* Messages written in English by the server -> translation keys.
   Anything not listed is shown as the server sent it. */
const EXACT = {
  "Please type a question.": "err.question_empty",
  "Please upload a PDF file.": "err.pdf_only",
  "This PDF is password protected.": "err.pdf_password",
  "Could not read this PDF.": "err.pdf_unreadable",
  "No readable text found. This may be a scanned PDF.": "err.no_text",
  "Could not split this document into sections.": "err.no_sections",
  "Document not found. Please upload it again.": "err.doc_missing",
  "Document not found.": "err.doc_missing",
  "Too many requests. Please wait a minute.": "err.rate",
  "The AI service is busy right now. Please try again in a minute.": "err.busy",
  "Please give a full http(s) link.": "err.bad_link",
  "Could not find that website.": "err.no_site",
  "That address is not allowed.": "err.addr",
  "Not enough readable text on that page.": "err.page_empty",
  "Please describe your situation or fill in some details.": "err.describe",
  "Nothing to read.": "err.nothing",
  "Unknown source.": "err.unknown_source",
  "Something went wrong while checking. Please try again.": "err.internal",
  "That website's security certificate could not be verified, so it was not imported.": "err.cert",
};
const PREFIX = [
  ["File is too big", "err.file_big"],
  ["That file is too big", "err.file_big"],
  ["GEMINI_API_KEY is not set", "err.no_key"],
  ["The website returned an error", "err.site_error"],
  ["Could not download this link", "err.download"],
  ["Could not answer right now", "err.answer"],
  ["Embedding service failed", "err.embed"],
  ["Please send", "err.audio"],
  ["The recording", "err.audio"],
];

export function localizeServer(message) {
  if (!message) return message;
  if (EXACT[message]) return t(EXACT[message]);
  const hit = PREFIX.find(([p]) => message.startsWith(p));
  return hit ? t(hit[1]) : message;
}

/* Error codes sent by the agent stream */
export const AGENT_ERROR = { not_configured: "err.no_key", ai_busy: "err.busy", internal: "err.internal" };

/* Each language written in its own script, so people can find theirs without reading English. */
export const NATIVE_NAME = {
  English: "English", Hindi: "हिन्दी", Bengali: "বাংলা", Tamil: "தமிழ்", Telugu: "తెలుగు", Marathi: "मराठी",
  Gujarati: "ગુજરાતી", Kannada: "ಕನ್ನಡ", Malayalam: "മലയാളം", Punjabi: "ਪੰਜਾਬੀ", Odia: "ଓଡ଼ିଆ", Urdu: "اردو",
};
