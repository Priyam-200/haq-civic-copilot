import re
import db
from pydantic import BaseModel, model_validator
from llm.client import ask_llm
from rag.store import get_all, search
from rag.qa import _parse_json

# What each audience needs from the same document (field -> instruction for the model)
AUDIENCES = {
    "citizen": {
        "intro": "ordinary citizens",
        "fields": {
            "what_is_this": "what this document is and why it exists",
            "who_is_it_for": "who it affects or helps",
            "rights_benefits": "the rights, powers or benefits it gives",
            "eligibility": "conditions or rules about who qualifies",
            "documents_needed": "documents a person must provide",
            "deadlines": "dates and time limits",
            "real_world_impact": "what this means in practice for a citizen (only what the sources support)",
        },
    },
    "business": {
        "intro": "owners of small businesses and shops",
        "fields": {
            "what_is_this": "what this document is and why it exists",
            "who_is_it_for": "which businesses or people it applies to",
            "obligations": "what a business MUST do (registrations, filings, records, standards)",
            "penalties": "fines, punishments or consequences of not following the rules",
            "deadlines": "dates and time limits a business must meet",
            "documents_needed": "documents, licences or certificates a business must have or provide",
            "rights_benefits": "benefits, support, exemptions or rights available to a business",
            "real_world_impact": "what this means in practice for running a small business (only what the sources support)",
        },
    },
}
FIELDS = list(AUDIENCES["citizen"]["fields"])          # kept for compatibility

TOPICS = ["purpose and objective of this document", "who can benefit or apply",
          "rights, benefits and powers", "eligibility conditions",
          "documents required", "deadlines and time limits", "penalties and impact",
          "obligations and compliance requirements"]


class Point(BaseModel):
    point: str
    cited_ids: list[str] = []

    @model_validator(mode="before")
    @classmethod
    def _shape(cls, v):
        """Models sometimes return "text" or {"text": ..., "sources": [...]} instead of the asked shape."""
        if isinstance(v, str):
            return {"point": v, "cited_ids": []}
        if isinstance(v, dict):
            point = next((v[k] for k in ("point", "item", "text", "description") if isinstance(v.get(k), str)), "")
            ids = next((v[k] for k in ("cited_ids", "citations", "sources", "source_ids") if k in v), [])
            if isinstance(ids, str):
                ids = [ids]
            return {"point": point, "cited_ids": [str(i) for i in ids] if isinstance(ids, list) else []}
        return v


class Explainer(BaseModel):
    what_is_this: list[Point] = []
    who_is_it_for: list[Point] = []
    rights_benefits: list[Point] = []
    eligibility: list[Point] = []
    documents_needed: list[Point] = []
    deadlines: list[Point] = []
    real_world_impact: list[Point] = []
    obligations: list[Point] = []
    penalties: list[Point] = []


def _key(item):
    return (item["page"], int(item["chunk_id"].rsplit("_c", 1)[1]))


def _load_chunks(doc_id):
    res = get_all(doc_id)
    items = [{"chunk_id": i, "page": m["page"], "clause": m["clause"], "text": d}
             for i, d, m in zip(res["ids"], res["documents"], res["metadatas"])]
    if sum(len(c["text"].split()) for c in items) <= 9000:
        return sorted(items, key=_key)            # small document: use all of it

    picked = {}                                    # big document: pick by topic
    for t in TOPICS:
        r = search(doc_id, t, k=5)
        for i, d, m in zip(r["ids"][0], r["documents"][0], r["metadatas"][0]):
            picked[i] = {"chunk_id": i, "page": m["page"], "clause": m["clause"], "text": d}
    return sorted(picked.values(), key=_key)


def _snippet(text, point, size=300):
    if len(text) <= size:
        return text
    words = set(re.findall(r"\w{4,}", point.lower()))      # any script, not just English
    best, best_score = 0, -1
    for start in range(0, len(text) - size + 60, 60):
        window = text[start:start + size].lower()
        score = sum(1 for w in words if w in window)
        if score > best_score:
            best, best_score = start, score
    piece = text[best:best + size].strip()
    return ("..." if best > 0 else "") + piece + ("..." if best + size < len(text) else "")


def explain_document(doc_id: str, language: str = "English", audience: str = "citizen"):
    audience = audience if audience in AUDIENCES else "citizen"
    spec = AUDIENCES[audience]
    cache_key = language if audience == "citizen" else f"{language}|{audience}"
    cached = db.get_explain(doc_id, cache_key)     # saved on disk, so free calls are not wasted
    if cached:
        return cached

    chunks = _load_chunks(doc_id)
    if not chunks:
        return None
    by_id = {c["chunk_id"]: c for c in chunks}
    context = "\n\n".join(
        f"[{c['chunk_id']}] (page {c['page']}, {c['clause']})\n{c['text']}" for c in chunks)

    field_lines = "\n".join(f"- {k}: {v}" for k, v in spec["fields"].items())
    shape = ", ".join(f'"{k}": []' for k in list(spec["fields"])[1:])
    prompt = f"""You explain ONE government document to {spec['intro']}.
Use ONLY the sources below. Never use outside knowledge. Never invent facts.
Write in {language}, in very simple everyday words and short sentences.
Keep numbers, dates, amounts and names exactly as in the sources.
Every point must have "cited_ids" with the source ids it came from.
If the sources have nothing for a field, return an empty list for it. Do NOT guess.
Fill "what_is_this" and "who_is_it_for" whenever the sources say what the document is or who it applies to. Give up to 6 points per field.

Fields:
{field_lines}

Return ONLY JSON in this shape (every point is an object with "point" and "cited_ids"):
{{"what_is_this": [{{"point": "...", "cited_ids": ["id"]}}], {shape}}}

SOURCES:
{context}"""

    parsed = None
    for attempt in range(2):
        raw = ""
        try:
            raw = ask_llm(prompt, json_mode=True)
            parsed = Explainer(**_parse_json(raw))
            break
        except Exception as e:
            print(f"[EXPLAIN] attempt {attempt + 1} failed: {e}")
            print(f"[EXPLAIN] raw: {raw[:300]!r}")
    if parsed is None:
        return {"error": "ai_busy", "doc_id": doc_id, "language": language,
                "audience": audience, "explainer": {}}

    out = {}
    for f in spec["fields"]:
        good = []
        for p in getattr(parsed, f):
            cites = [{**by_id[i], "text": _snippet(by_id[i]["text"], p.point)}
                     for i in p.cited_ids if i in by_id]
            if cites:                              # drop points with no real source
                good.append({"point": p.point, "citations": cites})
        out[f] = good

    result = {"doc_id": doc_id, "language": language, "audience": audience, "explainer": out}
    db.put_explain(doc_id, cache_key, result)
    return result
