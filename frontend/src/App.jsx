import { Component, useEffect, useState } from "react";
import { api } from "./api";
import { LangProvider, useContrast, useLang, useRoute, useT, useTextSize } from "./hooks";
import { Alert, LanguageSelect } from "./components/ui";
import Chat from "./components/Chat";
import Library from "./pages/Library";
import Document from "./pages/Document";
import Agent from "./pages/Agent";

function BrokenPage({ onHome }) {
  const t = useT();
  return (
    <Alert>
      {t("alert.broke")} <a href="#/" onClick={onHome}>{t("alert.goHome")}</a>
    </Alert>
  );
}

class Boundary extends Component {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    if (!this.state.failed) return this.props.children;
    return <BrokenPage onHome={() => this.setState({ failed: false })} />;
  }
}

function Logo() {
  return (
    <svg viewBox="0 0 48 48" aria-hidden="true" focusable="false">
      <rect width="48" height="48" rx="10" fill="#0f2a5c" />
      <path d="M9 13c5-2 10-1.5 15 2v21c-5-3.5-10-4-15-2z" fill="#fff" />
      <path d="M39 13c-5-2-10-1.5-15 2v21c5-3.5 10-4 15-2z" fill="#ffd9b3" />
      <rect x="9" y="39" width="30" height="2.5" rx="1.2" fill="#e8731a" />
    </svg>
  );
}

function AskLibrary() {
  const t = useT();
  return (
    <div className="stack">
      <section className="hero">
        <h1>{t("ask.title")}</h1>
        <p>{t("ask.lead")}</p>
      </section>
      <Chat docId="all" suggestions={[t("ask.s1"), t("ask.s2")]} />
    </div>
  );
}

function Breadcrumb({ page }) {
  const t = useT();
  if (page === "agent") return null;
  const trail =
    page === "ask"
      ? [[t("bc.home"), "#/"], [t("bc.ask")]]
      : page === "library"
      ? [[t("bc.home"), "#/"], [t("bc.docs")]]
      : [[t("bc.home"), "#/"], [t("bc.docs"), "#/library"], [t("bc.explainer")]];
  return (
    <nav className="breadcrumb no-print" aria-label={t("bc.label")}>
      <ol>
        {trail.map(([label, href], i) => (
          <li key={label} aria-current={i === trail.length - 1 ? "page" : undefined}>
            {href ? <a href={href}>{label}</a> : label}
          </li>
        ))}
      </ol>
    </nav>
  );
}

const SOURCES = [
  ["MyGov", "https://www.mygov.in/"],
  ["Reserve Bank of India", "https://www.rbi.org.in/"],
  ["Digital India", "https://www.digitalindia.gov.in/"],
  ["PM-KISAN", "https://pmkisan.gov.in/"],
  ["MUDRA", "https://www.mudra.org.in/"],
];

function Footer() {
  const t = useT();
  return (
    <footer className="site-footer no-print">
      <div className="wrap footer-grid">
        <div>
          <h3>{t("footer.aboutTitle")}</h3>
          <p>{t("footer.about1")}</p>
          <p>{t("footer.about2")}</p>
        </div>
        <div>
          <h3>{t("footer.exploreTitle")}</h3>
          <ul>
            <li><a href="#/">{t("footer.find")}</a></li>
            <li><a href="#/library">{t("footer.docs")}</a></li>
            <li><a href="#/ask">{t("footer.ask")}</a></li>
          </ul>
        </div>
        <div>
          <h3>{t("footer.sourcesTitle")}</h3>
          <ul>
            {SOURCES.map(([name, url]) => (
              <li key={url}><a href={url} target="_blank" rel="noreferrer">{name} ↗</a></li>
            ))}
          </ul>
        </div>
      </div>
      <div className="footer-bottom">
        <div className="wrap">{t("footer.disclaimer")}</div>
      </div>
    </footer>
  );
}

function Shell() {
  const t = useT();
  const route = useRoute();
  const { language, setLanguage, languages } = useLang();
  const size = useTextSize();
  const contrast = useContrast();
  const [health, setHealth] = useState(null);
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    // A free-tier backend sleeps when idle and needs up to a minute to wake: retry before showing the offline notice.
    let stop = false;
    const tries = 14;
    const attempt = (n) =>
      api.health().then((h) => {
        if (stop) return;
        setHealth(h);
        setOffline(false);
      }).catch(() => {
        if (stop) return;
        if (n + 1 >= tries) setOffline(true);
        else setTimeout(() => attempt(n + 1), 5000);
      });
    attempt(0);
    return () => { stop = true; };
  }, []);

  const docMatch = route.match(/^\/doc\/([\w-]+)/);
  const page = docMatch ? "doc" : route.startsWith("/ask") ? "ask" : route.startsWith("/library") ? "library" : "agent";

  return (
    <>
      <div className="utility no-print">
        <div className="wrap utility-inner">
          <span className="notice">{t("util.notice")}</span>
          <div className="util-actions">
            <a className="util-link" href="#main">{t("util.skip")}</a>
            <span className="size" role="group" aria-label={t("util.size")}>
              <button onClick={size.smaller} disabled={size.step === 0} aria-label={t("util.smaller")} title={t("util.smaller")}>A−</button>
              <button onClick={size.bigger} disabled={size.step === size.max} aria-label={t("util.bigger")} title={t("util.bigger")}>A+</button>
            </span>
            <button className="util-btn" onClick={contrast.toggle} aria-pressed={contrast.on} title={t("util.contrastTitle")}>
              ◐ {t("util.contrast")}
            </button>
            <LanguageSelect value={language} onChange={setLanguage} languages={languages} />
          </div>
        </div>
      </div>
      <div className="tricolor" aria-hidden="true" />

      <header className="site-header no-print">
        <div className="wrap header-inner">
          <a href="#/" className="brand" aria-label={t("brand.home")}>
            <Logo />
            <span>
              <span className="brand-name">Haq<small>हक़</small></span>
              <span className="brand-tag">{t("brand.tag")}</span>
            </span>
          </a>
          <nav className="mainnav" aria-label={t("nav.main")}>
            <a href="#/" className={page === "agent" ? "on" : ""}>{t("nav.find")}</a>
            <a href="#/library" className={page === "library" || page === "doc" ? "on" : ""}>{t("nav.docs")}</a>
            <a href="#/ask" className={page === "ask" ? "on" : ""}>{t("nav.ask")}</a>
          </nav>
        </div>
      </header>
      <Breadcrumb page={page} />

      <main id="main">
        {(offline || (health && !health.ai_configured)) && (
          <div className="wrap" style={{ paddingTop: 16 }}>
            {offline && <Alert>{t("alert.offline")}</Alert>}
            {health && !health.ai_configured && <Alert>{t("alert.nokey")}</Alert>}
          </div>
        )}
        <Boundary key={route}>
          {page === "agent" ? (
            <Agent />
          ) : (
            <div className="wrap page">
              {page === "doc" ? <Document id={docMatch[1]} /> : page === "library" ? <Library /> : <AskLibrary />}
            </div>
          )}
        </Boundary>
      </main>

      <Footer />
    </>
  );
}

export default function App() {
  return (
    <LangProvider>
      <Shell />
    </LangProvider>
  );
}
