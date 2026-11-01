import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { setUiLanguage, t } from "./i18n";

/* ---------- language (shared by every page) ---------- */
const FALLBACK = [{ name: "English", code: "en-IN" }, { name: "Hindi", code: "hi-IN" }];
const LangContext = createContext({ language: "English", setLanguage() {}, languages: FALLBACK, code: "en-IN" });

export function LangProvider({ children }) {
  const [languages, setLanguages] = useState(FALLBACK);
  const [language, setLanguageState] = useState(() => {
    try {
      return localStorage.getItem("haq.language") || "English";
    } catch {
      return "English";
    }
  });
  useEffect(() => {
    api.languages().then(setLanguages).catch(() => {});
  }, []);
  const setLanguage = (l) => {
    setLanguageState(l);
    try {
      localStorage.setItem("haq.language", l);
    } catch {
      /* private mode: just keep it in memory */
    }
  };
  const code = (languages.find((l) => l.name === language) || FALLBACK[0]).code;
  setUiLanguage(language, code);          // before children render, so every t() call sees the new language
  return <LangContext.Provider value={{ language, setLanguage, languages, code }}>{children}</LangContext.Provider>;
}

export const useLang = () => useContext(LangContext);

/** Translation function that re-renders its component when the language changes. */
export function useT() {
  const { language } = useLang();
  return useCallback((key, vars) => t(key, vars), [language]);
}

/* ---------- voice out (read aloud) ----------
   1. If this device has a voice for the language, the browser reads it.
   2. Otherwise (common on Windows for Indian languages) the browser would silently use an
      English voice, so we ask the server for real speech in that language instead. */
const baseLang = (code) => code.toLowerCase().replace("_", "-").split("-")[0];

export function splitForSpeech(text, max = 450) {
  const sentences = text.replace(/\s+/g, " ").trim().match(/[^.!?।]+[.!?।]*\s*/g) || [text];
  const chunks = [];
  let cur = "";
  for (const s of sentences) {
    if (cur && (cur + s).length > max) {
      chunks.push(cur.trim());
      cur = "";
    }
    cur += s;
    while (cur.length > max * 1.5) {            // a sentence with no stops: cut at a space
      const cut = cur.lastIndexOf(" ", max) > 0 ? cur.lastIndexOf(" ", max) : max;
      chunks.push(cur.slice(0, cut).trim());
      cur = cur.slice(cut);
    }
  }
  if (cur.trim()) chunks.push(cur.trim());
  return chunks.filter(Boolean);
}

let stopCurrent = null;                          // only one thing speaks at a time

export function useSpeech() {
  const { code, language } = useLang();
  const synth = typeof window !== "undefined" && "speechSynthesis" in window ? window.speechSynthesis : null;
  const [voices, setVoices] = useState(() => (synth ? synth.getVoices() : []));
  const [speakingId, setSpeakingId] = useState(null);
  const [loadingId, setLoadingId] = useState(null);
  const [error, setError] = useState("");
  const run = useRef(0);
  const audio = useRef(null);
  const abort = useRef(null);

  useEffect(() => {
    if (!synth) return;
    const load = () => setVoices(synth.getVoices());
    load();
    synth.addEventListener("voiceschanged", load);
    return () => synth.removeEventListener("voiceschanged", load);
  }, [synth]);

  const stop = useCallback(() => {
    run.current += 1;
    abort.current?.abort();
    if (audio.current) {
      audio.current.pause();
      audio.current = null;
    }
    if (synth) synth.cancel();
    setSpeakingId(null);
    setLoadingId(null);
  }, [synth]);

  useEffect(() => stop, [stop]);                 // stop talking when leaving the page

  const localVoice = useCallback(() => {
    const base = baseLang(code);
    return voices.find((v) => v.lang.toLowerCase().replace("_", "-") === code.toLowerCase()) ||
      voices.find((v) => baseLang(v.lang) === base);
  }, [voices, code]);

  const speak = useCallback(
    async (id, text) => {
      if (!text) return;
      stopCurrent?.();
      stopCurrent = stop;
      stop();
      setError("");
      const my = ++run.current;
      const voice = localVoice();

      if (synth && (voice || baseLang(code) === "en")) {
        const u = new SpeechSynthesisUtterance(text);
        u.lang = code;
        if (voice) u.voice = voice;
        u.onend = () => run.current === my && setSpeakingId(null);
        u.onerror = () => run.current === my && setSpeakingId(null);
        setSpeakingId(id);
        synth.speak(u);
        return;
      }

      // no local voice for this language: use server-side speech
      const parts = splitForSpeech(text);
      abort.current = new AbortController();
      setLoadingId(id);
      try {
        let next = api.tts(parts[0], language, abort.current.signal);
        for (let i = 0; i < parts.length; i++) {
          const blob = await next;
          if (run.current !== my) return;
          if (i + 1 < parts.length) next = api.tts(parts[i + 1], language, abort.current.signal); // prefetch
          const url = URL.createObjectURL(blob);
          const a = new Audio(url);
          audio.current = a;
          setLoadingId(null);
          setSpeakingId(id);
          await new Promise((resolve, reject) => {
            a.onended = resolve;
            a.onerror = () => reject(new Error(t("voice.play")));
            a.play().catch(reject);
          });
          URL.revokeObjectURL(url);
          if (run.current !== my) return;
        }
        setSpeakingId(null);
      } catch (e) {
        if (e.name === "AbortError" || run.current !== my) return;
        setSpeakingId(null);
        setLoadingId(null);
        setError(e.message || t("voice.fail"));
      }
    },
    [synth, code, language, localVoice, stop]
  );

  return { supported: true, speakingId, loadingId, speak, stop, error };
}

/* ---------- voice in (dictation) ----------
   Two ways, chosen automatically:
   1. The browser's own recogniser (instant and free) - but it relies on a Google web service and often fails
      ("network", "service-not-allowed") or lacks a language.
   2. Haq's own voice service: record the microphone, convert to WAV, send it to /stt (Gemini) and get the words back.
   If (1) fails, (2) takes over automatically and is remembered for the rest of the visit. */
const SILENCE_STOP_MS = 1200;      // stop this long after the last word
const NO_SPEECH_STOP_MS = 8000;    // give up if nothing is said for this long
const NATIVE_FALLBACK = new Set(["network", "service-not-allowed", "language-not-supported"]);

function encodeWav(samples, rate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buffer);
  const text = (at, s) => [...s].forEach((c, i) => v.setUint8(at + i, c.charCodeAt(0)));
  text(0, "RIFF");
  v.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);         // PCM
  v.setUint16(22, 1, true);         // mono
  v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  text(36, "data");
  v.setUint32(40, samples.length * 2, true);
  samples.forEach((s, i) => v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, s)) * 32767, true));
  return new Blob([buffer], { type: "audio/wav" });
}

/** Cut the silence before the first word and after the last (keeping a quarter second), so less audio is sent. */
function trimSilence(x, rate) {
  const win = Math.floor(rate * 0.02);
  const n = Math.floor(x.length / win);
  const env = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    let sum = 0;
    for (let j = 0; j < win; j++) sum += x[i * win + j] * x[i * win + j];
    env[i] = Math.sqrt(sum / win);
  }
  let peak = 0;
  for (const v of env) peak = Math.max(peak, v);
  const limit = Math.max(0.01, peak * 0.08);
  let a = 0;
  while (a < n && env[a] < limit) a++;
  if (a >= n) return x; // nothing loud: let the server decide
  let b = n - 1;
  while (b > a && env[b] < limit) b--;
  const pad = Math.floor(rate * 0.25);
  return x.subarray(Math.max(0, a * win - pad), Math.min(x.length, (b + 1) * win + pad));
}

/** Whatever the browser recorded (webm/opus, mp4...) -> 16 kHz mono 16-bit WAV. */
async function blobToWav(blob) {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  const ctx = new Ctx();
  try {
    const audio = await ctx.decodeAudioData(await blob.arrayBuffer());
    const off = new OfflineAudioContext(1, Math.max(1, Math.ceil(audio.duration * 16000)), 16000);
    const src = off.createBufferSource();
    src.buffer = audio;
    src.connect(off.destination);
    src.start();
    return encodeWav(trimSilence((await off.startRendering()).getChannelData(0), 16000), 16000);
  } finally {
    ctx.close();
  }
}

export function useDictation(value, onChange) {
  const t = useT();
  const { code, language } = useLang();
  const Rec = typeof window !== "undefined" && (window.SpeechRecognition || window.webkitSpeechRecognition);
  const canRecord =
    typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== "undefined";
  const [state, setState] = useState("idle"); // idle | listening | recording | transcribing
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const base = useRef("");
  const latest = useRef({ value, onChange });
  latest.current = { value, onChange };
  const native = useRef(null);
  const recorder = useRef(null);
  const stream = useRef(null);
  const timer = useRef(null);
  const useServer = useRef(false);
  try {
    useServer.current = useServer.current || sessionStorage.getItem("haq.stt") === "server";
  } catch {
    /* storage unavailable */
  }

  const join = (text) => (base.current ? base.current + " " : "") + text;

  const startRecording = useCallback(async () => {
    let mic;
    try {
      mic = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      setState("idle");
      setError(e?.name === "NotAllowedError" ? t("mic.denied") : e?.name === "NotFoundError" ? t("mic.nomic") : t("mic.fail"));
      return;
    }
    stream.current = mic;
    const chunks = [];
    const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"].find((m) =>
      MediaRecorder.isTypeSupported(m)
    );
    const rec = new MediaRecorder(mic, mime ? { mimeType: mime } : undefined);
    recorder.current = rec;
    let vadTimer = null;
    let audioCtx = null;
    rec.ondataavailable = (ev) => ev.data && ev.data.size && chunks.push(ev.data);
    rec.onstop = async () => {
      clearTimeout(timer.current);
      clearInterval(vadTimer);
      audioCtx && audioCtx.close();
      mic.getTracks().forEach((tr) => tr.stop());
      stream.current = null;
      recorder.current = null;
      if (!chunks.length) return setState("idle");
      setState("transcribing");
      try {
        const wav = await blobToWav(new Blob(chunks, { type: rec.mimeType }));
        const { text } = await api.stt(wav, language);
        if (text) latest.current.onChange(join(text));
        else setError(t("mic.empty"));
      } catch (e) {
        setError(e?.message || t("mic.fail"));
      } finally {
        setState("idle");
      }
    };
    rec.start();
    setState("recording");
    // Stop by itself shortly after the person stops talking, so they do not have to tap and the words appear sooner.
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      audioCtx = new Ctx();
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 1024;
      audioCtx.createMediaStreamSource(mic).connect(analyser);
      const samples = new Float32Array(analyser.fftSize);
      const began = performance.now();
      let noise = [];
      let heardSpeech = false;
      let quietSince = null;
      vadTimer = setInterval(() => {
        analyser.getFloatTimeDomainData(samples);
        let sum = 0;
        for (const v of samples) sum += v * v;
        const level = Math.sqrt(sum / samples.length);
        const now = performance.now();
        if (now - began < 400) return noise.push(level);                          // learn this microphone's background noise
        // Background noise = the QUIETEST moment so far (the person may already be talking in the first 0.4 s).
        // The threshold is capped, so speech that starts at once can never raise it above normal speaking level.
        const quietest = noise.length ? Math.min(...noise) : 0;
        const limit = Math.min(0.05, Math.max(0.015, 3 * quietest));
        if (level > limit) {
          heardSpeech = true;
          quietSince = null;
        } else if (heardSpeech) {
          quietSince = quietSince ?? now;
          if (now - quietSince > SILENCE_STOP_MS && rec.state === "recording") rec.stop();
        } else if (now - began > NO_SPEECH_STOP_MS && rec.state === "recording") {
          rec.stop();                                                             // nobody spoke at all
        }
      }, 100);
    } catch {
      /* no silence detection here: the person taps to stop */
    }
    timer.current = setTimeout(() => rec.state === "recording" && rec.stop(), 60000); // never record forever
  }, [language, t]);

  const startNative = useCallback(() => {
    const r = new Rec();
    let switched = false;
    r.lang = code;
    r.interimResults = true; // show words as they are heard
    r.continuous = false;
    r.onresult = (e) => {
      const heard = Array.from(e.results).map((x) => x[0].transcript).join(" ").replace(/ +/g, " ").trim();
      latest.current.onChange(join(heard));
    };
    r.onerror = (e) => {
      if (e.error === "aborted") return;
      const fallback = NATIVE_FALLBACK.has(e.error) || !["not-allowed", "no-speech", "audio-capture"].includes(e.error);
      if (fallback && canRecord) {
        switched = true; // the browser's recogniser is unusable here: use Haq's own voice service
        useServer.current = true;
        try {
          sessionStorage.setItem("haq.stt", "server");
        } catch {
          /* storage unavailable */
        }
        setNotice(t("mic.serverMode"));
        startRecording();
        return;
      }
      setState("idle");
      setError(
        e.error === "not-allowed" || e.error === "service-not-allowed"
          ? t("mic.denied")
          : e.error === "no-speech"
          ? t("mic.nospeech")
          : e.error === "audio-capture"
          ? t("mic.nomic")
          : t("mic.fail")
      );
    };
    r.onend = () => {
      if (!switched) setState((s) => (s === "listening" ? "idle" : s));
    };
    native.current = r;
    setState("listening");
    try {
      r.start();
    } catch {
      setState("idle");
      if (canRecord) startRecording();
      else setError(t("mic.fail"));
    }
  }, [Rec, code, canRecord, startRecording, t]);

  const start = useCallback(() => {
    setError("");
    setNotice("");
    base.current = (latest.current.value || "").trimEnd(); // what was typed before speaking
    if (Rec && !useServer.current) startNative();
    else if (canRecord) startRecording();
    else setError(t("mic.unsupported"));
  }, [Rec, canRecord, startNative, startRecording, t]);

  const stop = useCallback(() => {
    if (native.current) native.current.stop();
    if (recorder.current && recorder.current.state === "recording") recorder.current.stop();
  }, []);

  useEffect(
    () => () => {
      clearTimeout(timer.current);
      native.current && native.current.abort();
      if (recorder.current && recorder.current.state === "recording") recorder.current.stop();
      stream.current && stream.current.getTracks().forEach((tr) => tr.stop());
    },
    []
  );

  return {
    supported: !!Rec || canRecord,
    state,
    listening: state === "listening" || state === "recording",
    busy: state === "transcribing",
    error,
    notice,
    start,
    stop,
  };
}

/* ---------- tiny hash router (no collisions with the API paths) ---------- */
export function useRoute() {
  const read = () => window.location.hash.replace(/^#/, "") || "/";
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => {
      setRoute(read());
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export const go = (path) => {
  window.location.hash = path;
};

/* ---------- text size (accessibility) ---------- */
const SIZES = [0.9, 1, 1.15, 1.3, 1.5];

export function useTextSize() {
  const [step, setStep] = useState(() => {
    try {
      const raw = localStorage.getItem("haq.size");
      const v = raw === null ? 1 : Number(raw);          // nothing saved yet: normal size
      return Number.isInteger(v) && v >= 0 && v < SIZES.length ? v : 1;
    } catch {
      return 1;
    }
  });
  useEffect(() => {
    document.documentElement.style.fontSize = SIZES[step] * 100 + "%";
    try {
      localStorage.setItem("haq.size", String(step));
    } catch {
      /* private mode */
    }
  }, [step]);
  return { step, max: SIZES.length - 1, bigger: () => setStep((x) => Math.min(x + 1, SIZES.length - 1)), smaller: () => setStep((x) => Math.max(x - 1, 0)) };
}

/* ---------- high-contrast mode (accessibility) ---------- */
export function useContrast() {
  const [on, setOn] = useState(() => {
    try {
      return localStorage.getItem("haq.contrast") === "1";
    } catch {
      return false;
    }
  });
  useEffect(() => {
    if (on) document.documentElement.setAttribute("data-contrast", "high");
    else document.documentElement.removeAttribute("data-contrast");
    try {
      localStorage.setItem("haq.contrast", on ? "1" : "0");
    } catch {
      /* private mode */
    }
  }, [on]);
  return { on, toggle: () => setOn((x) => !x) };
}
