import json
import os
import time
from collections import defaultdict, deque

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
from agent.graph import Agent
from config import CORS_ORIGINS, MAX_UPLOAD_MB, RATE_LIMIT_PER_MIN
from ingestion.pdf import extract_pages
from ingestion.web import FetchError, load_url
from llm import client as llm
from llm.languages import LANGUAGES, normalize
from llm.stt import AudioError, MAX_BYTES, transcribe
from llm.translate import translate_texts
from llm.tts import MAX_CHARS, synthesize
from rag.explain import explain_document
from rag.qa import answer_question
from services.documents import IngestError, ingest_pages, remove
from sources import feeds
from sources.catalog import CATALOG

db.init()
app = FastAPI(title="Forge - Civic Rights & Schemes Copilot")
app.add_middleware(
    CORSMiddleware, allow_origins=CORS_ORIGINS,
    allow_methods=["*"], allow_headers=["*"],
)

# ---------- simple per-IP rate limit (protects the free AI quota) ----------
_hits = defaultdict(deque)
_LIMITED = ("/ask", "/explain", "/ingest", "/agent", "/tts", "/stt")


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if request.method == "POST" and request.url.path.startswith(_LIMITED):
        ip = request.client.host if request.client else "?"
        q, now = _hits[ip], time.time()
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= RATE_LIMIT_PER_MIN:
            return JSONResponse({"detail": "Too many requests. Please wait a minute."}, status_code=429)
        q.append(now)
    return await call_next(request)


@app.exception_handler(llm.LLMNotConfigured)
async def _not_configured(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=503)


@app.exception_handler(llm.LLMBusyError)
async def _busy(request, exc):
    return JSONResponse({"detail": "The AI service is busy right now. Please try again in a minute."},
                        status_code=503)


# ---------- meta ----------
@app.get("/health")
def health():
    return {"status": "ok", "ai_configured": llm.configured(),
            "documents": len(db.list_documents())}


@app.get("/languages")
def languages():
    return [{"name": n, "code": c} for n, c in LANGUAGES.items()]


# ---------- documents ----------
def _public(d):
    return {"doc_id": d["doc_id"], "title": d["title"], "source_type": d["source_type"],
            "source_url": d["source_url"], "page_count": d["page_count"],
            "chunk_count": d["chunk_count"], "status": d["status"], "created_at": d["created_at"]}


@app.get("/documents")
def documents(language: str = "English"):
    docs = db.list_documents()
    local = translate_texts([d["title"] for d in docs], normalize(language))
    return [{**_public(d), "title_local": t} for d, t in zip(docs, local)]


@app.get("/documents/{doc_id}")
def get_doc(doc_id: str, language: str = "English"):
    d = db.get_document(doc_id)
    if not d:
        raise HTTPException(404, "Document not found.")
    return {**_public(d), "title_local": translate_texts([d["title"]], normalize(language))[0]}


@app.delete("/documents/{doc_id}")
def delete_doc(doc_id: str):
    if not remove(doc_id):
        raise HTTPException(404, "Document not found.")
    return {"deleted": doc_id}


@app.post("/ingest")
async def ingest(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(400, f"File is too big (max {MAX_UPLOAD_MB} MB).")
    try:
        pages = extract_pages(data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(400, "Could not read this PDF.")
    return _ingest(pages, os.path.splitext(file.filename)[0], "upload", None)


def _ingest(pages, title, source_type, url):
    try:
        doc = ingest_pages(pages, title, source_type, url)
    except IngestError as e:
        raise HTTPException(400, str(e))
    except llm.LLMNotConfigured:
        raise
    except Exception as e:
        raise HTTPException(502, f"Embedding service failed: {e}")
    return {"doc_id": doc["doc_id"], "status": "ready", "title": doc["title"],
            "page_count": doc["page_count"], "chunk_count": doc["chunk_count"]}


class UrlIn(BaseModel):
    url: str
    title: str | None = None


@app.post("/ingest/url")
def ingest_url(body: UrlIn):
    """Import a government PDF or web page from a link."""
    url = body.url.strip()
    existing = db.find_by_url(url)
    if existing:
        return {"doc_id": existing["doc_id"], "status": "ready", "title": existing["title"],
                "page_count": existing["page_count"], "chunk_count": existing["chunk_count"],
                "already_imported": True}
    try:
        title, pages, final = load_url(url)
    except FetchError as e:
        raise HTTPException(400, str(e))
    return _ingest(pages, body.title or title, "web", url)


@app.get("/sources/catalog")
def source_catalog():
    """Official scheme documents that can be imported with one click (or fetched by the agent)."""
    out = []
    for c in CATALOG:
        d = db.find_by_url(c["url"])
        out.append({**c, "doc_id": d["doc_id"] if d else None})
    return out


@app.get("/sources/live")
def live_sources(source: str = "all", limit: int = 10, language: str = "English"):
    """Latest official announcements, live. `sources` says which feeds answered and which did not."""
    if source != "all" and source not in feeds.FEEDS:
        raise HTTPException(400, "Unknown source.")
    items, status = feeds.latest_all(per_source=max(1, min(limit, 30)), only=source)
    local = translate_texts([it["title"] for it in items], normalize(language))
    for it, t in zip(items, local):
        d = db.find_by_url(it["url"])
        it["doc_id"] = d["doc_id"] if d else None
        it["title_local"] = t
    return {"items": items, "sources": status}


# ---------- explain / ask ----------
class AskIn(BaseModel):
    doc_id: str | None = None      # None or "all" = search the whole library
    question: str = Field(max_length=1000)
    language: str = "English"


@app.post("/ask")
def ask(body: AskIn):
    if not body.question.strip():
        raise HTTPException(400, "Please type a question.")
    doc_id = None if body.doc_id in (None, "", "all") else body.doc_id
    if doc_id and not db.get_document(doc_id):
        raise HTTPException(404, "Document not found. Please upload it again.")
    try:
        return answer_question(doc_id, body.question, normalize(body.language))
    except llm.LLMNotConfigured:
        raise
    except Exception as e:
        raise HTTPException(502, f"Could not answer right now: {e}")


class ExplainIn(BaseModel):
    doc_id: str
    language: str = "English"
    audience: str = "citizen"        # "citizen" or "business"


@app.post("/explain")
def explain(body: ExplainIn):
    if not db.get_document(body.doc_id):
        raise HTTPException(404, "Document not found. Please upload it again.")
    result = explain_document(body.doc_id, normalize(body.language), body.audience)
    if result is None:
        raise HTTPException(404, "Document not found. Please upload it again.")
    return result


# ---------- speech ----------
class TtsIn(BaseModel):
    text: str = Field(max_length=MAX_CHARS)
    language: str = "English"


@app.post("/tts")
def tts(body: TtsIn):
    """Spoken audio (WAV) for a short text. Used when the device has no voice for the language."""
    if not body.text.strip():
        raise HTTPException(400, "Nothing to read.")
    wav = synthesize(body.text, normalize(body.language))
    return Response(content=wav, media_type="audio/wav", headers={"Cache-Control": "public, max-age=86400"})


@app.post("/stt")
async def stt(file: UploadFile = File(...), language: str = Form("English")):
    """Speech to text: a short WAV recording in, the spoken words out (used when the browser cannot do it)."""
    data = await file.read(MAX_BYTES + 1)
    try:
        text = await run_in_threadpool(transcribe, data, normalize(language))   # do not block other requests
    except AudioError as e:
        raise HTTPException(400, str(e))
    return {"text": text}


# ---------- agent ----------
class AgentIn(BaseModel):
    text: str = Field(default="", max_length=3000)     # the person's situation, in their words
    profile: dict = {}                                  # optional structured fields
    language: str = "English"
    doc_ids: list[str] | None = None
    live: bool = True                                   # also look at live PIB releases
    force: bool = False                                 # search even if the profile is thin


@app.post("/agent/run")
def agent_run(body: AgentIn):
    """Server-sent events: step / needs_info / result / error."""
    if not body.text.strip() and not body.profile:
        raise HTTPException(400, "Please describe your situation or fill in some details.")
    agent = Agent(body.text, body.profile, body.language, body.doc_ids, body.live, body.force)

    def stream():
        for ev in agent.run():
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- serve the built frontend (single-service deploy) ----------
_DIST = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(_DIST, "assets")), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = os.path.join(_DIST, path)
        if path and os.path.isfile(f) and os.path.abspath(f).startswith(os.path.abspath(_DIST)):
            return FileResponse(f)
        return FileResponse(os.path.join(_DIST, "index.html"))
