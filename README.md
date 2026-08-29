# ark-onboarding-bot

A **RAG chatbot that answers Ark onboarding questions** — grounded in the Ark onboarding docs + FAQ, with citations and an honest "not in the docs" when it doesn't know.

## What it does

Ask it "how do I enroll a host?" or "how do I set up Cursor?" → it retrieves the relevant doc chunks and a model answers **grounded in those chunks**, citing the page it used. When the docs do not cover the question it says so and hands off, rather than guessing.

## Architecture

```
ingest docs → chunk (900 chars) → embed (all-MiniLM-L6-v2)
            → retrieve top-8 (BM25 + dense, fused by reciprocal rank)
            → model answers grounded in those chunks, with citations
            → CLI · web UI (SSE streaming) · Slack bot
```

Answers are generated through the **Pi Inference** gateway (`qwen/qwen3-32b` by
default) — a plain OpenAI-compatible completion call, no agent harness.

## Knowledge sources

- Onboarding docs: https://foundry.mypaytm.com/onboarding/
- Roadmap: https://foundry.mypaytm.com/roadmap/
- FAQ doc: https://docs.google.com/document/d/1cFO96__cGuADEFvR_ahHcc0ILmYWvIrodjwMguihVbY/edit

## Layout

- `data/` — ingested onboarding + FAQ corpus (one file per source)
- `ingest/` — ingestion pipeline
- `src/` — retrieval, answer layer, CLI (`ask.py`), web UI (`web.py`), Slack bot
  (`slack_app.py`), auth, and the query/feedback logs
- `eval/` — the gold set (`questions.json`), the harness (`run_eval.py`), and
  tuning notes
- `tests/` — unit tests
- `deploy/` — hosting notes, the smoke script, and the Helm chart
  (`deploy/helm/ark-onboarding-bot`)

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
retrieved 8 chunks in 23ms, top_score=0.717
Generating answer...

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

## Eval and the CI gates

The gold set lives in `eval/questions.json`: scored questions carry an
`expected_source` (the page that legitimately answers them) and
`answer_must_include` markers; refusal questions assert the bot declines
cleanly on things the docs do not cover.

```bash
python eval/run_eval.py --only-scored              # retrieval only, no model calls
python eval/run_eval.py --only-scored --no-pins    # what the retriever scores unaided
python eval/run_eval.py --full --only-scored       # end to end, needs PI_API_KEY
```

`.github/workflows/eval.yml` runs four jobs on every PR, and **a red run blocks
the image publish** (`publish-image.yml` only publishes when the eval workflow
succeeds on `main`):

| job | what it asserts |
|---|---|
| `retrieval-eval` | every scored question retrieves an accepted page — no model calls |
| `refusal-eval` | out-of-scope questions are declined cleanly |
| `full-eval` | every scored question **cites** an accepted page, against a real model |
| `workflow-lint` | the workflows themselves parse |

Two things worth knowing before you touch the gold set:

- **A citation miss is asked once more** and recorded as `retried` in the report,
  so flakiness is visible rather than hidden. The run still fails if the second
  ask misses too.
- **A label may name several pages**, each with a `page_evidence` quote that must
  appear on that page. Some facts genuinely live on more than one page, and the
  FAQ is currently ingested twice (site scrape and Google Doc) at 96% similarity,
  so a page-level label that names only one of a duplicated pair fails a
  *correct* citation at random. `tests/test_eval_gold_set.py` enforces both rules.

Every run writes a full report, answers included, to `eval/results/`; CI uploads
it as the `full-eval-report` artifact even when the run fails.

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
