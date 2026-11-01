import json
import os
import sqlite3
import threading
import time

from config import DATA_DIR

_PATH = os.path.join(DATA_DIR, "forge.db")
_lock = threading.Lock()


def _conn():
    """Open the database, creating the file and tables if they are missing (self-healing,
    so a deleted forge.db never leaves the running app broken)."""
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    c = sqlite3.connect(_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS documents (
        doc_id TEXT PRIMARY KEY, title TEXT, source_type TEXT, source_url TEXT,
        page_count INTEGER, chunk_count INTEGER, status TEXT, created_at REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS title_cache (
        text TEXT, language TEXT, translated TEXT, PRIMARY KEY (text, language))""")
    c.execute("""CREATE TABLE IF NOT EXISTS explain_cache (
        doc_id TEXT, language TEXT, result TEXT, PRIMARY KEY (doc_id, language))""")
    return c


def init():
    with _lock, _conn():
        pass


def add_document(doc_id, title, source_type, source_url, page_count, chunk_count):
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?,?,?)",
                  (doc_id, title, source_type, source_url, page_count, chunk_count,
                   "ready", time.time()))


def get_document(doc_id):
    with _lock, _conn() as c:
        r = c.execute("SELECT * FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
    return dict(r) if r else None


def list_documents():
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM documents ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def find_by_url(url):
    with _lock, _conn() as c:
        r = c.execute("SELECT * FROM documents WHERE source_url=?", (url,)).fetchone()
    return dict(r) if r else None


def delete_document(doc_id):
    with _lock, _conn() as c:
        c.execute("DELETE FROM documents WHERE doc_id=?", (doc_id,))
        c.execute("DELETE FROM explain_cache WHERE doc_id=?", (doc_id,))


def get_explain(doc_id, language):
    with _lock, _conn() as c:
        r = c.execute("SELECT result FROM explain_cache WHERE doc_id=? AND language=?",
                      (doc_id, language)).fetchone()
    return json.loads(r["result"]) if r else None


def put_explain(doc_id, language, result):
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO explain_cache VALUES (?,?,?)",
                  (doc_id, language, json.dumps(result, ensure_ascii=False)))


def get_translations(language, texts):
    """{text: translation} for the texts already translated into `language`."""
    out = {}
    with _lock, _conn() as c:
        for t in texts:
            r = c.execute("SELECT translated FROM title_cache WHERE text=? AND language=?", (t, language)).fetchone()
            if r:
                out[t] = r["translated"]
    return out


def put_translations(language, mapping):
    with _lock, _conn() as c:
        c.executemany("INSERT OR REPLACE INTO title_cache VALUES (?,?,?)",
                      [(t, language, v) for t, v in mapping.items()])
