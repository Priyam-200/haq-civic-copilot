import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { NATIVE_NAME } from "../i18n";
import { useLang, useT } from "../hooks";
import Chat from "../components/Chat";
import { Alert, Citations, DownloadAudioButton, ListenButton, ShareButton, Spinner } from "../components/ui";

const SECTIONS = [
  "what_is_this",
  "who_is_it_for",
  "rights_benefits",
  "eligibility",
  "obligations",
  "penalties",
  "documents_needed",
  "deadlines",
  "real_world_impact",
];

export default function Document({ id }) {
  const t = useT();
  const { language } = useLang();
  const [doc, setDoc] = useState(null);
  const [tab, setTab] = useState("explain");
  const [audience, setAudience] = useState("citizen");
  const [error, setError] = useState("");

  const shownId = useRef(null);
  useEffect(() => {
    if (shownId.current !== id) {      // a different document: show the spinner. A language change just refreshes the title.
      setDoc(null);
      setError("");
    }
    api
      .document(id, language)
      .then((d) => {
        shownId.current = id;
        setDoc(d);
      })
      .catch((e) => setError(e.message));
  }, [id, language]);

  if (error)
    return (
      <div className="stack">
        <Alert>{error}</Alert>
        <a href="#/library" className="btn btn-ghost">
          {t("doc.back")}
        </a>
      </div>
    );
  if (!doc) return <Spinner label={t("doc.opening")} />;

  return (
    <div className="stack">
      <a href="#/library" className="back">
        {t("doc.library")}
      </a>
      <header>
        <h1>{doc.title_local || doc.title}</h1>
        <div className="meta">
          <span>{t("lib.pages", { n: doc.page_count })}</span>
          {doc.source_url && (
            <a href={doc.source_url} target="_blank" rel="noreferrer">
              {t("doc.original")} ↗
            </a>
          )}
        </div>
      </header>

      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "explain"} className={tab === "explain" ? "on" : ""} onClick={() => setTab("explain")}>
          {t("tab.explain")}
        </button>
        <button role="tab" aria-selected={tab === "ask"} className={tab === "ask" ? "on" : ""} onClick={() => setTab("ask")}>
          {t("tab.ask")}
        </button>
      </div>

      {tab === "explain" ? (
        <>
          <div className="seg" role="group" aria-label={t("aud.group")}>
            <span className="muted small">{t("aud.label")}</span>
            <button className={audience === "citizen" ? "on" : ""} aria-pressed={audience === "citizen"} onClick={() => setAudience("citizen")}>
              {t("aud.citizen")}
            </button>
            <button className={audience === "business" ? "on" : ""} aria-pressed={audience === "business"} onClick={() => setAudience("business")}>
              {t("aud.business")}
            </button>
          </div>
          <Explainer id={id} language={language} audience={audience} title={doc.title_local || doc.title} url={doc.source_url} />
        </>
      ) : (
        <Chat key={id} docId={id} suggestions={[t("sug.who"), t("sug.rights"), t("sug.docs"), t("sug.deadline")]} />
      )}
    </div>
  );
}

function Explainer({ id, language, audience, title, url }) {
  const t = useT();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [tries, setTries] = useState(0);

  useEffect(() => {
    let alive = true;
    setData(null);
    setError("");
    api
      .explain(id, language, audience)
      .then((r) => alive && setData(r))
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [id, language, audience, tries]);

  if (error)
    return (
      <Alert>
        {error}{" "}
        <button className="link" onClick={() => setTries(tries + 1)}>
          {t("expl.tryAgain")}
        </button>
      </Alert>
    );
  if (!data)
    return (
      <Spinner
        label={t("expl.writing", {
          audience: audience === "business" ? t("aud.businesses") : t("aud.citizens"),
          language: NATIVE_NAME[language] || language,
        })}
      />
    );
  if (data.error)
    return (
      <Alert>
        {t("expl.busy")}{" "}
        <button className="link" onClick={() => setTries(tries + 1)}>
          {t("expl.tryAgain")}
        </button>
      </Alert>
    );

  const shown = SECTIONS.filter((key) => data.explainer[key]?.length);
  const everything = shown.map((key) => `${t("sec." + key)}. ${data.explainer[key].map((p) => p.point).join(" ")}`).join(" ");

  if (!shown.length) return <Alert kind="info">{t("expl.empty")}</Alert>;

  return (
    <div className="stack">
      <div className="row between">
        <p className="muted">{t("expl.note")}</p>
        <div className="row">
          <ListenButton id="all" text={everything} label={t("ui.listenAll")} />
          <DownloadAudioButton text={everything} name={title} />
          <ShareButton title={title} text={everything} url={url} />
        </div>
      </div>
      {shown.map((key) => (
        <section key={key} className="card">
          <div className="row between">
            <h2>{t("sec." + key)}</h2>
            <ListenButton id={key} text={data.explainer[key].map((p) => p.point).join(" ")} />
          </div>
          <ul className="points">
            {data.explainer[key].map((p, i) => (
              <li key={i}>
                <div>{p.point}</div>
                <Citations items={p.citations} />
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
