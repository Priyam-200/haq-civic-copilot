import math
import os
import re
import chromadb
from config import DATA_DIR
from llm.client import embed

client = chromadb.PersistentClient(path=os.path.join(DATA_DIR, "chroma_db"))


def _col():
    return client.get_or_create_collection("docs", metadata={"hnsw:space": "cosine"})


def index_chunks(chunks):
    texts = [c["text"] for c in chunks]
    _col().add(
        ids=[c["chunk_id"] for c in chunks],
        documents=texts,
        embeddings=embed(texts),
        metadatas=[{"doc_id": c["doc_id"], "page": c["page"], "clause": c["clause"]} for c in chunks],
    )


def _where(doc_id):
    """doc_id can be one id, a list of ids, or None (search the whole library)."""
    if not doc_id:
        return None
    if isinstance(doc_id, (list, tuple, set)):
        ids = list(doc_id)
        return {"doc_id": ids[0]} if len(ids) == 1 else {"doc_id": {"$in": ids}}
    return {"doc_id": doc_id}


_STOP = set("the and for are what how many much does did who whom which when where with have has this that "
            "from can will shall not any all you your about into there their been were was its our may".split())


def _tokens(text):
    return [w for w in re.findall(r"\w{3,}", text.lower()) if w not in _STOP]


def _bm25(query_tokens, docs, k1=1.5, b=0.75):
    """BM25 scores of `docs` for the query; idf is computed over the candidate set."""
    toks = [_tokens(d) for d in docs]
    n = len(docs)
    avg = (sum(len(t) for t in toks) / n) or 1
    scores = []
    for t in toks:
        sc = 0.0
        for q in set(query_tokens):
            tf = t.count(q)
            if not tf:
                continue
            df = sum(1 for x in toks if q in x)
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            sc += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(t) / avg))
        scores.append(sc)
    return scores


def search(doc_id, question, k=6):
    """Hybrid retrieval: semantic search for a wide candidate set, then fuse its ranking with a
    keyword (BM25) ranking (reciprocal rank fusion). Pure semantic search can bury the one section
    that literally answers a short question; exact words fix that. `distances` stay the semantic
    cosine distances, so the 'not found' threshold keeps its meaning."""
    col = _col()
    n = col.count()
    if n == 0:
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
    res = col.query(query_embeddings=embed([question]), n_results=min(max(k * 4, 30), n),
                    where=_where(doc_id))
    ids, docs = res["ids"][0], res["documents"][0]
    metas, dists = res["metadatas"][0], res["distances"][0]
    qt = _tokens(question)
    lex = _bm25(qt, docs) if qt and len(docs) > 1 else [0.0] * len(docs)
    if not any(lex):                                   # no shared words (e.g. a Hindi question): semantic only
        order = list(range(len(ids)))
    else:
        lex_rank = {i: r for r, i in enumerate(sorted(range(len(docs)), key=lambda i: -lex[i]))}
        fused = [(1 / (60 + i)) + (1 / (60 + lex_rank[i]) if lex[i] > 0 else 0) for i in range(len(ids))]
        order = sorted(range(len(ids)), key=lambda i: -fused[i])
    keep = order[:k]
    return {"ids": [[ids[i] for i in keep]], "documents": [[docs[i] for i in keep]],
            "metadatas": [[metas[i] for i in keep]], "distances": [[dists[i] for i in keep]]}


def get_section(doc_id, clause):
    return _col().get(
        where={"$and": [{"doc_id": doc_id}, {"clause": clause}]}
    )


def get_all(doc_id):
    return _col().get(where={"doc_id": doc_id})


def delete_doc(doc_id):
    _col().delete(where={"doc_id": doc_id})
