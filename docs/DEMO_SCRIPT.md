# Demo script (2–3 minutes, recorded live — no mock-ups)

The judges disqualify pre-recorded or fake demos, so **record the screen while using the real deployed app.**
Everything below was run against the real AI and real government sources.

## Prepare (10 minutes before)
* Open the deployed URL, wait for it to wake up, and confirm there is no red banner.
* In **Documents**, import 2–3 real items so the library is not empty:
  * the **PM-KISAN Operational Guidelines** — paste this link into “Import from a link”:
    `https://pmkisan.gov.in/Documents/RevisedPM-KISANOperationalGuidelines(English).pdf`
  * one or two items from the **live** panel (e.g. a MyGov campaign).
* Make sure the Gemini quota is not exhausted (see `DEPLOYMENT.md`).

## The story (say this as you click)
| Time | What you do | What to say |
|---|---|---|
| 0:00 | Show **Find schemes** | “Millions of people miss schemes they qualify for because the rules are buried in legal PDFs in a language they may not read.” |
| 0:15 | **Documents** → show the **live** panel | “Haq reads official government feeds live. PIB blocks automated access, and we show that honestly instead of hiding it.” |
| 0:30 | Import a live item → it opens → **Explained simply** | “One click and it is explained in simple words. Every point has a source — open it to see the exact words.” |
| 0:50 | Click **🏪 Small business** | “Same document, now for a shop owner — obligations, penalties and deadlines instead of personal rights.” (If the page has none, it says nothing rather than inventing.) |
| 0:55 | Switch language to **Hindi** (also try Tamil) | “Same document, now in Hindi. It can also read it aloud.” Click **Listen**. |
| 1:15 | **Ask a question** → “Who is eligible?” then a **question that is not in the document** (e.g. “What is the recipe for chocolate cake?”) | “It answers only from the document. When the answer is not there, it says so — no hallucination.” |
| 1:40 | **Find schemes** → type or speak: *“I am a 34 year old farmer in Bihar, OBC category, my family earns about 1.5 lakh a year.”* → **Check** | “Now the agent.” Point at the steps as they stream. |
| 1:55 | While steps run, narrate: profile → plan → live sources → retrieve → evaluate each document → verify | “It builds my profile, plans searches, reads live sources, checks every rule against my details, then verifies — dropping anything without a source and recomputing the verdict itself.” |
| 2:20 | Open the result: **Likely qualify**, criteria with ✔ / ✖ / ? and **Documents to keep ready** | “A rule it cannot confirm is marked unknown, so it says ‘likely’, never overpromises. And I get my document checklist.” |
| 2:35 | Click **💬 WhatsApp** on the results | “And it can be sent straight to a family member on WhatsApp.” |
| 2:40 | Close | “Haq: your rights and schemes, in your language — with proof for every answer.” |

## Tick the checkbox
On **Find schemes** keep “Fetch official scheme documents and today’s live announcements” ticked. It is what lets the agent fetch PM-KISAN, MUDRA, PMAY and others by itself. Unticked, it only searches what is already in your library.

## Extra moments (10 seconds each)
* Click **⬇ Audio** on an explainer: it saves a voice note you can forward on WhatsApp.
* Click **A+** in the top bar: the whole app gets larger for older or low-vision users.

## Show these on purpose (they score under *robustness*)
* **Thin input**: type only “I am a farmer” → the agent **asks follow-up questions** instead of guessing.
* **Off-topic question** → “Not found in the document.”
* **Scanned PDF** → upload a scan; it is read with OCR (works in the Docker image).
* **A blocked/unavailable source** → the app keeps working and reports it.

## Questions judges may ask (and the honest answers)
* *“Is any data hard-coded?”* No. All content comes from uploaded files, links or live feeds, processed by the AI at request time. The only canned answers are in `backend/tests/`, used for offline unit tests and never in the app.
* *“How do you prevent hallucination?”* Distance threshold before the LLM is called; citations must be real retrieved chunk ids; uncited statements are dropped (or grounded against the source text); the agent recomputes the verdict from the evidence.
* *“What if the model returns bad JSON / is busy?”* One self-correction retry with the error shown to the model, automatic model fallbacks, then a clear message.
* *“How accurate is it?”* On two real documents: 8/8 correct, 8/8 cited, 6/6 refused (`backend/eval/run_eval.py`). It is a small set, and we say so.
* *“Why not scrape PIB?”* PIB refuses automated clients. We respect that and use sources that allow access.
* *“What is not covered?”* PIB; eligibility is only as good as the document text; it is not legal advice.

## Every teammate should be able to explain (judges will quiz you)
1. Upload → `ingestion/pdf.py` (OCR fallback) → `ingestion/chunker.py` (sections, chapters) → embeddings → ChromaDB.
2. Question → `rag/qa.py`: search, threshold guard, section expansion, JSON answer, citation check.
3. Agent → `agent/graph.py`: the seven steps and the guardrails in `n_verify`.
4. Live data → `sources/feeds.py` + `ingestion/web.py` (private-address protection, OS certificate store).
5. Streaming → `/agent/run` sends Server-Sent Events; `frontend/src/api.js` reads them.
