"""Tests for the corpus in Redis.

The store is exercised against a dict-backed fake client, so the suite needs no
server. The encoding is tested directly, because it is the part where a silent
mistake would look like working software: a matrix decoded at the wrong shape
still retrieves, just badly.
"""

from __future__ import annotations

import json
import os
import unittest
import unittest.mock
from typing import Any
from unittest.mock import patch

import numpy as np

from src.corpus_store import (
    CORPUS_KEY,
    RedisCorpusStore,
    corpus_backend,
    corpus_digest,
    decode_corpus,
    encode_corpus,
    load_stored_corpus,
)
from src.retriever import MODEL_NAME, build_index


class FakeRedis:
    """Binary-safe key/value, which is all the store uses."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.calls: list[str] = []
        self.fail_on: set[str] = set()

    def _maybe_fail(self, op: str) -> None:
        self.calls.append(op)
        if op in self.fail_on:
            raise RuntimeError(f"redis {op} unavailable")

    def get(self, key: str) -> bytes | None:
        self._maybe_fail("get")
        return self.data.get(key)

    def set(self, key: str, value: bytes, **kwargs: Any) -> None:
        self._maybe_fail("set")
        if kwargs:
            raise AssertionError(f"the corpus must not carry an expiry: {kwargs}")
        self.data[key] = value


CHUNKS = [
    {"source": "getting-started", "text": "Register a compute host with ark compute register."},
    {"source": "faq", "text": "Team‑admin uses a non‑breaking hyphen; 'quotes'; back\\slash"},
    {"source": "emoji", "text": "Ark \U0001F680 中文 राज"},
]
VECTORS = np.asarray(
    [[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8], [-0.9, 0.0, 0.25, 1.0]], dtype=np.float32
)


class EncodingTests(unittest.TestCase):
    def test_round_trip_preserves_text_exactly(self) -> None:
        header, matrix = decode_corpus(encode_corpus(CHUNKS, VECTORS, embedding_model=MODEL_NAME))
        self.assertEqual([c["text"] for c in header["chunks"]], [c["text"] for c in CHUNKS])
        self.assertEqual([c["source"] for c in header["chunks"]], [c["source"] for c in CHUNKS])

    def test_round_trip_preserves_vectors_bit_for_bit(self) -> None:
        _, matrix = decode_corpus(encode_corpus(CHUNKS, VECTORS, embedding_model=MODEL_NAME))
        self.assertTrue(np.array_equal(matrix, VECTORS))
        self.assertEqual(matrix.dtype, np.float32)
        self.assertEqual(matrix.shape, VECTORS.shape)

    def test_header_records_what_was_stored(self) -> None:
        header, _ = decode_corpus(encode_corpus(CHUNKS, VECTORS, embedding_model=MODEL_NAME))
        self.assertEqual(header["chunk_count"], 3)
        self.assertEqual(header["embedding_dim"], 4)
        self.assertEqual(header["embedding_model"], MODEL_NAME)
        self.assertEqual(header["corpus_digest"], corpus_digest(CHUNKS))

    def test_misaligned_vectors_are_refused_at_encode(self) -> None:
        with self.assertRaises(ValueError):
            encode_corpus(CHUNKS, VECTORS[:1], embedding_model=MODEL_NAME)

    def test_a_truncated_payload_raises_rather_than_decoding_garbage(self) -> None:
        payload = encode_corpus(CHUNKS, VECTORS, embedding_model=MODEL_NAME)
        with self.assertRaises(ValueError):
            decode_corpus(payload[:-4])

    def test_a_payload_shorter_than_its_header_length_raises(self) -> None:
        with self.assertRaises(ValueError):
            decode_corpus(b"0000")

    def test_text_containing_newlines_does_not_break_the_framing(self) -> None:
        chunks = [{"source": "s", "text": "line one\nline two\n0000000042 not a header"}]
        vectors = np.asarray([[1.0, 2.0]], dtype=np.float32)
        header, matrix = decode_corpus(encode_corpus(chunks, vectors, embedding_model="m"))
        self.assertEqual(header["chunks"][0]["text"], chunks[0]["text"])
        self.assertTrue(np.array_equal(matrix, vectors))


class RedisCorpusStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeRedis()
        self.store = RedisCorpusStore("redis://fake", client=self.client)

    def test_ingest_then_load_round_trips(self) -> None:
        self.store.ingest(CHUNKS, VECTORS, embedding_model=MODEL_NAME)
        loaded = self.store.load(embedding_model=MODEL_NAME)
        assert loaded is not None
        chunks, matrix = loaded
        self.assertEqual([c["source"] for c in chunks], [c["source"] for c in CHUNKS])
        self.assertTrue(np.array_equal(matrix, VECTORS))

    def test_the_whole_corpus_is_one_key(self) -> None:
        self.store.ingest(CHUNKS, VECTORS, embedding_model=MODEL_NAME)
        self.assertEqual(list(self.client.data), [CORPUS_KEY])

    def test_ingest_replaces_rather_than_appends(self) -> None:
        self.store.ingest(CHUNKS, VECTORS, embedding_model=MODEL_NAME)
        self.store.ingest(CHUNKS[:1], VECTORS[:1], embedding_model=MODEL_NAME)
        loaded = self.store.load(embedding_model=MODEL_NAME)
        assert loaded is not None
        self.assertEqual(len(loaded[0]), 1)

    def test_an_empty_corpus_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.store.ingest([], np.zeros((0, 4), dtype=np.float32), embedding_model=MODEL_NAME)

    def test_load_before_any_ingest_is_none(self) -> None:
        self.assertIsNone(self.store.load(embedding_model=MODEL_NAME))

    def test_a_corpus_from_another_model_is_refused(self) -> None:
        self.store.ingest(CHUNKS, VECTORS, embedding_model="some-other-encoder")
        self.assertIsNone(self.store.load(embedding_model=MODEL_NAME))

    def test_a_header_disagreeing_with_itself_is_refused(self) -> None:
        self.store.ingest(CHUNKS, VECTORS, embedding_model=MODEL_NAME)
        payload = self.client.data[CORPUS_KEY]
        header, matrix = decode_corpus(payload)
        header["chunk_count"] = 99
        head = json.dumps(header, ensure_ascii=False).encode()
        self.client.data[CORPUS_KEY] = (
            f"{len(head):010d}".encode() + head + matrix.tobytes()
        )
        # chunk_count now disagrees with both the chunk list and the byte count,
        # so the payload must be rejected rather than reshaped.
        with self.assertRaises(Exception):
            self.store.load(embedding_model=MODEL_NAME)

    def test_meta_omits_the_chunk_bodies(self) -> None:
        self.store.ingest(CHUNKS, VECTORS, embedding_model=MODEL_NAME)
        meta = self.store.meta()
        assert meta is not None
        self.assertNotIn("chunks", meta)
        self.assertEqual(meta["chunk_count"], 3)


class LoadStoredCorpusTests(unittest.TestCase):
    def test_files_backend_never_touches_redis(self) -> None:
        with patch.dict(os.environ, {"CORPUS_STORE": "files"}):
            self.assertIsNone(load_stored_corpus(MODEL_NAME))

    def test_default_backend_is_files(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CORPUS_STORE", None)
            self.assertEqual(corpus_backend(), "files")

    def test_an_unreachable_server_falls_back_instead_of_raising(self) -> None:
        with patch.dict(os.environ, {"CORPUS_STORE": "redis", "REDIS_URL": "redis://x"}):
            with patch(
                "src.corpus_store.build_corpus_store", side_effect=OSError("connection refused")
            ):
                self.assertIsNone(load_stored_corpus(MODEL_NAME))


class BuildIndexWithStoredVectorsTests(unittest.TestCase):
    def test_precomputed_vectors_skip_the_model(self) -> None:
        with patch("src.retriever._embed", side_effect=AssertionError("should not embed")):
            index = build_index(list(CHUNKS), embeddings=VECTORS)
        self.assertEqual(len(index.chunks), 3)
        self.assertEqual(index.embeddings.shape, (3, 4))
        self.assertIsNotNone(index.bm25)

    def test_misaligned_vectors_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            build_index(list(CHUNKS), embeddings=VECTORS[:1])


class ReadinessReportsTheCorpusInUseTests(unittest.TestCase):
    """`/ready` must describe the corpus the pod is serving, not the copy on
    disk. Counting files would report the image's documents while the pod
    answered from Redis, and would fail an image that ships none."""

    def test_ready_names_the_backend_it_loaded_from(self) -> None:
        from src import warmup

        with patch("src.warmup.retrieve", return_value=[{"source": "faq", "text": "t"}]):
            with patch(
                "src.warmup.default_index_info",
                return_value={"chunks": 393, "corpus_source": "redis"},
            ):
                ok, body = warmup.check_retrieval_ready()
        self.assertTrue(ok)
        self.assertEqual(body["corpus_source"], "redis")

    def test_missing_data_dir_is_not_fatal_on_the_redis_backend(self) -> None:
        from pathlib import Path

        from src import warmup

        with patch.dict(os.environ, {"CORPUS_STORE": "redis"}):
            with patch("src.warmup.DATA_DIR", Path("/does/not/exist")):
                with patch("src.warmup.retrieve", return_value=[{"source": "faq", "text": "t"}]):
                    with patch(
                        "src.warmup.default_index_info",
                        return_value={"chunks": 3, "corpus_source": "redis"},
                    ):
                        ok, body = warmup.check_retrieval_ready()
        self.assertTrue(ok, body)

    def test_missing_data_dir_still_fails_on_the_files_backend(self) -> None:
        from pathlib import Path

        from src import warmup

        with patch.dict(os.environ, {"CORPUS_STORE": "files"}):
            with patch("src.warmup.DATA_DIR", Path("/does/not/exist")):
                ok, body = warmup.check_retrieval_ready()
        self.assertFalse(ok)
        self.assertEqual(body["reason"], "data directory missing")

    def test_index_info_is_consistent_before_anything_is_loaded(self) -> None:
        from src import retriever

        with patch.object(retriever, "_default_index", None):
            self.assertEqual(
                retriever.default_index_info(), {"chunks": 0, "corpus_source": "not loaded"}
            )


class IngestCliTests(unittest.TestCase):
    """The ingest embeds the whole corpus, which takes most of a minute.
    Anything that makes the run pointless must be found before that."""

    def test_missing_url_fails_before_embedding_anything(self) -> None:
        from src import corpus_store

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REDIS_URL", None)
            with patch("src.chunker.load_chunks") as chunked:
                code = corpus_store.main([])
        self.assertEqual(code, 2)
        chunked.assert_not_called()

    def test_dry_run_needs_no_server(self) -> None:
        from src import corpus_store

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REDIS_URL", None)
            with patch("src.corpus_store.build_corpus_store") as build:
                with patch("src.chunker.load_chunks", return_value=list(CHUNKS)):
                    with patch("src.retriever._embed", return_value=VECTORS):
                        code = corpus_store.main(["--dry-run"])
        self.assertEqual(code, 0)
        build.assert_not_called()

    def test_an_ingest_failure_is_reported_not_raised(self) -> None:
        from src import corpus_store

        store = unittest.mock.MagicMock()
        store.ingest.side_effect = RuntimeError("OOM command not allowed when used memory > maxmemory")
        with patch("src.corpus_store.build_corpus_store", return_value=store):
            with patch("src.chunker.load_chunks", return_value=list(CHUNKS)):
                with patch("src.retriever._embed", return_value=VECTORS):
                    code = corpus_store.main(["--url", "redis://x"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
