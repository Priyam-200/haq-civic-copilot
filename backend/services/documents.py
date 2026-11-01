import re
import uuid

import db
from ingestion.chunker import chunk_pages
from rag.store import index_chunks, delete_doc as _delete_vectors


class IngestError(ValueError):
    pass


def _relabel_unstructured(chunks):
    """Web pages have no 'Section 5' headings. Give each block its own label so that
    pulling 'the rest of the section' does not drag in the whole page."""
    if chunks and all(c["clause"] == "Preamble" for c in chunks):
        for i, c in enumerate(chunks, 1):
            c["clause"] = f"Part {i}"
    return chunks


def ingest_pages(pages, title, source_type, source_url=None):
    if not any(p["text"] for p in pages):
        raise IngestError("No readable text found. This may be a scanned PDF.")
    doc_id = uuid.uuid4().hex[:8]
    chunks = _relabel_unstructured(chunk_pages(doc_id, pages))
    if not chunks:
        raise IngestError("Could not split this document into sections.")
    index_chunks(chunks)
    title = re.sub(r"\s+", " ", title or "Untitled document").strip()[:140]
    db.add_document(doc_id, title, source_type, source_url, len(pages), len(chunks))
    return db.get_document(doc_id)


def remove(doc_id):
    if not db.get_document(doc_id):
        return False
    _delete_vectors(doc_id)
    db.delete_document(doc_id)
    return True
