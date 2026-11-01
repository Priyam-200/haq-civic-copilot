"""Run the real app with a stand-in AI, for clicking through the UI without an API key.
    python tests/serve_fake.py        (then open http://localhost:8000)
Answers are canned - this is for UI testing only, never for the demo or deployment."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import conftest  # noqa: E402  (sets a temp DATA_DIR)
import agent.graph, llm.client, rag.explain, rag.qa, rag.store  # noqa: E402
import uvicorn  # noqa: E402

llm.client.client = object()
rag.store.embed = conftest.fake_embed
for m in (rag.qa, rag.explain, agent.graph):
    m.ask_llm = conftest.fake_llm

from main import app  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
