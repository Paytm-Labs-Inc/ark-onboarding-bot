# ark-onboarding-bot

A **RAG chatbot that answers Ark onboarding questions** — grounded in the Ark onboarding docs + FAQ, with citations and an honest "not in the docs" when it doesn't know.

## What it does

Ask it "how do I enroll a host?" or "how do I set up Cursor?" → it retrieves relevant doc chunks and the **Cursor agent** answers **grounded in those chunks**, linking the source. No hallucinating.

## Architecture (v1)

`ingest docs → chunk → embed → retrieve top-k → Cursor generates a grounded, cited answer → CLI (web UI stretch)`

## Knowledge sources

- Onboarding docs: https://foundry.mypaytm.com/onboarding/
- FAQ doc: https://docs.google.com/document/d/1cFO96__cGuADEFvR_ahHcc0ILmYWvIrodjwMguihVbY/edit

## Layout

- `data/` — ingested onboarding + FAQ corpus (one file per source)
- `ingest/` — ingestion pipeline (Keerthi)
- `src/` — retrieval, answer layer, and CLI
- `tests/` — unit tests + eval harness

---

## How to run the bot

### 1. Clone and set up Python

```bash
git clone git@github.com:Paytm-Labs-Inc/ark-onboarding-bot.git
cd ark-onboarding-bot

python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
pip install --default-timeout=600 -r requirements.txt
```

If `pip install` times out on a slow network, install in steps:

```bash
pip install --default-timeout=300 numpy html2text python-dotenv
pip install --default-timeout=600 "sentence-transformers>=3.0,<4.0"
```

### 2. Set your answer-generation key

Answers are generated through the **Pi Inference** gateway by default — a plain
OpenAI-compatible completion call, no agent harness. Copy the example env file
and set your key:

```bash
cp .env.example .env
# edit .env → PI_API_KEY=...
```

Defaults are `ANSWER_BACKEND=pi` and `PI_MODEL=qwen/qwen3-32b`. Keys are read from
the environment, never passed on the command line.

> The gateway host is `api.inference.paytm.com`. `app.inference.paytm.com` is the
> control plane and returns 404 for completions.

Models that need extra request parameters carry them in `PI_MODEL_PARAMS` in
`src/answer.py` — qwen3-32b, for example, needs reasoning disabled and JSON mode
or it spends its token budget on a `<think>` block. Override per deployment with
`PI_EXTRA_PARAMS`.

<details>
<summary>Using the Cursor backend instead</summary>

Set `ANSWER_BACKEND=cursor` and `CURSOR_API_KEY=crsr_...` (key from
https://cursor.com/dashboard/api). This path also needs the **Cursor agent CLI**
on your PATH:

```bash
agent status
```

It boots a workspace session per question, so expect roughly 10x the latency of
the Pi Inference path.

</details>

### 3. Ask a question

**Interactive mode** (REPL):

```bash
source .venv/bin/activate
python -m src.ask
```

**One-shot mode** (single question — useful for scripts and eval):

```bash
python -m src.ask "how do I get Cursor working?"
python -m src.ask "how do I enroll a host?"
```

Example output:

```
Retrieving relevant docs...
retrieved 5 chunks in 29ms, top_score=0.509
Generating answer (may take 1–2 min)...

--- Answer ---

Run ark host enroll <compute-name> --launchd --detach ...

Sources:
  1. getting-started -- https://foundry.mypaytm.com/onboarding/getting-started
```

If the docs don't contain the answer:

```
--- Not in the docs ---

I don't have that in the onboarding docs
```

### 4. Run tests

```bash
python3 -m unittest discover -s tests -v
```

---

## Web UI

A browser chat UI (FastAPI) with multi-turn memory, citations, and thumbs
feedback lives in `src/web.py`. Run it locally:

```bash
source .venv/bin/activate
python -m src.web        # → http://127.0.0.1:8765
```

It binds to loopback (`127.0.0.1`) by default. To host it for the team on an Ark
compute — internal-only, no open bind — see **[deploy/README.md](deploy/README.md)**.

---

## Ingest docs (rebuild corpus)

Rebuild the `data/` corpus from Foundry onboarding pages + the Google Docs FAQ:

```bash
python ingest/ingest.py
```

For the Google Doc FAQ, export plain text to `sources/faq-google-doc.txt` (or pass `--gdoc-file`). Optional: set `FOUNDRY_PLATFORM_PATH` to a local `foundry-platform` clone to read raw markdown from `doc-site/onboarding/`.

## Access control (deployed UI)

The web UI is gated by a single shared team token. Set `ARK_ACCESS_TOKEN` (env or
`.env`) and the app requires it on every route except the health probes and the
login page:

```bash
export ARK_ACCESS_TOKEN=some-long-random-team-token
python -m src.web
```

- Users open the site, are redirected to `/login`, and enter the token once (stored
  in an HttpOnly cookie). API calls also accept `Authorization: Bearer <token>`.
- When `ARK_ACCESS_TOKEN` is unset the app runs open for local dev, but
  `python -m src.web` refuses to bind to a non-loopback host in that state, so a
  deployment can't be accidentally exposed without a token.
- `src/auth.py` has an `sso_stub_identity` hook where real SSO can be added later.

Feedback thumbs up/down submitted in the chat are viewable at `/reviews`.

## Who

- **Keerthi** — retrieval: ingestion, chunking, embeddings, retriever, eval harness
- **Aneetta** — answer layer + CLI: grounded `answer(question, chunks)`, `ask()`, citations
