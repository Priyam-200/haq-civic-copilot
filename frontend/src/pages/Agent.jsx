import { useEffect, useRef, useState } from "react";
import { api, runAgent } from "../api";
import { AGENT_ERROR } from "../i18n";
import { useLang, useT } from "../hooks";
import { Alert, Citations, ListenButton, MicButton, ShareButton, Spinner, VERDICT_CLASS } from "../components/ui";

// label/placeholder are translation keys; option values stay English because the server expects them
const FIELDS = [
  { key: "age", label: "field.age", ph: "field.age.ph", type: "number" },
  { key: "state", label: "field.state", ph: "field.state.ph", type: "text" },
  { key: "occupation", label: "field.work", ph: "field.work.ph", type: "text" },
  { key: "annual_income_inr", label: "field.income", ph: "field.income.ph", type: "number" },
  { key: "category", label: "field.category", type: "select", options: ["", "General", "OBC", "SC", "ST", "EWS"] },
  { key: "gender", label: "field.gender", type: "select", options: ["", "Female", "Male", "Other"] },
  { key: "area", label: "field.area", type: "select", options: ["", "rural", "urban"] },
  { key: "disability", label: "field.disability", type: "select", options: ["", "yes", "no"] },
];

const STATUS_ICON = { running: "…", done: "✓", skipped: "!", needs_info: "?" };
const CRIT_ICON = { met: "✔", unmet: "✖", unknown: "?" };
const EXAMPLE_KEYS = ["farmer", "woman", "vendor", "student"];

function profileToBody(form) {
  const out = {};
  for (const f of FIELDS) {
    const v = form[f.key];
    if (v === undefined || v === "") continue;
    out[f.key] = f.key === "disability" ? v === "yes" : v;
  }
  return out;
}

/** Translate a value only if a translation exists (categories like OBC / SC stay as they are). */
function useOptionLabel() {
  const t = useT();
  return (v) => {
    if (!v) return t("opt.choose");
    const k = `opt.${v}`;
    const s = t(k);
    return s === k ? v : s;
  };
}

export default function Agent() {
  const t = useT();
  const optLabel = useOptionLabel();
  const { language } = useLang();
  const [text, setText] = useState("");
  const [form, setForm] = useState({});
  const [live, setLive] = useState(true);
  const [running, setRunning] = useState(false);
  const [steps, setSteps] = useState([]);
  const [asking, setAsking] = useState(null); // needs_info event
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const abort = useRef(null);
  const resultsRef = useRef(null);

  useEffect(() => () => abort.current?.abort(), []);

  async function run(force = false) {
    const body = { text, profile: profileToBody(form), language, live, force };
    if (!text.trim() && !Object.keys(body.profile).length) {
      setError(t("home.fillFirst"));
      return;
    }
    abort.current?.abort();
    const ctl = new AbortController();
    abort.current = ctl;
    setRunning(true);
    setError("");
    setAsking(null);
    setResult(null);
    setSteps([]);
    try {
      await runAgent(
        body,
        (ev) => {
          if (ev.type === "step") {
            setSteps((prev) => {
              const i = prev.findIndex((s) => s.id === ev.id);
              if (i < 0) return [...prev, ev];
              const next = prev.slice();
              next[i] = ev;
              return next;
            });
            // keep what the agent understood so the form shows it
            if (ev.profile) setForm((f) => mergeProfile(f, ev.profile));
          } else if (ev.type === "needs_info") {
            setAsking(ev);
            setForm((f) => mergeProfile(f, ev.profile));
          } else if (ev.type === "result") {
            setResult(ev);
            setTimeout(() => resultsRef.current?.scrollIntoView({ behavior: "smooth" }), 100);
          } else if (ev.type === "error") {
            setError(AGENT_ERROR[ev.code] ? t(AGENT_ERROR[ev.code]) : ev.message);
          }
        },
        ctl.signal
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  }

  function reset() {
    abort.current?.abort();
    setText("");
    setForm({});
    setSteps([]);
    setResult(null);
    setAsking(null);
    setError("");
    setRunning(false);
  }

  return (
    <>
      <section className="hero-band" aria-labelledby="hero-title">
        <div className="wrap hero-inner">
          <div className="hero-copy">
            <p className="eyebrow">{t("home.eyebrow")}</p>
            <h1 id="hero-title">{t("home.title")}</h1>
            <p>{t("home.lead")}</p>
            <ul className="hero-points">
              <li>{t("home.pt1")}</li>
              <li>{t("home.pt2")}</li>
              <li>{t("home.pt3")}</li>
            </ul>
          </div>

          <section className="hero-card" aria-label={t("home.cardAria")}>
            <h2>{t("home.cardTitle")}</h2>
            <label htmlFor="story" className="label">
              {t("home.tell")}
            </label>
            <textarea
              id="story"
              rows={4}
              value={text}
              maxLength={3000}
              onChange={(e) => setText(e.target.value)}
              placeholder={t("home.placeholder")}
            />
            <div className="row">
              <MicButton value={text} onChange={setText} />
            </div>
            <div className="example-row">
              <span>{t("home.tryExample")}</span>
              {EXAMPLE_KEYS.map((k) => (
                <button key={k} type="button" className="chip" onClick={() => setText(t(`ex.${k}.text`))}>
                  {t(`ex.${k}.label`)}
                </button>
              ))}
            </div>

            <details open={!!asking}>
              <summary>{t("home.details")}</summary>
              <div className="form-grid">
                {FIELDS.map((f) => (
                  <label key={f.key} className={asking?.missing?.includes(f.key) ? "need" : ""}>
                    <span>{t(f.label)}</span>
                    {f.type === "select" ? (
                      <select value={form[f.key] ?? ""} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}>
                        {f.options.map((o) => (
                          <option key={o} value={o}>
                            {optLabel(o)}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        type={f.type}
                        min={f.type === "number" ? 0 : undefined}
                        value={form[f.key] ?? ""}
                        placeholder={t(f.ph)}
                        onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                      />
                    )}
                  </label>
                ))}
              </div>
            </details>

            <label className="check">
              <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} />
              <span>{t("home.live")}</span>
            </label>

            <div className="row">
              <button className="btn btn-primary btn-lg" onClick={() => run(false)} disabled={running}>
                {running ? t("home.working") : t("home.check")}
              </button>
              {(steps.length > 0 || result) && (
                <button className="btn btn-ghost" onClick={reset}>
                  {t("home.startOver")}
                </button>
              )}
            </div>
          </section>
        </div>
      </section>

      <div className="wrap page stack">
        {error && <Alert onClose={() => setError("")}>{error}</Alert>}

        {asking && (
          <Alert kind="info">
            <strong>{t("asking.title")}</strong>
            <ul className="questions">
              {asking.questions.map((q) => (
                <li key={q.field}>{q.question}</li>
              ))}
            </ul>
            <div className="row">
              <span className="muted">{t("asking.fill")}</span>
              <button className="btn btn-ghost btn-sm" onClick={() => run(true)} disabled={running}>
                {t("asking.anyway")}
              </button>
            </div>
          </Alert>
        )}

        {steps.length > 0 && <Timeline steps={steps} running={running} />}

        <div ref={resultsRef}>{result && <Results result={result} />}</div>

        {!running && !result && steps.length === 0 && <HomeExtras />}
      </div>
    </>
  );
}

const ICONS = {
  doc: <path d="M7 3h7l5 5v13H7zM14 3v5h5M10 13h6M10 17h6" />,
  ask: <path d="M4 5h16v10H9l-5 4zM8 9h8M8 12h5" />,
  scheme: <path d="M12 3l9 4v2H3V7zM5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20h18" />,
  live: <path d="M5 12a7 7 0 0 1 14 0M8 12a4 4 0 0 1 8 0M12 12v.01M12 12v8" />,
};

function Icon({ name }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {ICONS[name]}
    </svg>
  );
}

/** Home page below the form: what Haq offers, how it works, and live numbers from the server. */
function HomeExtras() {
  const t = useT();
  const { languages } = useLang();
  const [docs, setDocs] = useState(null);
  const [schemes, setSchemes] = useState(null);
  useEffect(() => {
    api.health().then((h) => setDocs(h.documents)).catch(() => {});
    api.catalog().then((c) => setSchemes(c.length)).catch(() => {});
  }, []);

  const tiles = [
    ["doc", "tile.doc", "#/library"],
    ["ask", "tile.ask", "#/ask"],
    ["scheme", "tile.scheme", "#/library"],
    ["live", "tile.live", "#/library"],
  ];
  return (
    <>
      <section aria-labelledby="svc">
        <div className="section-title">
          <h2 id="svc">{t("svc.title")}</h2>
        </div>
        <div className="tiles">
          {tiles.map(([icon, key, href]) => (
            <a key={key} className="tile" href={href}>
              <span className="ico"><Icon name={icon} /></span>
              <strong>{t(`${key}.t`)}</strong>
              <span className="d">{t(`${key}.d`)}</span>
              <span className="go">{t("tile.open")}</span>
            </a>
          ))}
        </div>
      </section>

      <section aria-labelledby="how">
        <div className="section-title">
          <h2 id="how">{t("how.title")}</h2>
        </div>
        <div className="how">
          {[1, 2, 3].map((n) => (
            <div key={n}><strong>{t(`how.${n}.t`)}</strong>{t(`how.${n}.d`)}</div>
          ))}
        </div>
      </section>

      <section className="stats" aria-label={t("stats.aria")}>
        <div className="stat"><b>{docs ?? "–"}</b><span>{t("stats.docs")}</span></div>
        <div className="stat"><b>{languages.length}</b><span>{t("stats.langs")}</span></div>
        <div className="stat"><b>{schemes ?? "–"}</b><span>{t("stats.schemes")}</span></div>
        <div className="stat"><b>8</b><span>{t("stats.steps")}</span></div>
      </section>
    </>
  );
}

function mergeProfile(form, profile) {
  const next = { ...form };
  for (const [k, v] of Object.entries(profile || {})) {
    if (!FIELDS.some((f) => f.key === k)) continue;
    if (next[k] === undefined || next[k] === "") next[k] = typeof v === "boolean" ? (v ? "yes" : "no") : String(v);
  }
  return next;
}

/** Progress texts: the server sends a code and numbers; the sentence is built here in the chosen language. */
function stepTitle(s, t) {
  if (s.id.startsWith("eval-")) return t("step.eval", { title: s.doc_title_local || s.doc_title || "" });
  const k = `step.${s.id}`;
  return t(k) !== k ? t(k) : s.title;
}

function stepDetail(s, t, optLabel) {
  const p = s.params || {};
  switch (s.code) {
    case "profile_done": {
      const labels = Object.fromEntries(FIELDS.map((f) => [f.key, f.label]));
      return Object.entries(s.profile || {})
        .filter(([k]) => labels[k])
        .map(([k, v]) => `${t(labels[k])}: ${(s.profile_display && s.profile_display[k]) || (typeof v === "boolean" ? optLabel(v ? "yes" : "no") : optLabel(String(v)) || v)}`)
        .join(" · ");
    }
    case "needs_info": return t("detail.needs_info");
    case "plan_done": return t("detail.plan", { queries: (s.queries_local || s.queries || []).join(" • ") });
    case "catalog_done": return t("detail.catalog_done", {
      names: (p.keys && p.keys.length ? p.keys.map((k) => t("catalog." + k).split(" (")[0]) : p.names || []).join(", "),
    });
    case "catalog_none": return t("detail.catalog_none");
    case "live_none": return t("detail.live_none");
    case "live_done":
    case "live_nomatch": {
      const base = t(s.code === "live_done" ? "detail.live_done" : "detail.live_nomatch", {
        read: p.read, total: p.total, ok: (p.ok_keys ? p.ok_keys.map((k) => t("feed.label." + k)) : p.ok || []).join(", "),
      });
      return p.down && p.down.length ? `${base} ${t("detail.live_down", { down: (p.down_keys ? p.down_keys.map((k) => t("feed.label." + k)) : p.down).join(", ") })}` : base;
    }
    case "retrieve_done": return t("detail.retrieve_done", p);
    case "eval_done": return t("detail.eval_done", p);
    case "eval_skipped": return t("detail.eval_skipped");
    case "verify_done": return t("detail.verify_done", p);
    case "checklist_done": return t("detail.checklist_done", p);
    default: return s.detail;
  }
}

function Timeline({ steps, running }) {
  const t = useT();
  const optLabel = useOptionLabel();
  return (
    <section className="card">
      <h2>
        {t("timeline.title")} {running && <Spinner />}
      </h2>
      <ol className="timeline">
        {steps.map((s) => {
          const detail = stepDetail(s, t, optLabel);
          return (
            <li key={s.id} className={`step step-${s.status}`}>
              <span className="step-icon" aria-hidden="true">
                {STATUS_ICON[s.status] || "•"}
              </span>
              <div>
                <div className="step-title">
                  {stepTitle(s, t)}
                  {s.tool && <code className="tool">{s.tool}</code>}
                </div>
                {detail && <div className="step-detail">{detail}</div>}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function Results({ result }) {
  const t = useT();
  const { results, checklist, warnings } = result;
  const good = results.filter((r) => r.verdict === "eligible" || r.verdict === "likely");
  const summary = good.map((r) => `${r.name_local || r.name}. ${r.summary}`).join(" ");
  const shareText = [
    ...good.map((r) => `• ${r.name_local || r.name} (${t("verdict." + r.verdict)}): ${r.summary}`),
    checklist.length ? t("results.keepReady", { items: checklist.map((c) => c.item).join(", ") }) : "",
  ]
    .filter(Boolean)
    .join("\n");

  return (
    <div className="stack">
      <div className="row between">
        <h2>{t("results.title")}</h2>
        <div className="row no-print">
          {summary && <ListenButton id="results" text={summary} label={t("results.listen")} />}
          {shareText && <ShareButton title={t("results.shareTitle")} text={shareText} />}
          <button className="btn btn-ghost btn-sm" onClick={() => window.print()}>
            {t("results.print")}
          </button>
        </div>
      </div>

      {warnings?.map((w, i) => (
        <Alert key={i} kind="info">
          {w}
        </Alert>
      ))}
      {result.message_code === "no_match" && (
        <Alert kind="info">
          {t("results.noMatch")} {t("results.tip")} <a href="#/library">{t("results.tipLink")}</a>
        </Alert>
      )}

      {results.map((r) => (
        <SchemeCard key={r.doc_id} r={r} />
      ))}

      {checklist.length > 0 && (
        <section className="card">
          <h2>{t("results.checklist")}</h2>
          <ul className="checklist">
            {checklist.map((c) => (
              <li key={c.item}>
                <label>
                  <input type="checkbox" /> <span>{c.item}</span>
                </label>
                <div className="muted small">{t("results.for", { schemes: [...new Set(c.schemes)].join(", ") })}</div>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function SchemeCard({ r }) {
  const t = useT();
  const cls = VERDICT_CLASS[r.verdict] || "unclear";
  const [open, setOpen] = useState(r.verdict === "eligible" || r.verdict === "likely");
  return (
    <article className={`card scheme verdict-${cls}`}>
      <div className="row between">
        <div>
          <span className={`badge badge-${cls}`}>{t("verdict." + (VERDICT_CLASS[r.verdict] ? r.verdict : "unclear"))}</span>
          <h3>{r.name_local || r.name}</h3>
        </div>
        <button className="btn btn-ghost btn-sm no-print" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? t("scheme.hide") : t("scheme.show")}
        </button>
      </div>
      {r.summary && <p>{r.summary}</p>}
      <div className="meta">
        <a href={`#/doc/${r.doc_id}`}>{t("scheme.readFull")}</a>
        {r.source_url && (
          <a href={r.source_url} target="_blank" rel="noreferrer">
            {t("scheme.official")} ↗
          </a>
        )}
      </div>

      {open && (
        <div className="details">
          {r.criteria.length > 0 && (
            <Block title={t("scheme.checked")}>
              <ul className="criteria">
                {r.criteria.map((c, i) => (
                  <li key={i} className={`crit crit-${c.status}`}>
                    <span className="crit-icon" aria-label={t("crit." + c.status)}>
                      {CRIT_ICON[c.status]}
                    </span>
                    <div>
                      <div>{c.criterion}</div>
                      {c.reason && <div className="muted small">{c.reason}</div>}
                      <Citations items={c.citations} />
                    </div>
                  </li>
                ))}
              </ul>
            </Block>
          )}
          <Points title={t("scheme.get")} items={r.benefits} />
          <Points title={t("scheme.docs")} items={r.documents_needed} />
          <Points title={t("scheme.apply")} items={r.how_to_apply} ordered />
          <Points title={t("scheme.deadlines")} items={r.deadlines} />
        </div>
      )}
    </article>
  );
}

function Block({ title, children }) {
  return (
    <div className="block">
      <h4>{title}</h4>
      {children}
    </div>
  );
}

function Points({ title, items, ordered }) {
  if (!items?.length) return null;
  const List = ordered ? "ol" : "ul";
  return (
    <Block title={title}>
      <List className="points">
        {items.map((p, i) => (
          <li key={i}>
            <div>{p.point}</div>
            <Citations items={p.citations} />
          </li>
        ))}
      </List>
    </Block>
  );
}
