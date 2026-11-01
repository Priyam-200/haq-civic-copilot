import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { go, useLang, useT } from "../hooks";
import { Alert, Spinner } from "../components/ui";

const SOURCE_KEYS = ["mygov", "rbi", "digitalindia", "pib"];
const TAG = { mygov: "MyGov", rbi: "RBI", digitalindia: "Digital India", pib: "PIB" };

export default function Library() {
  const t = useT();
  const { language } = useLang();
  const [docs, setDocs] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [url, setUrl] = useState("");
  const [drag, setDrag] = useState(false);
  const fileRef = useRef(null);

  const load = useCallback(() => {
    api.documents(language).then(setDocs).catch((e) => setError(e.message));
  }, [language]);
  useEffect(load, [load]);

  async function upload(file) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) return setError(t("err.pdfPicker"));
    setError("");
    setBusy(t("lib.reading", { name: file.name }));
    try {
      const r = await api.upload(file);
      go(`/doc/${r.doc_id}`);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  async function importUrl(link, title) {
    if (!link.trim()) return;
    setError("");
    setBusy(t("lib.fetching"));
    try {
      const r = await api.importUrl(link.trim(), title);
      go(`/doc/${r.doc_id}`);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  async function remove(d) {
    if (!window.confirm(t("lib.deleteConfirm", { title: d.title }))) return;
    try {
      await api.deleteDocument(d.doc_id);
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <div className="stack">
      <section className="hero">
        <h1>{t("lib.title")}</h1>
        <p>{t("lib.lead")}</p>
      </section>

      {error && <Alert onClose={() => setError("")}>{error}</Alert>}
      {busy && (
        <Alert kind="info">
          <Spinner label={busy} />
        </Alert>
      )}

      <div className="grid-2">
        <section
          className={`card dropzone ${drag ? "drag" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            upload(e.dataTransfer.files[0]);
          }}
        >
          <h2>{t("lib.uploadTitle")}</h2>
          <p className="muted">{t("lib.uploadText")}</p>
          <input ref={fileRef} type="file" accept="application/pdf,.pdf" hidden onChange={(e) => upload(e.target.files[0])} />
          <button className="btn btn-primary" onClick={() => fileRef.current.click()} disabled={!!busy}>
            {t("lib.choose")}
          </button>
        </section>

        <section className="card">
          <h2>{t("lib.importTitle")}</h2>
          <p className="muted">{t("lib.importText")}</p>
          <form
            className="row"
            onSubmit={(e) => {
              e.preventDefault();
              importUrl(url);
            }}
          >
            <input
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://…"
              aria-label={t("lib.urlAria")}
              required
              dir="ltr"
            />
            <button className="btn btn-primary" disabled={!!busy}>
              {t("lib.import")}
            </button>
          </form>
        </section>
      </div>

      <Catalog onImport={importUrl} busy={!!busy} />

      <LiveFeed onImport={importUrl} busy={!!busy} />

      <section>
        <h2>
          {t("lib.libraryTitle")} {docs && <span className="count">{docs.length}</span>}
        </h2>
        {docs === null && !error && <Spinner label={t("lib.loading")} />}
        {docs && !docs.length && <p className="muted">{t("lib.empty")}</p>}
        <ul className="doc-list">
          {(docs || []).map((d) => (
            <li key={d.doc_id} className="card doc-item">
              <div>
                <a className="doc-title" href={`#/doc/${d.doc_id}`}>
                  {d.title_local || d.title}
                </a>
                <div className="meta">
                  <span className="tag">{t(`src.${d.source_type}`) !== `src.${d.source_type}` ? t(`src.${d.source_type}`) : d.source_type}</span>
                  <span>
                    {t("lib.pages", { n: d.page_count })} · {t("lib.sections", { n: d.chunk_count })}
                  </span>
                  {d.source_url && (
                    <a href={d.source_url} target="_blank" rel="noreferrer">
                      {t("lib.original")} ↗
                    </a>
                  )}
                </div>
              </div>
              <button className="btn btn-ghost btn-sm" onClick={() => remove(d)} aria-label={`${t("lib.delete")}: ${d.title}`}>
                {t("lib.delete")}
              </button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function LiveFeed({ onImport, busy }) {
  const t = useT();
  const { language } = useLang();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [source, setSource] = useState("all");

  const load = useCallback(() => {
    setData(null);
    setError("");
    api
      .live(source, 10, language)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [source, language]);
  useEffect(load, [load]);

  const down = (data?.sources || []).filter((s) => !s.ok);
  const lab = (s) => (t(`feed.label.${s.key}`) !== `feed.label.${s.key}` ? t(`feed.label.${s.key}`) : s.label);
  const why = (s) => (String(s.error || "").includes("automated access") ? t("feed.blocked") : t("feed.unreachable"));

  return (
    <section className="card">
      <div className="row between">
        <h2>
          {t("live.title")} <span className="tag live">{t("live.badge")}</span>
        </h2>
        <div className="row">
          <select value={source} onChange={(e) => setSource(e.target.value)} aria-label={t("live.sourceAria")}>
            <option value="all">{t("live.all")}</option>
            {SOURCE_KEYS.map((k) => (
              <option key={k} value={k}>
                {TAG[k]}
              </option>
            ))}
          </select>
          <button className="btn btn-ghost btn-sm" onClick={load}>
            {t("live.refresh")}
          </button>
        </div>
      </div>
      <p className="muted">{t("live.text")}</p>
      <p className="muted small">{t("lib.importHelp")}</p>
      {data === null && !error && <Spinner label={t("live.fetching")} />}
      {error && <Alert kind="info">{t("live.unavailable")} {error}</Alert>}
      {data && !data.items.length && <Alert kind="info">{t("live.none")}</Alert>}
      {down.length > 0 && data.items.length > 0 && (
        <p className="muted small">{t("live.down", { list: down.map((s) => `${lab(s)} (${why(s)})`).join("; ") })}</p>
      )}
      <ul className="feed">
        {(data?.items || []).map((it) => (
          <li key={it.url}>
            <span>
              <span className="tag">{TAG[it.source] || it.source}</span> {it.title_local || it.title}
            </span>
            {it.doc_id ? (
              <a className="btn btn-ghost btn-sm" href={`#/doc/${it.doc_id}`}>
                {t("lib.open")}
              </a>
            ) : (
              <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => onImport(it.url, it.title)}>
                {t("lib.readExplain")}
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Catalog({ onImport, busy }) {
  const t = useT();
  const [items, setItems] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api.catalog().then(setItems).catch((e) => setError(e.message));
  }, []);
  const label = (c) => (t(`catalog.${c.key}`) !== `catalog.${c.key}` ? t(`catalog.${c.key}`) : c.name);
  return (
    <section className="card">
      <h2>{t("lib.catalogTitle")}</h2>
      <p className="muted">{t("lib.catalogText")}</p>
      <p className="muted small">{t("lib.importHelp")}</p>
      {error && <Alert kind="info">{error}</Alert>}
      {items === null && !error && <Spinner label={t("lib.loading")} />}
      <ul className="feed">
        {(items || []).map((c) => (
          <li key={c.key}>
            <span>{label(c)}</span>
            {c.doc_id ? (
              <a className="btn btn-ghost btn-sm" href={`#/doc/${c.doc_id}`}>
                {t("lib.open")}
              </a>
            ) : (
              <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => onImport(c.url, c.name.split(" (")[0])}>
                {t("lib.readExplain")}
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
