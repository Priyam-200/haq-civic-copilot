import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useLang, useT } from "../hooks";
import { Citations, ListenButton, MicButton, Spinner } from "./ui";

/** Question and answer over one document (docId) or the whole library (docId = "all"). */
export default function Chat({ docId, suggestions = [] }) {
  const t = useT();
  const { language } = useLang();
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef(null);

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, busy]);

  async function send(q) {
    const question = (q ?? text).trim();
    if (!question || busy) return;
    setText("");
    setMessages((m) => [...m, { role: "user", text: question }]);
    setBusy(true);
    try {
      const r = await api.ask(docId, question, language);
      setMessages((m) => [...m, { role: "bot", ...r }]);
    } catch (e) {
      setMessages((m) => [...m, { role: "bot", found: false, error: true, answer: e.message, citations: [] }]);
    } finally {
      setBusy(false);
    }
  }

  // The server's own fixed sentences ("Not found in the document", "AI busy") are shown in the chosen language
  const shown = (m) => (m.found ? m.answer : m.error === "ai_busy" ? t("err.busy") : m.error ? m.answer : t("chat.notFound"));

  return (
    <div className="chat">
      <div className="chat-log" aria-live="polite">
        {!messages.length && (
          <div className="empty">
            <p>{docId === "all" ? t("chat.emptyAll") : t("chat.emptyDoc")}</p>
            <div className="chips">
              {suggestions.map((s) => (
                <button key={s} className="chip" onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="msg msg-user">
              {m.text}
            </div>
          ) : (
            <div key={i} className={`msg msg-bot ${m.found ? "" : "msg-muted"}`}>
              <div className="answer">{shown(m)}</div>
              {m.found && (
                <>
                  <Citations items={m.citations} />
                  <ListenButton id={`chat-${i}`} text={m.answer} />
                </>
              )}
              {!m.found && !m.error && <p className="hint">{t("chat.hint")}</p>}
            </div>
          )
        )}
        {busy && (
          <div className="msg msg-bot">
            <Spinner label={t("chat.reading")} />
          </div>
        )}
        <div ref={end} />
      </div>
      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={t("chat.placeholder")}
          aria-label={t("chat.aria")}
          maxLength={1000}
        />
        <MicButton value={text} onChange={setText} />
        <button className="btn btn-primary" disabled={busy || !text.trim()}>
          {t("chat.ask")}
        </button>
      </form>
    </div>
  );
}
