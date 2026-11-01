import { useState } from "react";
import { api } from "../api";
import { NATIVE_NAME } from "../i18n";
import { splitForSpeech, useDictation, useLang, useSpeech, useT } from "../hooks";

export function Spinner({ label }) {
  return (
    <span className="spinner-wrap" role="status">
      <span className="spinner" aria-hidden="true" />
      {label && <span>{label}</span>}
    </span>
  );
}

export function Alert({ kind = "error", children, onClose }) {
  const t = useT();
  return (
    <div className={`alert alert-${kind}`} role={kind === "error" ? "alert" : "status"}>
      <div>{children}</div>
      {onClose && (
        <button className="link" onClick={onClose} aria-label={t("alert.dismiss")}>
          ✕
        </button>
      )}
    </div>
  );
}

/** Sources behind a statement. Shows where in the document the words came from. */
export function Citations({ items }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  if (!items || !items.length) return null;
  return (
    <div className="cites">
      <button className="link" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? t("ui.hide", { n: items.length }) : t("ui.show", { n: items.length })}
      </button>
      {open && (
        <ul>
          {items.map((c) => (
            <li key={c.chunk_id}>
              <div className="cite-head">
                {c.doc_title ? `${c.doc_title} · ` : ""}
                {t("ui.page", { page: c.page })} · {c.clause}
              </div>
              <blockquote>{c.text}</blockquote>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Read-aloud button. `id` identifies what is being read so only one thing plays at a time. */
export function ListenButton({ id, text, label }) {
  const t = useT();
  const { speakingId, loadingId, speak, stop, error } = useSpeech();
  const on = speakingId === id;
  const loading = loadingId === id;
  return (
    <span className="mic">
      <button
        className="btn btn-ghost btn-sm"
        onClick={() => (on || loading ? stop() : speak(id, text))}
        aria-pressed={on}
        disabled={!text}
      >
        {loading ? t("ui.preparingVoice") : on ? `■ ${t("ui.stop")}` : label || t("ui.listen")}
      </button>
      {error && <span className="mic-error">{error}</span>}
    </span>
  );
}

/** Microphone button: dictates into the field whose text is `value` (updates it via `onChange`). */
export function MicButton({ value, onChange }) {
  const t = useT();
  const { supported, state, listening, busy, error, notice, start, stop } = useDictation(value, onChange);
  if (!supported) return null;
  const label = busy
    ? t("mic.transcribing")
    : state === "recording"
    ? `● ${t("mic.recording")}`
    : listening
    ? `● ${t("ui.listening")}`
    : t("ui.speak");
  return (
    <span className="mic">
      <button
        type="button"
        className={`btn btn-ghost btn-sm ${listening ? "listening" : ""}`}
        onClick={listening ? stop : start}
        aria-pressed={listening}
        disabled={busy}
        title={t("ui.speakTitle")}
      >
        {label}
      </button>
      {notice && <span className="mic-note">{notice}</span>}
      {error && <span className="mic-error">{error}</span>}
    </span>
  );
}

export function LanguageSelect({ value, onChange, languages }) {
  const t = useT();
  return (
    <label className="lang-select">
      <span aria-hidden="true">{t("util.language")}:</span>
      <span className="sr-only">{t("util.language")}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {languages.map((l) => (
          <option key={l.name} value={l.name}>
            {NATIVE_NAME[l.name] || l.name}
            {l.name !== "English" && NATIVE_NAME[l.name] ? ` (${l.name})` : ""}
          </option>
        ))}
      </select>
    </label>
  );
}

export const VERDICT_CLASS = { eligible: "good", likely: "likely", unclear: "unclear", not_eligible: "bad" };

/** Share as a WhatsApp message, or copy the text. */
export function ShareButton({ title, text, url }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  const body = [title, text, url ? `${t("ui.source")}: ${url}` : "", t("ui.explainedBy")].filter(Boolean).join("\n\n").slice(0, 1800);
  async function copy() {
    try {
      await navigator.clipboard.writeText(body);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      window.prompt(t("ui.copyPrompt"), body);
    }
  }
  return (
    <span className="row no-print">
      <a className="btn btn-ghost btn-sm" href={"https://wa.me/?text=" + encodeURIComponent(body)} target="_blank" rel="noreferrer">
        {t("ui.whatsapp")}
      </a>
      <button className="btn btn-ghost btn-sm" onClick={copy}>
        {copied ? `✓ ${t("ui.copied")}` : t("ui.copy")}
      </button>
    </span>
  );
}

/** Join several WAV files (same format, 44-byte header) into one. */
async function joinWav(blobs) {
  const bufs = await Promise.all(blobs.map((b) => b.arrayBuffer()));
  const pcm = bufs.map((b) => new Uint8Array(b, 44));
  const total = pcm.reduce((n, p) => n + p.length, 0);
  const out = new Uint8Array(44 + total);
  out.set(new Uint8Array(bufs[0], 0, 44), 0);
  const view = new DataView(out.buffer);
  view.setUint32(4, 36 + total, true);
  view.setUint32(40, total, true);
  let at = 44;
  for (const p of pcm) {
    out.set(p, at);
    at += p.length;
  }
  return new Blob([out], { type: "audio/wav" });
}

/** Saves the spoken explainer as an audio file, so it can be sent as a voice note. */
export function DownloadAudioButton({ text, name }) {
  const t = useT();
  const { language } = useLang();
  const [state, setState] = useState({ busy: false, label: "", error: "" });
  async function run() {
    const parts = splitForSpeech(text);
    if (!parts.length) return;
    try {
      const blobs = [];
      for (let i = 0; i < parts.length; i++) {
        setState({ busy: true, label: t("ui.preparing", { i: i + 1, n: parts.length }), error: "" });
        blobs.push(await api.tts(parts[i], language));
      }
      const url = URL.createObjectURL(await joinWav(blobs));
      const a = document.createElement("a");
      a.href = url;
      a.download = `${(name || "explainer").replace(/[^\w\- ]+/g, "").trim().slice(0, 50) || "explainer"} - ${language}.wav`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
      setState({ busy: false, label: "", error: "" });
    } catch (e) {
      setState({ busy: false, label: "", error: e.message || t("ui.audioFail") });
    }
  }
  return (
    <span className="mic">
      <button className="btn btn-ghost btn-sm no-print" onClick={run} disabled={state.busy || !text} title={t("ui.downloadTitle")}>
        {state.busy ? state.label : t("ui.download")}
      </button>
      {state.error && <span className="mic-error">{state.error}</span>}
    </span>
  );
}
