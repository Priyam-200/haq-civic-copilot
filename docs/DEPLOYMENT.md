# Deploying Haq (get your live URL)

The Docker image serves the API **and** the built UI from one service, so you only deploy one thing.
The image was built and tested locally (API, UI, live feeds and OCR all worked in the container, and no `.env` is baked in).

## Before you deploy
1. Put the code on GitHub (public repo — the submission requires it):
   ```bash
   cd forge-project
   git init && git add . && git commit -m "Haq: civic rights and schemes copilot"
   git branch -M main
   git remote add origin https://github.com/<you>/<repo>.git
   git push -u origin main
   ```
   `.gitignore` already excludes `.env`, `node_modules`, `data/` and `dist/`. **Check that `backend/.env` is not in the commit** (`git ls-files | grep .env` should only show `.env.example`).
2. Have your Gemini API key ready. **Never commit it.** Set it as a secret in the host’s dashboard.

## Recommended: website on Vercel + AI backend on Render

**Why two hosts?** Vercel runs short, stateless functions with a read-only disk and a size limit of about 250 MB. Haq's backend
needs a persistent document database, OCR and speech libraries, and an agent that runs for 30–60 s, so it runs as a container on
Render. Vercel hosts the website (static files) and talks to the backend over HTTPS.

1. **Backend on Render** — render.com → **New → Blueprint** → choose this repository (it reads `render.yaml`).
   When asked for `GEMINI_API_KEY`, paste your own key (it stays in Render, never in GitHub). Deploy and copy the service URL,
   e.g. `https://haq-backend.onrender.com`. Check `<url>/health` shows `"ai_configured": true`.
2. **Website on Vercel** — vercel.com → **Add New → Project** → import this repository and set
   * **Root Directory:** `frontend` (Framework Preset: Vite is detected automatically)
   * **Environment Variable:** `VITE_API_URL` = the Render URL from step 1 (no trailing slash)

   Deploy. Open the Vercel URL; the page should load without a red "cannot reach the server" banner.
3. After changing `VITE_API_URL` in Vercel, **redeploy** (it is baked in at build time).

Notes: Render's free plan sleeps after inactivity (the first request takes about a minute to wake it) and its disk is wiped on
restart, so re-import your demo documents shortly before judging. The same image also serves the full website, so the Render URL
alone is a complete fallback if Vercel is unavailable.

## Option A (one host, no Vercel) — Render (easiest)
1. render.com → **New → Web Service** → connect the GitHub repo.
2. Runtime: **Docker** (it finds the `Dockerfile` in the repo root). Instance: free or Starter.
3. Environment variables:
   * `GEMINI_API_KEY` = your key
   * `GEMINI_MODEL` = `gemini-3.8-flash`
   * `GEMINI_FALLBACK_MODELS` = `gemini-3.6-flash,gemini-3.5-flash-lite,gemini-3-flash-preview,gemini-3.1-flash-lite,gemini-flash-lite-latest`
4. Health check path: `/health`. Deploy. Your URL is `https://<name>.onrender.com`.
5. The free tier sleeps after inactivity and the disk is wiped on restart. **Warm it up and re-import your demo documents about 10 minutes before judging**, or add a persistent disk mounted at `/app/backend/data` (paid).

## Option B — Hugging Face Spaces (Docker)
1. Create a Space → SDK **Docker** → link or push the repo.
2. In the Space’s `README.md` front matter set `app_port: 8000` (or set the `PORT` variable to `7860`; the container honours `PORT`).
3. Settings → **Secrets**: add `GEMINI_API_KEY` (and the variables above).

## Option C — Railway / Fly.io
Both detect the `Dockerfile`. Set the same environment variables; expose port 8000 (or use `PORT`).

## After deploying — smoke test (5 minutes)
Open the URL and check, in this order:
1. The page loads and there is **no** red “AI key is not set” banner.
2. **Documents → Latest from the Government** shows live items (PIB will say it does not allow automated access; that is expected).
3. Import one item → open it → the explainer appears → switch language to Hindi → it re-explains.
4. **Find schemes** → run the sample sentence from `DEMO_SCRIPT.md` → steps stream, results appear.
5. Ask a question that is **not** in the document → it must answer “Not found in the document.”

## Quota — important for demo day
The Gemini **free tier allows about 20 requests/day per model**. An agent run uses 5–7 calls, and each explainer/answer uses 1–2.
Rehearsals can use it up. Before the finale: enable billing on the key (a demo costs a few rupees), or put several models in
`GEMINI_FALLBACK_MODELS`. Results are cached on disk (explainers), which also saves calls.

**Voice quota:** server-side speech (used only when a device has no voice for the language) has a much smaller free
allowance — about **10 requests/day per speech model**. Spoken text is cached on disk, so repeats are free, and several speech
models are tried in turn. For the finale, enable billing, or install the Hindi/regional voices on the demo computer
(Windows: Settings → Time & language → Speech → Add voices) so the browser reads aloud for free.

## Troubleshooting
| Symptom | Cause / fix |
|---|---|
| “AI service is busy” | quota used up or network blip; wait, or add fallbacks / billing |
| Live panel empty | the host has no outbound internet to government sites; check logs |
| A link import says certificate could not be verified | the site’s certificate is broken; use the PDF upload instead |
| `pip install` “No such file … .tmp” on Windows | project path too long; move it to `C:\forge` |
