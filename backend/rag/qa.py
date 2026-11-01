import json
import re
import db
from llm.client import ask_llm
from rag.store import search, get_section

# Cosine distance: smaller = closer match. Tune this by testing.
NOT_FOUND_THRESHOLD = 0.5
MAX_CHUNKS = 16

NOT_FOUND = {
    "answer": "Not found in the document.",
    "found": False,
    "citations": [],
}


def _parse_json(text: str):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)  # grab the first {...} block
        if not m:
            raise
        return json.loads(m.group(0))


def _order_key(item):
    cid, c = item
    return (c["page"], int(cid.rsplit("_c", 1)[1]))


def _chunk(cid, text, meta):
    return {"chunk_id": cid, "doc_id": meta["doc_id"], "page": meta["page"],
            "clause": meta["clause"], "text": text}


def answer_question(doc_id, question: str, language: str = "English"):
    """doc_id: one id, a list of ids, or None to search the whole library."""
    res = search(doc_id, question, k=10)
    ids = res["ids"][0]
    if not ids:
        return {**NOT_FOUND, "best_distance": None}

    docs, metas, dists = res["documents"][0], res["metadatas"][0], res["distances"][0]
    best = min(dists)

    # Guardrail 1: weak match, so do not even call the LLM
    if best > NOT_FOUND_THRESHOLD:
        return {**NOT_FOUND, "best_distance": round(best, 3)}

    # Go through matches from closest to farthest.
    # For each match, also bring in the rest of its section.
    chosen = {}
    done_clauses = set()
    for cid, d, m in zip(ids, docs, metas):
        if len(chosen) >= MAX_CHUNKS:
            break
        chosen.setdefault(cid, _chunk(cid, d, m))
        if (m["doc_id"], m["clause"]) in done_clauses:
            continue
        done_clauses.add((m["doc_id"], m["clause"]))
        sec = get_section(m["doc_id"], m["clause"])
        for scid, sd, sm in zip(sec["ids"], sec["documents"], sec["metadatas"]):
            if len(chosen) >= MAX_CHUNKS:
                break
            chosen.setdefault(scid, _chunk(scid, sd, sm))

    ordered = dict(sorted(chosen.items(), key=_order_key))
    print("[ASK] sources sent:", list(ordered.keys()))

    titles = {}
    for c in ordered.values():
        d = db.get_document(c["doc_id"])
        c["doc_title"] = titles.setdefault(c["doc_id"], d["title"] if d else c["doc_id"])
    multi = len(titles) > 1
    context = "\n\n".join(
        f"[{c['chunk_id']}] ({c['doc_title'] + ', ' if multi else ''}page {c['page']}, {c['clause']})\n{c['text']}"
        for c in ordered.values()
    )

    prompt = f"""You answer questions about government documents.
Use ONLY the sources below. Do not use outside knowledge.
If the sources do not contain the answer, set "found" to false.

Write the answer in {language}, in very simple everyday words and short sentences.
Do not copy the legal sentences. Explain them. Use "-" for a list.
If the sources have a list, include ALL its items from start to end.
Keep numbers, dates and amounts exactly as in the sources.
If sources disagree, trust the operative parts of the law (a numbered Section, Article or Rule) over the
Statement of Objects, preamble or introduction, and briefly mention the difference.
In "cited_ids", put EVERY source id you used.
Never write source ids or chunk codes inside the answer text; they belong only in "cited_ids".

Return ONLY JSON in this exact shape:
{{"found": true or false, "answer": "...", "cited_ids": ["chunk id", ...]}}

SOURCES:
{context}

QUESTION: {question}"""

    data = None
    for attempt in range(2):  # one retry if the answer is broken
        raw = ""
        try:
            raw = ask_llm(prompt, json_mode=True)
            data = _parse_json(raw)
            break
        except Exception as e:
            print(f"[ASK] attempt {attempt + 1} failed: {e}")
            print(f"[ASK] raw model output: {raw[:500]!r}")

    # The AI service was busy or gave broken output (not the same as "not found")
    if data is None:
        return {
            "answer": "The AI service is busy right now. Please try again in a minute.",
            "found": False,
            "citations": [],
            "error": "ai_busy",
            "best_distance": round(best, 3),
        }

    # Guardrail 2: keep only citations that were really retrieved
    valid = [ordered[i] for i in data.get("cited_ids", []) if i in ordered]
    if not data.get("found") or not valid:
        return {**NOT_FOUND, "best_distance": round(best, 3)}

    return {
        "answer": data["answer"],
        "found": True,
        "citations": valid,
        "best_distance": round(best, 3),
    }