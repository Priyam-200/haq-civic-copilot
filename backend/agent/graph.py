"""Scheme-eligibility agent.

A small state graph. Every node is a function that updates the shared state and reports
progress as events, so the UI can show the agent's reasoning live:

  profile -> plan -> live (optional) -> retrieve -> evaluate (per document) -> verify -> checklist

Hallucination guardrails live in `verify`: the verdict is recomputed from the per-criterion
results, and anything without a real retrieved source is dropped.
"""
import re
from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

import db
from ingestion.web import FetchError, load_url
from llm.client import LLMBusyError, LLMNotConfigured, ask_llm
from llm.languages import normalize
from llm.translate import translate_texts
from rag.explain import _snippet
from rag.qa import _parse_json
from rag.store import get_all, search
from services.documents import IngestError, ingest_pages
from sources import feeds
from sources.catalog import CATALOG

PROFILE_FIELDS = ["age", "gender", "state", "occupation", "annual_income_inr", "category",
                  "disability", "education", "is_student", "marital_status", "area", "notes"]
CORE_FIELDS = ["age", "state", "occupation", "annual_income_inr", "category"]
MIN_CORE = 3                       # how many core facts we need before searching
MAX_DOCS = 5
MIN_DOCS = 2
MAX_LIVE_IMPORTS = 3
MAX_CATALOG_IMPORTS = 3
DOC_DISTANCE_LIMIT = 0.6
SMALL_DOC_WORDS = 6000

FALLBACK_QUESTIONS = {
    "age": "How old are you?",
    "state": "Which state or union territory do you live in?",
    "occupation": "What work do you do (for example farmer, student, shopkeeper)?",
    "annual_income_inr": "What is your family's yearly income, roughly, in rupees?",
    "category": "Which category do you belong to (General, OBC, SC, ST, EWS)?",
}
ORDER = {"eligible": 0, "likely": 1, "unclear": 2, "not_eligible": 3}


class Crit(BaseModel):
    criterion: str
    status: Literal["met", "unmet", "unknown"] = "unknown"
    reason: str = ""
    cited_ids: list[str] = []
    needs_missing_info: bool = False      # deciding this needs a fact the citizen did not give

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, v):
        v = str(v or "").strip().lower()
        return v if v in ("met", "unmet", "unknown") else "unknown"

    @field_validator("needs_missing_info", mode="before")
    @classmethod
    def _flag(cls, v):
        return v if isinstance(v, bool) else str(v).strip().lower() in ("true", "yes", "1")

    @model_validator(mode="before")
    @classmethod
    def _shape(cls, v):
        if isinstance(v, str):
            return {"criterion": v}
        if isinstance(v, dict):
            v = dict(v)
            v.setdefault("criterion", v.get("condition") or v.get("rule") or v.get("text") or "")
            ids = next((v[k] for k in ("cited_ids", "citations", "sources", "source_ids") if k in v), [])
            v["cited_ids"] = _as_ids(ids)
        return v


def _as_ids(v):
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v] if isinstance(v, list) else []


class Item(BaseModel):
    point: str
    cited_ids: list[str] = []

    @model_validator(mode="before")
    @classmethod
    def _shape(cls, v):
        """Accept the shapes models actually produce: "text", {"item": ...}, {"text": ..., "sources": [...]}."""
        if isinstance(v, str):
            return {"point": v, "cited_ids": []}
        if isinstance(v, dict):
            point = next((v[k] for k in ("point", "item", "text", "document", "step", "description", "name")
                          if isinstance(v.get(k), str)), None)
            ids = next((v[k] for k in ("cited_ids", "citations", "sources", "source_ids") if k in v), [])
            return {"point": point if point is not None else "", "cited_ids": _as_ids(ids)}
        return v


class Eval(BaseModel):
    is_scheme: bool = True
    name: str = ""
    summary: str = ""
    verdict: str = "unclear"
    criteria: list[Crit] = []
    benefits: list[Item] = []
    documents_needed: list[Item] = []
    how_to_apply: list[Item] = []
    deadlines: list[Item] = []


def _clean_profile(p: dict) -> dict:
    out = {}
    for k in PROFILE_FIELDS:
        v = (p or {}).get(k)
        if v is None or v == "" or v == []:
            continue
        if k in ("age", "annual_income_inr"):
            try:
                v = int(float(str(v).replace(",", "")))
            except ValueError:
                continue
        elif k in ("disability", "is_student"):
            if isinstance(v, str):
                v = v.strip().lower() in ("true", "yes", "y", "1")
        else:
            v = str(v).strip()
        out[k] = v
    return out


def _missing_core(profile):
    return [f for f in CORE_FIELDS if f not in profile]


def _describe(profile):
    return "; ".join(f"{k}: {v}" for k, v in profile.items()) or "nothing known"


def _ground(text, by_id, min_overlap=0.6):
    """For a statement the model forgot to cite: find the retrieved chunk that really contains it.
    Only accepted when most of its content words appear in that chunk, otherwise it is dropped."""
    words = set(re.findall(r"[a-z0-9]{4,}", text.lower()))
    if len(words) < 4:
        return []
    best, score = None, 0.0
    for cid, c in by_id.items():
        have = set(re.findall(r"[a-z0-9]{4,}", c["text"].lower()))
        sc = len(words & have) / len(words)
        if sc > score:
            best, score = cid, sc
    return [best] if best and score >= min_overlap else []


class Agent:
    def __init__(self, text="", profile=None, language="English", doc_ids=None,
                 live=True, force=False):
        self.text = (text or "").strip()
        self.given = _clean_profile(profile or {})
        self.language = normalize(language)
        self.doc_ids = doc_ids or None
        self.live = live
        self.force = force
        self.profile = {}
        self.queries = []
        self.candidates = []          # [{doc_id, distance}]
        self.results = []
        self.warnings = []

    # ---------- helpers ----------
    @staticmethod
    def _step(id, title, status, detail=None, **extra):
        return {"type": "step", "id": id, "title": title, "status": status,
                "detail": detail, **extra}

    def _json(self, prompt, model_cls=None, attempts=2):
        """Ask for JSON. If the answer is broken, show the model its mistake and retry once."""
        err = ""
        for _ in range(attempts):
            raw = ""
            try:
                raw = ask_llm(prompt + (f"\n\nYour previous answer was invalid ({err}). "
                                        "Return ONLY valid JSON in the requested shape." if err else ""),
                              json_mode=True)
                data = _parse_json(raw)
                return model_cls(**data) if model_cls else data
            except (LLMBusyError, LLMNotConfigured):
                raise
            except Exception as e:
                err = str(e)[:200]
                print(f"[AGENT] bad JSON ({err}); raw={raw[:200]!r}")
        return None

    # ---------- nodes ----------
    def n_profile(self):
        yield self._step("profile", "Understanding your situation", "running")
        data = {}
        if self.text:
            prompt = f"""Extract facts about a person from their own words. Never guess a fact they did not say.
Return ONLY JSON: {{"profile": {{"age": number|null, "gender": string|null, "state": string|null,
"occupation": string|null, "annual_income_inr": number|null, "category": "General|OBC|SC|ST|EWS"|null,
"disability": true|false|null, "education": string|null, "is_student": true|false|null,
"marital_status": string|null, "area": "rural|urban"|null, "notes": string|null}}}}
Convert income to rupees per year (e.g. "2 lakh" = 200000). Use English for values.

PERSON'S WORDS:
{self.text}"""
            out = self._json(prompt)
            data = (out or {}).get("profile", {}) if isinstance(out, dict) else {}
            if out is None:
                self.warnings.append("Could not fully read the description; using only the form fields.")
        self.profile = {**_clean_profile(data), **self.given}   # form fields win

        missing = _missing_core(self.profile)
        if len(self.profile) and (len(CORE_FIELDS) - len(missing)) >= MIN_CORE or self.force:
            yield self._step("profile", "Understanding your situation", "done",
                             _describe(self.profile), profile=self.profile, code="profile_done")
            return True
        yield self._step("profile", "Understanding your situation", "needs_info",
                         "More details are needed to check eligibility.", profile=self.profile,
                         code="needs_info")
        questions = [{"field": f, "question": FALLBACK_QUESTIONS[f]} for f in missing]
        questions = self._translate_questions(questions)
        yield {"type": "needs_info", "profile": self.profile, "missing": missing,
               "questions": questions}
        return False

    def _translate_questions(self, questions):
        if self.language == "English" or not questions:
            return questions
        try:
            out = self._json(
                f"Translate each question into {self.language}, simple everyday words. "
                'Return ONLY JSON: {"questions": ["...", ...]} in the same order.\n'
                + "\n".join(q["question"] for q in questions))
            tr = (out or {}).get("questions", [])
            if len(tr) == len(questions):
                return [{**q, "question": t} for q, t in zip(questions, tr)]
        except (LLMBusyError, LLMNotConfigured):
            pass
        return questions

    def n_plan(self):
        yield self._step("plan", "Planning what to look for", "running")
        out = self._json(f"""A citizen in India has this profile: {_describe(self.profile)}.
Write 3 to 4 short English search queries to find government schemes, benefits, subsidies or
legal rights that may apply to them in a library of official documents.
Return ONLY JSON: {{"queries": ["...", "..."]}}""")
        qs = [q.strip() for q in (out or {}).get("queries", []) if isinstance(q, str) and q.strip()]
        if not qs:                                  # fall back to a rule-based plan
            base = " ".join(str(self.profile.get(k, "")) for k in ("occupation", "state")).strip()
            qs = [f"{base} scheme benefits eligibility".strip(),
                  "who is eligible to apply and documents required"]
            self.warnings.append("Planner was unavailable; used a basic search plan.")
        self.queries = qs[:4]
        # what the person sees is shown in their language (one cached call for the search terms and profile values)
        pv = [k for k in ("state", "occupation", "education", "marital_status") if isinstance(self.profile.get(k), str)]
        local = translate_texts(list(self.queries) + [self.profile[k] for k in pv], self.language)
        queries_local = local[:len(self.queries)]
        if self.language != "English" and pv:
            display = {k: local[len(self.queries) + i] for i, k in enumerate(pv)}
            yield self._step("profile", "Understanding your situation", "done", _describe(self.profile),
                             profile=self.profile, code="profile_done", profile_display=display)
        yield self._step("plan", "Planning what to look for", "done",
                         " • ".join(self.queries), tool="llm.plan", queries=self.queries,
                         queries_local=queries_local, code="plan_done")

    def n_catalog(self):
        """Tool use: fetch official scheme documents that fit this person, if we do not have them yet."""
        if not self.live:
            return
        title = "Looking up official scheme documents"
        yield self._step("catalog", title, "running")
        listing = "\n".join(f"{i}. {c['name']}" for i, c in enumerate(CATALOG))
        out = self._json(f"""Citizen profile: {_describe(self.profile)}.
Below are official government scheme documents we can read. Pick up to {MAX_CATALOG_IMPORTS} that this
citizen might qualify for or benefit from. Return ONLY JSON: {{"picks": [numbers]}}. Use an empty list if none fit.

{listing}""")
        picks = []
        for n in (out or {}).get("picks", []):
            if isinstance(n, int) and 0 <= n < len(CATALOG) and n not in picks:
                picks.append(n)
        got, names, keys = [], [], []
        for n in picks[:MAX_CATALOG_IMPORTS]:
            c = CATALOG[n]
            existing = db.find_by_url(c["url"])
            if existing:
                got.append(existing["doc_id"])
                names.append(c["name"].split(" (")[0])
                keys.append(c["key"])
                continue
            try:
                t, pages, final = load_url(c["url"])
                doc = ingest_pages(pages, c["name"].split(" (")[0], "web", c["url"])
                got.append(doc["doc_id"])
                names.append(c["name"].split(" (")[0])
                keys.append(c["key"])
            except (FetchError, IngestError, RuntimeError) as e:
                self.warnings.append(f"Could not read {c['name'].split(' (')[0]}: {e}")
        self.live_docs += got
        detail = ("Using: " + ", ".join(names) + ".") if names else "No catalog document looked relevant."
        yield self._step("catalog", title, "done", detail, tool="catalog.fetch", imported=got,
                         code="catalog_done" if names else "catalog_none",
                         params={"names": names, "keys": keys})

    def n_live(self):
        """Tool use: look at the latest official announcements and import the relevant ones."""
        if not self.live:
            return
        title = "Checking live official sources"
        yield self._step("live", title, "running")
        items, status = feeds.latest_all(per_source=10)
        ok = [s["label"] for s in status if s["ok"]]
        down = [s["label"] for s in status if not s["ok"]]
        if not items:
            msg = "No live source could be reached right now. " + " ".join(
                s.get("error", "") for s in status)
            self.warnings.append(msg.strip())
            yield self._step("live", title, "skipped", msg.strip(), code="live_none")
            return
        listing = "\n".join(f"{i}. [{it['source']}] {it['title']}" for i, it in enumerate(items))
        out = self._json(f"""Citizen profile: {_describe(self.profile)}.
Below are today's government announcement titles (any language). Pick up to {MAX_LIVE_IMPORTS}
that announce or explain a scheme, benefit, subsidy, rule or right this citizen could use.
Return ONLY JSON: {{"picks": [numbers]}}. Use an empty list if none fit.

{listing}""")
        picks = []
        for n in (out or {}).get("picks", []):
            if isinstance(n, int) and 0 <= n < len(items) and n not in picks:
                picks.append(n)
        picks = picks[:MAX_LIVE_IMPORTS]
        imported = []
        for n in picks:
            it = items[n]
            existing = db.find_by_url(it["url"])
            if existing:
                imported.append(existing["doc_id"])
                continue
            try:
                t, pages, final = load_url(it["url"])
                doc = ingest_pages(pages, it["title"] or t, "web", it["url"])
                imported.append(doc["doc_id"])
            except (FetchError, IngestError, RuntimeError) as e:
                self.warnings.append(f"Skipped an announcement: {e}")
        self.live_docs += imported
        detail = (f"Read {len(imported)} relevant item(s) out of {len(items)} latest." if imported
                  else f"Looked at {len(items)} latest items; none matched.")
        detail += f" Sources: {', '.join(ok)}." + (f" Unavailable: {', '.join(down)}." if down else "")
        yield self._step("live", title, "done", detail, tool="feeds.latest + fetch", imported=imported,
                         code="live_done" if imported else "live_nomatch",
                         params={"read": len(imported), "total": len(items), "ok": ok, "down": down,
                                 "ok_keys": [s["key"] for s in status if s["ok"]],
                                 "down_keys": [s["key"] for s in status if not s["ok"]]})

    def n_retrieve(self):
        yield self._step("retrieve", "Searching the document library", "running")
        best = {}
        for q in self.queries:
            res = search(self.doc_ids, q, k=8)
            for m, d in zip(res["metadatas"][0], res["distances"][0]):
                best[m["doc_id"]] = min(d, best.get(m["doc_id"], 9))
        for d in getattr(self, "live_docs", []):    # always evaluate what we just fetched
            if self.doc_ids is None or d in self.doc_ids:
                best.setdefault(d, DOC_DISTANCE_LIMIT)
        # close matches first; always keep the nearest MIN_DOCS so generic queries cannot
        # leave the agent empty-handed (the evaluate step decides if a document is relevant)
        everything = sorted(best.items(), key=lambda x: x[1])
        ranked = [x for i, x in enumerate(everything)
                  if x[1] <= DOC_DISTANCE_LIMIT or i < MIN_DOCS][:MAX_DOCS]
        self.candidates = [{"doc_id": d, "distance": round(v, 3)} for d, v in ranked]
        yield self._step("retrieve", "Searching the document library", "done",
                         f"{len(self.candidates)} document(s) look relevant.",
                         tool="vector.search", candidates=self.candidates, code="retrieve_done",
                         params={"n": len(self.candidates)})

    def _context(self, doc_id):
        res = get_all(doc_id)
        items = [{"chunk_id": i, "page": m["page"], "clause": m["clause"], "text": d}
                 for i, d, m in zip(res["ids"], res["documents"], res["metadatas"])]
        key = lambda c: (c["page"], int(c["chunk_id"].rsplit("_c", 1)[1]))
        if sum(len(c["text"].split()) for c in items) <= SMALL_DOC_WORDS:
            return sorted(items, key=key)
        picked = {}
        for t in ["who is eligible to apply", "benefits and rights", "documents required",
                  "how to apply and deadlines", _describe(self.profile)]:
            r = search(doc_id, t, k=5)
            for i, d, m in zip(r["ids"][0], r["documents"][0], r["metadatas"][0]):
                picked[i] = {"chunk_id": i, "page": m["page"], "clause": m["clause"], "text": d}
        return sorted(picked.values(), key=key)

    def _evaluate(self, doc, chunks):
        context = "\n\n".join(
            f"[{c['chunk_id']}] (page {c['page']}, {c['clause']})\n{c['text']}" for c in chunks)
        prompt = f"""You check whether ONE citizen qualifies for what ONE official document offers.
Use ONLY the sources below. Never use outside knowledge. Never invent rules, amounts or dates.
Write all human-readable text in {self.language}, in very simple everyday words.

CITIZEN: {_describe(self.profile)}

Steps: (1) if this document is not a scheme, benefit, subsidy, right or service a citizen can use,
set "is_scheme" to false. (2) List each eligibility condition in "criteria". Write every condition as
a POSITIVE requirement (for example "Does not pay income tax", not "Income tax payers are excluded"). Then
compare it with the citizen's known facts above and nothing else:
  - "met": the citizen's known facts clearly satisfy the requirement.
  - "unmet": the citizen's known facts clearly VIOLATE the requirement (for example the requirement is age
    over 60 and the citizen is 34). Never use "unmet" because information is missing.
  - "unknown": the requirement depends on something the citizen did not tell us (land owned, income tax paid,
    pension, government job, bank account, documents...). Do NOT guess or assume.
For each condition set "needs_missing_info" to true if deciding it needs a fact that is NOT in the
citizen's profile, otherwise false. If it is true the status MUST be "unknown". (3) Fill benefits, documents_needed,
how_to_apply (ordered steps) and deadlines only if the sources say so.
Every criterion and item needs "cited_ids" with the source ids it came from.
Keep numbers, dates and amounts exactly as in the sources.
"verdict" must be one of: eligible, likely, not_eligible, unclear.

Return ONLY JSON:
{{"is_scheme": true, "name": "scheme or document name", "summary": "one or two sentences",
"verdict": "likely", "criteria": [{{"criterion": "...", "status": "met", "reason": "...", "needs_missing_info": false, "cited_ids": ["id"]}}],
"benefits": [{{"point": "...", "cited_ids": ["id"]}}],
"documents_needed": [{{"point": "Aadhaar card", "cited_ids": ["id"]}}],
"how_to_apply": [{{"point": "Step 1 ...", "cited_ids": ["id"]}}],
"deadlines": [{{"point": "...", "cited_ids": ["id"]}}]}}
Every list item MUST be an object with "point" and "cited_ids", never a plain string.

SOURCES:
{context}"""
        return self._json(prompt, Eval)

    def n_evaluate(self):
        docs_ = [db.get_document(c["doc_id"]) for c in self.candidates]
        titles = [d["title"] for d in docs_ if d]
        local_title = dict(zip(titles, translate_texts(titles, self.language)))     # one cached call
        for cand in self.candidates:
            doc = db.get_document(cand["doc_id"])
            if not doc:
                continue
            sid = f"eval-{doc['doc_id']}"
            title = f"Checking: {doc['title'][:70]}"
            dl = local_title.get(doc["title"], doc["title"])
            yield self._step(sid, title, "running", doc_title=doc["title"], doc_title_local=dl)
            try:
                chunks = self._context(doc["doc_id"])
                ev = self._evaluate(doc, chunks) if chunks else None
            except (LLMBusyError, LLMNotConfigured):
                raise
            except Exception as e:
                ev = None
                print(f"[AGENT] evaluate failed for {doc['doc_id']}: {e}")
            if ev is None:
                self.warnings.append(f"Could not check “{doc['title'][:60]}”. Try again later.")
                yield self._step(sid, title, "skipped", "The AI could not read this document reliably.",
                                 doc_title=doc["title"], doc_title_local=dl, code="eval_skipped")
                continue
            self.results.append((doc, {c["chunk_id"]: c for c in chunks}, ev, cand["distance"]))
            yield self._step(sid, title, "done", f"{len(ev.criteria)} condition(s) compared.",
                             tool="llm.evaluate", doc_title=doc["title"], doc_title_local=dl, code="eval_done",
                             params={"n": len(ev.criteria)})

    def n_verify(self):
        yield self._step("verify", "Verifying against sources", "running")
        final, dropped, adjusted, guessed = [], 0, 0, 0
        for doc, by_id, ev, dist in self.results:
            def cites(ids, text):
                ids = [i for i in dict.fromkeys(ids) if i in by_id] or _ground(text, by_id)
                return [{**by_id[i], "text": _snippet(by_id[i]["text"], text)} for i in ids]

            criteria = []
            for c in ev.criteria:
                cc = cites(c.cited_ids, c.criterion + " " + c.reason)
                status = c.status
                if c.needs_missing_info and status != "unknown":
                    status = "unknown"      # the model itself says it lacks a fact: never turn that into met / unmet
                    guessed += 1
                if cc:
                    criteria.append({"criterion": c.criterion, "status": status,
                                     "reason": c.reason, "citations": cc})
                else:
                    dropped += 1

            def items(lst):
                nonlocal dropped
                out = []
                for it in lst:
                    cc = cites(it.cited_ids, it.point)
                    if cc:
                        out.append({"point": it.point, "citations": cc})
                    else:
                        dropped += 1
                return out

            benefits, docs_needed = items(ev.benefits), items(ev.documents_needed)
            how_to, deadlines = items(ev.how_to_apply), items(ev.deadlines)
            if not ev.is_scheme or not (criteria or benefits):
                continue

            # recompute the verdict from the evidence instead of trusting the model
            statuses = {c["status"] for c in criteria}
            if "unmet" in statuses:
                verdict = "not_eligible"
            elif not criteria:
                verdict = "unclear"
            elif "unknown" in statuses:
                verdict = "likely"
            else:
                verdict = "eligible"
            if verdict != ev.verdict:
                adjusted += 1
            final.append({
                "doc_id": doc["doc_id"], "title": doc["title"], "source_url": doc["source_url"],
                "source_type": doc["source_type"], "name": ev.name or doc["title"],
                "summary": ev.summary, "verdict": verdict, "criteria": criteria,
                "benefits": benefits, "documents_needed": docs_needed,
                "how_to_apply": how_to, "deadlines": deadlines, "distance": dist,
            })
        final.sort(key=lambda r: (ORDER[r["verdict"]], r["distance"]))
        for r, nm in zip(final, translate_texts([r["name"] for r in final], self.language)):
            r["name_local"] = nm                                                    # one cached call
        self.final = final
        yield self._step("verify", "Verifying against sources", "done",
                         f"Kept {len(final)} result(s); removed {dropped} unsupported statement(s); "
                         f"corrected {adjusted} verdict(s).", tool="guardrails", code="verify_done",
                         params={"kept": len(final), "dropped": dropped, "fixed": adjusted})

    def n_checklist(self):
        yield self._step("checklist", "Building your checklist", "running")
        seen, checklist = {}, []
        for r in self.final:
            if r["verdict"] not in ("eligible", "likely"):
                continue
            for d in r["documents_needed"]:
                key = re.sub(r"\W+", " ", d["point"].lower()).strip()
                if key in seen:
                    seen[key]["schemes"].append(r.get("name_local") or r["name"])
                else:
                    seen[key] = {"item": d["point"], "schemes": [r.get("name_local") or r["name"]]}
                    checklist.append(seen[key])
        self.checklist = checklist
        yield self._step("checklist", "Building your checklist", "done",
                         f"{len(checklist)} document(s) to keep ready.", code="checklist_done",
                         params={"n": len(checklist)})

    # ---------- driver ----------
    def run(self):
        """Generator of events. Never raises; failures become an `error` event."""
        try:
            gen = self.n_profile()
            ok = True
            try:
                while True:
                    yield next(gen)
            except StopIteration as stop:
                ok = stop.value
            if not ok:
                return
            self.live_docs = []
            for node in (self.n_plan, self.n_catalog, self.n_live, self.n_retrieve, self.n_evaluate,
                         self.n_verify, self.n_checklist):
                yield from node()
            yield {"type": "result", "language": self.language, "profile": self.profile,
                   "results": self.final, "checklist": self.checklist,
                   "warnings": self.warnings,
                   "message": None if self.final else
                   "No matching scheme was found in the library. Try importing more documents.",
                   "message_code": None if self.final else "no_match"}
        except LLMNotConfigured as e:
            yield {"type": "error", "code": "not_configured", "message": str(e)}
        except LLMBusyError:
            yield {"type": "error", "code": "ai_busy",
                   "message": "The AI service is busy right now. Please try again in a minute."}
        except Exception as e:
            print(f"[AGENT] crashed: {e!r}")
            yield {"type": "error", "code": "internal",
                   "message": "Something went wrong while checking. Please try again."}
