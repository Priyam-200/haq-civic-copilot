import { localizeServer, t } from "./i18n";

const BASE = import.meta.env.VITE_API_URL || "";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(BASE + path, options);
  } catch {
    throw new ApiError(t("err.network"), 0);
  }
  if (!res.ok) {
    let msg = t("err.generic", { status: res.status });
    try {
      const body = await res.json();
      if (typeof body.detail === "string") msg = localizeServer(body.detail);
      else if (Array.isArray(body.detail)) msg = t("err.check");
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(msg, res.status);
  }
  return res.json();
}

const json = (method, body) => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  health: () => request("/health"),
  languages: () => request("/languages"),
  documents: (language = "English") => request(`/documents?language=${encodeURIComponent(language)}`),
  document: (id, language = "English") => request(`/documents/${id}?language=${encodeURIComponent(language)}`),
  deleteDocument: (id) => request(`/documents/${id}`, { method: "DELETE" }),
  upload: (file) => {
    const form = new FormData();
    form.append("file", file);
    return request("/ingest", { method: "POST", body: form });
  },
  importUrl: (url, title) => request("/ingest/url", json("POST", { url, title })),
  catalog: () => request("/sources/catalog"),
  live: (source = "all", limit = 10, language = "English") =>
    request(`/sources/live?source=${source}&limit=${limit}&language=${encodeURIComponent(language)}`),
  explain: (doc_id, language, audience = "citizen") => request("/explain", json("POST", { doc_id, language, audience })),
  stt: (wav, language) => {
    const form = new FormData();
    form.append("file", wav, "voice.wav");
    form.append("language", language);
    return request("/stt", { method: "POST", body: form });
  },
  tts: async (text, language, signal) => {
    let res;
    try {
      res = await fetch(BASE + "/tts", { ...json("POST", { text, language }), signal });
    } catch (e) {
      if (e.name === "AbortError") throw e;
      throw new ApiError(t("err.voiceNet"), 0);
    }
    if (!res.ok) {
      let msg = t("err.voiceFail", { status: res.status });
      try {
        const b = await res.json();
        if (typeof b.detail === "string") msg = localizeServer(b.detail);
      } catch {
        /* keep generic */
      }
      throw new ApiError(msg, res.status);
    }
    return res.blob();
  },
  ask: (doc_id, question, language) => request("/ask", json("POST", { doc_id, question, language })),
};

/**
 * Run the eligibility agent. The server streams events (step / needs_info / result / error);
 * `onEvent` is called for each one as it arrives. Resolves when the stream ends.
 */
export async function runAgent(body, onEvent, signal) {
  let res;
  try {
    res = await fetch(BASE + "/agent/run", { ...json("POST", body), signal });
  } catch (e) {
    if (e.name === "AbortError") return;
    throw new ApiError(t("err.network"), 0);
  }
  if (!res.ok) {
    let msg = t("err.generic", { status: res.status });
    try {
      const b = await res.json();
      if (typeof b.detail === "string") msg = localizeServer(b.detail);
    } catch {
      /* keep generic */
    }
    throw new ApiError(msg, res.status);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let i;
      while ((i = buffer.indexOf("\n\n")) >= 0) {
        const chunk = buffer.slice(0, i);
        buffer = buffer.slice(i + 2);
        if (chunk.startsWith("data: ")) {
          try {
            onEvent(JSON.parse(chunk.slice(6)));
          } catch {
            /* ignore a malformed event */
          }
        }
      }
    }
  } catch (e) {
    if (e.name !== "AbortError") throw new ApiError(t("err.dropped"), 0);
  }
}
