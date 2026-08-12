# ark-onboarding-bot

A **RAG chatbot that answers Ark onboarding questions** — grounded in the Ark onboarding docs + FAQ, with citations and an honest "not in the docs" when it doesn't know. v1 target: **Fri Aug 14**.

## What it does
Ask it "how do I enroll a host?" or "how do I set up Cursor?" → it retrieves the relevant doc chunks and Claude answers **grounded in those chunks**, linking the source. No hallucinating.

## Architecture (v1)
`ingest docs → chunk → embed → vector store → retrieve top-k → Claude generates a grounded, cited answer → simple chat UI`

## Knowledge sources
- Onboarding docs: https://foundry.mypaytm.com/onboarding/
- FAQ doc: https://docs.google.com/document/d/1cFO96__cGuADEFvR_ahHcc0ILmYWvIrodjwMguihVbY/edit

## Layout
- `data/` — the ingested onboarding + FAQ corpus (one clean file per source)
- `ingest/` — ingestion + chunking + embedding pipeline (Keerthi)
- `src/` — the answer layer (Claude, grounded + cited) + retriever + chat UI (Aneetta)
- `tests/` — tests + eval against the FAQ questions

## Who
- **Keerthi** — retrieval track: ingestion, chunking, embeddings, retriever
- **Aneetta** — answer + UI track: grounded `answer(question, chunks)` + citations, chat UI

## Setup
```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...   # for Claude generation
```

## Ingest docs (Task 1)
Rebuild the `data/` corpus from Foundry onboarding pages + the Google Docs FAQ:

```bash
python ingest/ingest.py
```

For the Google Doc FAQ, export plain text to `sources/faq-google-doc.txt` (or pass `--gdoc-file`). Optional: set `FOUNDRY_PLATFORM_PATH` to a local `foundry-platform` clone to read raw markdown from `doc-site/onboarding/`.
