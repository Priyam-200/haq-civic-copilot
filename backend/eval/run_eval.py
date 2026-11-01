"""Measure answer quality against a RUNNING backend, using real documents and the real AI.

    python eval/run_eval.py            (backend must be running on http://127.0.0.1:8000)

Checks, for each question:
  * in-scope questions  -> must be answered (found), must cite a source, and the answer must
                           contain the expected fact (a keyword from the official text)
  * out-of-scope ones   -> must be refused ("Not found in the document"), never answered
Prints a table and the totals. Each in-scope question costs one AI call, so run it sparingly
on the free tier."""
import os
import sys
import time

import httpx

BASE = os.getenv("HAQ_API", "http://127.0.0.1:8000")
HERE = os.path.dirname(__file__)
NCW_PDF = os.path.join(HERE, "..", "a1990-20.pdf")
KISAN_URL = "https://pmkisan.gov.in/Documents/RevisedPM-KISANOperationalGuidelines(English).pdf"

# (document, question, expected keywords - ANY of them must appear in the answer)
IN_SCOPE = [
    ("ncw", "How many members does the Commission have?", ["five", "5"]),
    ("ncw", "Who nominates the members of the Commission?", ["central government"]),
    ("ncw", "What are the functions of the Commission?", ["safeguard", "investigat", "complaint", "law"]),
    ("ncw", "Does the Commission have the powers of a civil court?", ["civil court", "summon", "yes"]),
    ("kisan", "How much money do eligible farmers get per year?", ["6000", "6,000"]),
    ("kisan", "How is the money paid?", ["installment", "instalment", "three", "2000", "2,000"]),
    ("kisan", "Is Aadhaar needed?", ["aadhaar"]),
    ("kisan", "Who runs the identification of beneficiaries?", ["state", "ut", "union territory"]),
]
OUT_OF_SCOPE = [
    ("ncw", "What is the recipe for chocolate cake?"),
    ("ncw", "Who won the cricket world cup?"),
    ("ncw", "Explain quantum computing."),
    ("kisan", "What is the weather in Delhi today?"),
    ("kisan", "How do I reset my phone?"),
    ("kisan", "Best places to visit in Goa?"),
]


def need_docs(c):
    docs = {}
    for d in c.get("/documents").json():
        if d["source_url"] == KISAN_URL:
            docs["kisan"] = d["doc_id"]
        if d["title"] == "a1990-20":
            docs["ncw"] = d["doc_id"]
    if "ncw" not in docs:
        with open(NCW_PDF, "rb") as f:
            docs["ncw"] = c.post("/ingest", files={"file": ("a1990-20.pdf", f, "application/pdf")}).json()["doc_id"]
    if "kisan" not in docs:
        docs["kisan"] = c.post("/ingest/url", json={"url": KISAN_URL, "title": "PM-KISAN Guidelines"}).json()["doc_id"]
    return docs


def ask(c, doc, q):
    for attempt in range(3):
        r = c.post("/ask", json={"doc_id": doc, "question": q, "language": "English"})
        if r.status_code == 200 and r.json().get("error") != "ai_busy":
            return r.json()
        time.sleep(5)
    return {"found": None, "answer": f"(service busy: HTTP {r.status_code})", "citations": []}


def main():
    c = httpx.Client(base_url=BASE, timeout=180)
    try:
        c.get("/health").raise_for_status()
    except Exception:
        sys.exit(f"Backend not reachable at {BASE}. Start it first.")
    docs = need_docs(c)

    ok_in = cited = 0
    print("\nIN-SCOPE (must be answered, cited, and contain the expected fact)")
    for d, q, kws in IN_SCOPE:
        r = ask(c, docs[d], q)
        ans = (r.get("answer") or "").lower()
        hit = bool(r.get("found")) and any(k in ans for k in kws)
        has_cite = bool(r.get("citations"))
        ok_in += hit
        cited += bool(r.get("found")) and has_cite
        print(f"  {'PASS' if hit else 'FAIL'}  cited={has_cite!s:<5} dist={r.get('best_distance')}  {q}")

    refused = 0
    print("\nOUT-OF-SCOPE (must be refused)")
    for d, q in OUT_OF_SCOPE:
        r = ask(c, docs[d], q)
        good = r.get("found") is False and "not found" in (r.get("answer") or "").lower()
        refused += good
        print(f"  {'PASS' if good else 'FAIL'}  {q}")

    n_in, n_out = len(IN_SCOPE), len(OUT_OF_SCOPE)
    print(f"\nRESULT: answered correctly {ok_in}/{n_in} | every answer cited {cited}/{n_in} | "
          f"correctly refused {refused}/{n_out}")


if __name__ == "__main__":
    main()
