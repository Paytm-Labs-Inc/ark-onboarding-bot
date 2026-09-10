"""The retrieval corpus, in Redis instead of baked into the image.

Today the onboarding documents ship inside the container and the pod re-chunks
and re-embeds all of them at boot. Two consequences: editing a sentence in the
FAQ needs an image rebuild and a redeploy, and every restart recomputes vectors
for text that has not changed.

This puts the chunks and their vectors in Redis. An ingest runs out of band
when the documents change; the pod reads the key and builds the same in-memory
index it builds today.

Redis rather than a relational database, deliberately. The corpus is about a
megabyte, nothing ever queries it in the store, and it is read whole exactly
once per pod. It is also derived data: if every byte vanished, re-running the
ingest reproduces it. So the store is somewhere to keep bytes, not a query
engine, and Redis is the one we already run.

The whole corpus is a single key. That is the point rather than a shortcut: one
key means the swap is atomic by definition, with no MULTI to forget and no
window where a reader can see half of one corpus and half of another.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import time
from typing import Any, Iterable

# Namespaced away from the chat session keys so the two can share an instance.
# Versioned so a change to the payload layout cannot be misread as corruption.
CORPUS_KEY = "ark-onboarding-bot:corpus:v1"

# A cache would be pointless here, but a hung read at boot is not: the pod would
# sit unready instead of falling back to the files it already has.
REDIS_TIMEOUT_SECONDS = 5.0

# The payload is a length-prefixed JSON header followed by the raw float32
# matrix. Ten zero-padded digits, so parsing needs no delimiter and no guess
# about what may appear inside the document text.
_HEADER_LEN_WIDTH = 10


def corpus_backend() -> str:
    """`files` chunks and embeds data/ at boot; `redis` reads the key."""
    return os.environ.get("CORPUS_STORE", "files").strip().lower() or "files"


def corpus_digest(chunks: Iterable[dict[str, Any]]) -> str:
    """A fingerprint of the corpus, so a stale ingest is detectable.

    Covers source and text only, in order. Two ingests of the same documents
    produce the same digest regardless of when they ran or which machine
    embedded them.
    """
    import hashlib

    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(str(chunk.get("source", "")).encode("utf-8"))
        digest.update(b"\x00")
        digest.update(str(chunk.get("text", "")).encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def encode_corpus(
    chunks: list[dict[str, Any]],
    vectors: Any,
    *,
    embedding_model: str,
) -> bytes:
    """Pack the corpus into one value.

    Vectors go in as raw float32 rather than JSON numbers. The matrix is 393 by
    384 here: about 590 KB as bytes and several megabytes as decimal text, and
    the text form would also round-trip through Python floats for no reason.
    """
    import numpy as np

    matrix = np.ascontiguousarray(np.asarray(vectors, dtype=np.float32))
    if matrix.ndim != 2:
        raise ValueError(f"expected a 2-D matrix of vectors, got shape {matrix.shape}")
    if len(chunks) != matrix.shape[0]:
        raise ValueError(
            f"{len(chunks)} chunks but {matrix.shape[0]} vectors; they must align"
        )

    header = {
        "version": 1,
        "embedding_model": embedding_model,
        "embedding_dim": int(matrix.shape[1]),
        "chunk_count": len(chunks),
        "corpus_digest": corpus_digest(chunks),
        "ingested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "chunks": [
            {"source": str(c["source"]), "text": str(c["text"])} for c in chunks
        ],
    }
    header_bytes = json.dumps(header, ensure_ascii=False).encode("utf-8")
    prefix = f"{len(header_bytes):0{_HEADER_LEN_WIDTH}d}".encode("ascii")
    return prefix + header_bytes + matrix.tobytes()


def decode_corpus(payload: bytes) -> tuple[dict[str, Any], Any]:
    """Unpack a value written by `encode_corpus`. Raises on anything malformed."""
    import numpy as np

    if len(payload) < _HEADER_LEN_WIDTH:
        raise ValueError("corpus payload is too short to hold a header length")
    header_len = int(payload[:_HEADER_LEN_WIDTH].decode("ascii"))
    start = _HEADER_LEN_WIDTH
    end = start + header_len
    if end > len(payload):
        raise ValueError("corpus payload is truncated: header runs past the value")

    header = json.loads(payload[start:end].decode("utf-8"))
    body = payload[end:]

    dim = int(header["embedding_dim"])
    count = int(header["chunk_count"])
    expected = count * dim * 4
    if len(body) != expected:
        raise ValueError(
            f"corpus payload holds {len(body)} vector bytes, expected {expected} "
            f"for {count} chunks of {dim} dimensions"
        )
    matrix = np.frombuffer(body, dtype=np.float32).reshape(count, dim)
    return header, matrix


class RedisCorpusStore:
    """One key, read whole at boot and replaced whole by the ingest."""

    def __init__(self, url: str, client: Any | None = None) -> None:
        if client is None:
            import redis

            # decode_responses stays off: the value is binary, and asking the
            # client to decode it as text would corrupt the matrix.
            client = redis.Redis.from_url(
                url,
                decode_responses=False,
                socket_timeout=REDIS_TIMEOUT_SECONDS,
                socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
            )
        self._client = client

    def ingest(
        self,
        chunks: list[dict[str, Any]],
        vectors: Any,
        *,
        embedding_model: str,
    ) -> dict[str, Any]:
        """Replace the corpus. One SET, so readers see one corpus or the other."""
        if not chunks:
            raise ValueError("refusing to ingest an empty corpus")
        payload = encode_corpus(chunks, vectors, embedding_model=embedding_model)
        # No expiry, ever. The corpus is not a cache, and the instance runs
        # noeviction, so nothing removes this key but another ingest.
        self._client.set(CORPUS_KEY, payload)
        header, _ = decode_corpus(payload)
        return {k: v for k, v in header.items() if k != "chunks"} | {
            "bytes": len(payload)
        }

    def load(self, *, embedding_model: str) -> tuple[list[dict[str, str]], Any] | None:
        """The corpus, or None if there is nothing usable to load.

        Refuses a corpus embedded by a different model. Those vectors are the
        right shape and the right length; they simply describe a different
        space, so retrieval would quietly get worse rather than fail. Returning
        None sends the caller back to embedding the files: slower, and correct.
        """
        payload = self._client.get(CORPUS_KEY)
        if not payload:
            return None
        header, matrix = decode_corpus(payload)

        stored_model = str(header.get("embedding_model", ""))
        if stored_model != embedding_model:
            print(
                f"corpus in Redis was embedded with {stored_model!r} but this pod uses "
                f"{embedding_model!r}; ignoring it and embedding data/ instead. "
                f"Re-run the corpus ingest to fix.",
                file=sys.stderr,
            )
            return None

        chunks = [
            {"source": str(c["source"]), "text": str(c["text"])}
            for c in header.get("chunks", [])
        ]
        if len(chunks) != int(header.get("chunk_count", -1)):
            print(
                f"corpus header claims {header.get('chunk_count')} chunks but carries "
                f"{len(chunks)}; ignoring it and embedding data/ instead.",
                file=sys.stderr,
            )
            return None
        return chunks, matrix

    def meta(self) -> dict[str, Any] | None:
        payload = self._client.get(CORPUS_KEY)
        if not payload:
            return None
        header, _ = decode_corpus(payload)
        return {k: v for k, v in header.items() if k != "chunks"}


def build_corpus_store(url: str | None = None) -> RedisCorpusStore:
    """Resolve the connection. The message stays neutral because both the
    reader and the ingest land here, and naming CORPUS_STORE would send whoever
    ran the ingest to set a variable that would not help them."""
    url = (url or os.environ.get("REDIS_URL", "")).strip()
    if not url:
        raise RuntimeError("REDIS_URL is not set; the corpus store needs a connection string.")
    return RedisCorpusStore(url)


def load_stored_corpus(embedding_model: str) -> tuple[list[dict[str, str]], Any] | None:
    """What the retriever calls at boot. Never raises; falls back to files."""
    if corpus_backend() != "redis":
        return None
    try:
        return build_corpus_store().load(embedding_model=embedding_model)
    except Exception as exc:  # noqa: BLE001 - a corpus read must not stop the pod
        print(
            f"could not read the corpus from Redis ({exc!r}); embedding data/ instead.",
            file=sys.stderr,
        )
        return None


def main(argv: list[str] | None = None) -> int:
    """Ingest data/ into Redis. Run out of band, not at pod start."""
    import argparse

    parser = argparse.ArgumentParser(description="Ingest the corpus into Redis")
    parser.add_argument("--url", default=None, help="defaults to REDIS_URL")
    parser.add_argument("--dry-run", action="store_true", help="chunk and embed, write nothing")
    parser.add_argument("--status", action="store_true", help="print what is stored, then exit")
    args = parser.parse_args(argv)

    # Resolve the destination before doing any work. Embedding the corpus takes
    # the better part of a minute, and discovering there is nowhere to write it
    # only afterwards wastes all of it.
    store = None
    if not args.dry_run:
        try:
            store = build_corpus_store(args.url)
        except RuntimeError as exc:
            print(f"{exc} Pass --url, or set REDIS_URL.", file=sys.stderr)
            return 2

    if args.status:
        assert store is not None
        meta = store.meta()
        print(json.dumps(meta, indent=2) if meta else "no corpus stored")
        return 0

    from src.chunker import load_chunks
    from src.retriever import MODEL_NAME, _embed

    chunks = load_chunks()
    if not chunks:
        print("no chunks found under data/", file=sys.stderr)
        return 1
    print(f"chunking data/ produced {len(chunks)} chunks; embedding with {MODEL_NAME} ...")
    vectors = _embed([c["text"] for c in chunks])

    if args.dry_run:
        print(json.dumps({
            "chunk_count": len(chunks),
            "embedding_model": MODEL_NAME,
            "embedding_dim": int(vectors.shape[1]),
            "corpus_digest": corpus_digest(chunks),
            "written": False,
        }, indent=2))
        return 0

    assert store is not None
    try:
        result = store.ingest(chunks, vectors, embedding_model=MODEL_NAME)
    except Exception as exc:  # noqa: BLE001 - a CLI should not print a traceback
        print(f"ingest failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({**result, "written": True}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
