# AGENTS.md

## Cursor Cloud specific instructions

This repo is `ark-onboarding-bot`: a Python RAG (retrieval-augmented generation) chatbot
that answers Ark onboarding questions grounded in the docs under `data/`. It is a
**command-line app, not a web service** — there is no server to start.

### Environment
- Python deps live in the `.venv/` virtualenv at the repo root. Activate with
  `source .venv/bin/activate`, or call binaries directly (e.g. `.venv/bin/python`).
  The update script recreates/refreshes this venv on startup.
- Setup/run commands are documented in `README.md`; standard commands are not duplicated here.

### Two independent layers (important)
1. **Retrieval** (`src/chunker.py`, `src/retriever.py`, `src/retrieve.py`) — uses the
   `sentence-transformers` model `all-MiniLM-L6-v2`. Works fully offline of any API key.
   The model is downloaded from Hugging Face on first use and cached under `~/.cache`.
2. **Answer generation** (`src/answer.py`) — posts to the **Pi Inference** gateway by
   default (`ANSWER_BACKEND=pi`), an OpenAI-compatible completions endpoint. Requirements:
   - `PI_API_KEY` must be set (add it as a Secret). Without it `answer()` / the `ask` CLI
     raise `PI_API_KEY not set`.
   - Default model is `qwen/qwen3-32b`. Models needing extra request params carry them in
     `PI_MODEL_PARAMS`; override with `PI_EXTRA_PARAMS` (JSON).
   - The host is `api.inference.paytm.com`. `app.inference.paytm.com` is the control plane
     and 404s on completions.

   Setting `ANSWER_BACKEND=cursor` uses the original **Cursor Agent CLI** path instead. That
   needs the `agent` binary on `PATH` (installed at `~/.local/bin/agent`; reinstall with
   `curl https://cursor.com/install -fsS | bash`, or point at it with `CURSOR_AGENT_BIN`)
   plus `CURSOR_API_KEY`. It boots a workspace session per question and is far slower.

### Running things
- Unit tests (all mocked, no network, no API key): `.venv/bin/python -m unittest discover -s tests`
- Retrieval-only eval (no API key needed): `.venv/bin/python eval/run_eval.py --quiet-retriever --only-scored`
- Full eval (Pi Inference: citations + answer facts + refusals): `.venv/bin/python eval/run_eval.py --full --quiet-retriever` (needs `PI_API_KEY`). Citation-only scored set: add `--only-scored`. Without a key the command exits 2 with a one-line message instead of failing on the first question.
- Interactive chat CLI: `.venv/bin/python -m src.ask` (needs `PI_API_KEY`).
- There is no configured linter (no ruff/flake8/pyproject config); `python -m py_compile`
  is a quick syntax sanity check.

### Gotchas
- `src/retrieve.py` (public `retrieve(question, k=...)`) is a thin wrapper over
  `src/retriever.py` (`retrieve(question, top_k=...)`). Likewise `src/ingest.py` wraps
  `ingest/ingest.py`. Don't confuse the pairs.
- `ingest/ingest.py` fetches live docs from Foundry + a Google Doc and needs network +
  possibly auth; it is only for refreshing the `data/` corpus, which is already committed.
  You do not need to run it for tests/eval/answering.
