"""The retrieval corpus, in Postgres instead of baked into the image.

Today the 16 onboarding documents ship inside the container and the pod
re-embeds all of them at boot. Two consequences: editing a sentence in the FAQ
needs an image rebuild and a redeploy, and every restart pays the embedding
cost again for text that has not changed.

This moves the chunks and their vectors into a table. Ingest runs once, out of
band; the pod reads rows and builds the same in-memory index it builds today.

What this deliberately does NOT do is move search into the database. At 437
chunks the whole matrix is a few megabytes and retrieval is ~6ms of local
numpy, which no network round trip will beat. Postgres is the durable source
here, not the search engine. Vectors are stored as REAL[] rather than a
pgvector column for the same reason: nothing queries them in SQL yet, and the
extension is not confirmed on the instance we are being given. When the corpus
outgrows memory, the column becomes vector(384) and search moves server-side;
that is one ALTER and a reindex on a table of this size.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from typing import Any, Iterable

CORPUS_TABLE = "corpus_chunks"
CORPUS_META_TABLE = "corpus_meta"

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {CORPUS_TABLE} (
  ordinal   INTEGER PRIMARY KEY,
  source    TEXT NOT NULL,
  text      TEXT NOT NULL,
  embedding REAL[] NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_{CORPUS_TABLE}_source ON {CORPUS_TABLE} (source);

CREATE TABLE IF NOT EXISTS {CORPUS_META_TABLE} (
  only_row        BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (only_row),
  ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  chunk_count     INTEGER NOT NULL,
  embedding_model TEXT NOT NULL,
  embedding_dim   INTEGER NOT NULL,
  corpus_digest   TEXT NOT NULL
);
"""


def corpus_backend() -> str:
    """`files` reads data/ as today; `postgres` reads the table."""
    return os.environ.get("CORPUS_STORE", "files").strip().lower() or "files"


def corpus_digest(chunks: Iterable[dict[str, Any]]) -> str:
    """A fingerprint of the corpus, so a stale ingest is detectable.

    Covers source and text only. Two ingests of the same documents produce the
    same digest regardless of when they ran or which machine embedded them.
    """
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(str(chunk.get("source", "")).encode("utf-8"))
        digest.update(b"\x00")
        digest.update(str(chunk.get("text", "")).encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


class PostgresCorpusStore:
    """Chunks and vectors, one row each.

    A connection per call rather than a pool, on purpose: unlike chat sessions
    this is read exactly once per pod lifetime and written only by the ingest
    job, so there is nothing for a pool to amortise.
    """

    def __init__(self, dsn: str, connect: Any | None = None) -> None:
        self._dsn = dsn
        if connect is None:
            import psycopg

            connect = psycopg.connect
        self._connect = connect

    def ensure_schema(self) -> None:
        """Create the tables. Called by the ingest, never by a reader.

        `CREATE INDEX IF NOT EXISTS` takes a lock that conflicts with the
        ingest's DELETE, so running this on the read path would make a pod
        starting mid-ingest block on DDL until that transaction committed --
        and then read the new rows against the old metadata. Readers need no
        schema: if the tables are not there the load fails and falls back to
        the files, which is the correct outcome anyway.
        """
        with self._connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)

    def ingest(
        self,
        chunks: list[dict[str, Any]],
        embeddings: Any,
        *,
        embedding_model: str,
    ) -> dict[str, Any]:
        """Replace the corpus in one transaction.

        Delete-then-insert inside a single transaction rather than an upsert:
        a document that disappeared from data/ has to disappear from the table
        too, and MVCC means a pod booting mid-ingest reads the previous corpus
        whole rather than a half-written mixture of the two.
        """
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"{len(chunks)} chunks but {len(embeddings)} embeddings; they must align"
            )
        if not chunks:
            raise ValueError("refusing to ingest an empty corpus")

        self.ensure_schema()

        dim = len(embeddings[0])
        digest = corpus_digest(chunks)
        rows = [
            (ordinal, str(chunk["source"]), str(chunk["text"]), [float(x) for x in vector])
            for ordinal, (chunk, vector) in enumerate(zip(chunks, embeddings))
        ]

        with self._connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {CORPUS_TABLE}")
                cur.executemany(
                    f"INSERT INTO {CORPUS_TABLE} (ordinal, source, text, embedding)"
                    f" VALUES (%s, %s, %s, %s)",
                    rows,
                )
                cur.execute(f"DELETE FROM {CORPUS_META_TABLE}")
                cur.execute(
                    f"INSERT INTO {CORPUS_META_TABLE}"
                    f" (chunk_count, embedding_model, embedding_dim, corpus_digest)"
                    f" VALUES (%s, %s, %s, %s)",
                    (len(chunks), embedding_model, dim, digest),
                )
        return {
            "chunk_count": len(chunks),
            "embedding_model": embedding_model,
            "embedding_dim": dim,
            "corpus_digest": digest,
        }

    def meta(self) -> dict[str, Any] | None:
        with self._connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT chunk_count, embedding_model, embedding_dim, corpus_digest,"
                    f" ingested_at FROM {CORPUS_META_TABLE} LIMIT 1"
                )
                row = cur.fetchone()
        if not row:
            return None
        return {
            "chunk_count": int(row[0]),
            "embedding_model": str(row[1]),
            "embedding_dim": int(row[2]),
            "corpus_digest": str(row[3]),
            "ingested_at": str(row[4]),
        }

    def load(self, *, embedding_model: str) -> tuple[list[dict[str, str]], list[list[float]]] | None:
        """Chunks in ordinal order with their vectors, or None if unusable.

        Refuses a corpus embedded by a different model. Those vectors are not
        wrong in any way the code can see -- they are the right shape and the
        right length -- they simply describe a different space, so retrieval
        would degrade quietly rather than fail. Returning None sends the caller
        back to embedding the files: slower, and correct.
        """
        meta = self.meta()
        if meta is None:
            return None
        if meta["embedding_model"] != embedding_model:
            print(
                f"corpus in Postgres was embedded with {meta['embedding_model']!r} but this "
                f"pod uses {embedding_model!r}; ignoring it and embedding data/ instead. "
                f"Re-run the corpus ingest to fix.",
                file=sys.stderr,
            )
            return None

        with self._connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT source, text, embedding FROM {CORPUS_TABLE} ORDER BY ordinal"
                )
                rows = cur.fetchall() or []
        if not rows:
            return None
        chunks = [{"source": str(r[0]), "text": str(r[1])} for r in rows]
        vectors = [list(r[2]) for r in rows]
        if len(chunks) != meta["chunk_count"]:
            print(
                f"corpus table holds {len(chunks)} rows but metadata claims "
                f"{meta['chunk_count']}; ignoring it and embedding data/ instead.",
                file=sys.stderr,
            )
            return None
        return chunks, vectors


def build_corpus_store(dsn: str | None = None) -> PostgresCorpusStore:
    """Resolve the DSN. The message stays neutral because both the reader and
    the ingest land here, and naming CORPUS_STORE would send whoever ran the
    ingest to set a variable that would not help them."""
    dsn = (dsn or os.environ.get("DATABASE_URL", "")).strip()
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set; the corpus store needs a connection string.")
    return PostgresCorpusStore(dsn)


def load_stored_corpus(embedding_model: str) -> tuple[list[dict[str, str]], list[list[float]]] | None:
    """What the retriever calls at boot. Never raises; falls back to files."""
    if corpus_backend() != "postgres":
        return None
    try:
        return build_corpus_store().load(embedding_model=embedding_model)
    except Exception as exc:  # noqa: BLE001 - a corpus read must not stop the pod
        print(
            f"could not read the corpus from Postgres ({exc!r}); embedding data/ instead.",
            file=sys.stderr,
        )
        return None


def main(argv: list[str] | None = None) -> int:
    """Ingest data/ into Postgres. Run out of band, not at pod start."""
    import argparse

    parser = argparse.ArgumentParser(description="Ingest the corpus into Postgres")
    parser.add_argument("--dsn", default=None, help="defaults to DATABASE_URL")
    parser.add_argument("--dry-run", action="store_true", help="chunk and embed, write nothing")
    args = parser.parse_args(argv)

    # Resolve the destination before doing any work. Embedding the corpus takes
    # the better part of a minute, and discovering there is nowhere to write it
    # only afterwards wastes all of it.
    store = None
    if not args.dry_run:
        try:
            store = build_corpus_store(args.dsn)
        except RuntimeError as exc:
            print(f"{exc} Pass --dsn, or set DATABASE_URL.", file=sys.stderr)
            return 2

    from src.chunker import load_chunks
    from src.retriever import MODEL_NAME, _embed

    chunks = load_chunks()
    if not chunks:
        print("no chunks found under data/", file=sys.stderr)
        return 1
    print(f"chunking data/ produced {len(chunks)} chunks; embedding with {MODEL_NAME} ...")
    embeddings = _embed([c["text"] for c in chunks])

    if args.dry_run:
        print(json.dumps({
            "chunk_count": len(chunks),
            "embedding_model": MODEL_NAME,
            "embedding_dim": int(embeddings.shape[1]),
            "corpus_digest": corpus_digest(chunks),
            "written": False,
        }, indent=2))
        return 0

    assert store is not None
    try:
        result = store.ingest(chunks, embeddings.tolist(), embedding_model=MODEL_NAME)
    except Exception as exc:  # noqa: BLE001 - a CLI should not print a traceback
        print(f"ingest failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({**result, "written": True}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
