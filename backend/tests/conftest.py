"""Test doubles for the AI. They exist ONLY here, so tests run offline and free.
The real app never uses them."""
import hashlib
import json
import math
import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="forge-test-")
os.environ.setdefault("GEMINI_API_KEY", "test-key")


def fake_embed(texts):
    out = []
    for t in texts:
        v = [0.0] * 128
        for w in re.findall(r"[a-z]{3,}", t.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 128] += 1
        n = math.sqrt(sum(x * x for x in v)) or 1
        out.append([x / n for x in v])
    return out


def first_id(prompt):
    m = re.search(r"SOURCES:\s*\[([^\]]+)\]", prompt)
    return m.group(1) if m else "nope"


def fake_llm(prompt, rounds=3, json_mode=False):
    if "Extract facts about a person" in prompt:
        return json.dumps({"profile": {"age": 30, "state": "Bihar", "occupation": "farmer",
                                       "annual_income_inr": 150000, "category": "OBC"}})
    if "search queries" in prompt:
        return json.dumps({"queries": ["women commission functions", "complaints and powers"]})
    if "official government scheme documents" in prompt:
        return json.dumps({"picks": []})
    if "announcement titles" in prompt:
        return json.dumps({"picks": []})
    if "You check whether ONE citizen" in prompt:
        cid = first_id(prompt)
        return json.dumps({
            "is_scheme": True, "name": "Test Scheme", "summary": "A test.", "verdict": "eligible",
            "criteria": [
                {"criterion": "Must be a woman", "status": "unknown", "reason": "not stated", "cited_ids": [cid]},
                {"criterion": "Made up rule", "status": "met", "reason": "x", "cited_ids": ["bogus_id"]}],
            "benefits": [{"point": "Gets help", "cited_ids": [cid]}, {"point": "Invented", "cited_ids": []}],
            "documents_needed": [{"point": "Aadhaar card", "cited_ids": [cid]}],
            "how_to_apply": [], "deadlines": []})
    if "You answer questions" in prompt:
        return json.dumps({"found": True, "answer": "- Test answer", "cited_ids": [first_id(prompt)]})
    if "You explain ONE" in prompt:
        cid = first_id(prompt)
        return json.dumps({"what_is_this": [{"point": "A law", "cited_ids": [cid]}]})
    return "{}"


@pytest.fixture(autouse=True)
def patch_ai(monkeypatch):
    import llm.client
    import rag.store, rag.qa, rag.explain, agent.graph
    monkeypatch.setattr(llm.client, "client", object())     # "configured"
    monkeypatch.setattr(rag.store, "embed", fake_embed)
    for mod in (rag.qa, rag.explain, agent.graph):
        monkeypatch.setattr(mod, "ask_llm", fake_llm)
