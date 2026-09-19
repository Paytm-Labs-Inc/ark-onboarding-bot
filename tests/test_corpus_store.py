"""Tests for the corpus in Postgres.

Run against a fake connection: the statements the store issues are real SQL,
and the fake interprets enough of them to round-trip a corpus, so the suite
needs no database.
"""

from __future__ import annotations

import os
import unittest
import unittest.mock
from typing import Any
from unittest.mock import patch

import numpy as np

from src.corpus_store import (
    PostgresCorpusStore,
    corpus_backend,
    corpus_digest,
    load_stored_corpus,
)
from src.retriever import MODEL_NAME, build_index


class FakeCursor:
    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state
        self._result: list[tuple] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: tuple = ()) -> None:
        head = " ".join(sql.split()).upper()
        if head.startswith("CREATE"):
            return
        if head.startswith("DELETE FROM CORPUS_CHUNKS"):
            self._state["chunks"] = []
            return
        if head.startswith("DELETE FROM CORPUS_META"):
            self._state["meta"] = None
            return
        if head.startswith("INSERT INTO CORPUS_CHUNKS"):
            self._state["chunks"].append(params)
            return
        if head.startswith("INSERT INTO CORPUS_META"):
            self._state["meta"] = params
            return
        if "FROM CORPUS_META" in head:
            meta = self._state["meta"]
            self._result = [(*meta, "2026-09-09T00:00:00Z")] if meta else []
            return
        if "FROM CORPUS_CHUNKS" in head:
            rows = sorted(self._state["chunks"], key=lambda r: r[0])
            self._result = [(r[1], r[2], r[3]) for r in rows]
            return
        raise AssertionError(f"unexpected statement: {sql}")

    def executemany(self, sql: str, seq: list[tuple]) -> None:
        for params in seq:
            self.execute(sql, params)

    def fetchone(self) -> tuple | None:
        return self._result[0] if self._result else None

    def fetchall(self) -> list[tuple]:
        return self._result


class FakeConnection:
    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return FakeCursor(self._state)


CHUNKS = [
    {"source": "getting-started", "text": "Register a compute host with ark compute register."},
    {"source": "faq", "text": "The gateway lives at api.inference.paytm.com."},
]
VECTORS = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]


class CorpusDigestTests(unittest.TestCase):
    def test_same_corpus_gives_the_same_digest(self) -> None:
        self.assertEqual(corpus_digest(CHUNKS), corpus_digest(list(CHUNKS)))

    def test_changed_text_changes_the_digest(self) -> None:
        edited = [dict(CHUNKS[0], text="something else"), CHUNKS[1]]
        self.assertNotEqual(corpus_digest(CHUNKS), corpus_digest(edited))

    def test_reordering_changes_the_digest(self) -> None:
        self.assertNotEqual(corpus_digest(CHUNKS), corpus_digest(list(reversed(CHUNKS))))


class PostgresCorpusStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state: dict[str, Any] = {"chunks": [], "meta": None}
        self.store = PostgresCorpusStore(
            "postgresql://fake/db", connect=lambda _dsn: FakeConnection(self.state)
        )

    def test_a_reader_runs_no_ddl(self) -> None:
        """Constructing a store must not touch the schema: the DDL lock would
        block a pod that started while an ingest was mid-transaction."""
        seen: list[str] = []

        class RecordingCursor(FakeCursor):
            def execute(self, sql: str, params: tuple = ()) -> None:
                seen.append(" ".join(sql.split()).split()[0].upper())
                super().execute(sql, params)

        class RecordingConnection(FakeConnection):
            def cursor(self) -> RecordingCursor:
                return RecordingCursor(self._state)

        PostgresCorpusStore("postgresql://fake/db", connect=lambda _d: RecordingConnection(self.state))
        self.assertEqual(seen, [])

    def test_ingest_creates_the_schema(self) -> None:
        self.store.ingest(CHUNKS, VECTORS, embedding_model=MODEL_NAME)
        loaded = self.store.load(embedding_model=MODEL_NAME)
        self.assertIsNotNone(loaded)

    def test_ingest_then_load_round_trips_in_order(self) -> None:
        self.store.ingest(CHUNKS, VECTORS, embedding_model=MODEL_NAME)
        loaded = self.store.load(embedding_model=MODEL_NAME)
        assert loaded is not None
        chunks, vectors = loaded
        self.assertEqual([c["source"] for c in chunks], ["getting-started", "faq"])
        self.assertEqual(vectors, VECTORS)

    def test_ingest_replaces_rather_than_appends(self) -> None:
        self.store.ingest(CHUNKS, VECTORS, embedding_model=MODEL_NAME)
        self.store.ingest(CHUNKS[:1], VECTORS[:1], embedding_model=MODEL_NAME)
        loaded = self.store.load(embedding_model=MODEL_NAME)
        assert loaded is not None
        self.assertEqual(len(loaded[0]), 1)

    def test_misaligned_embeddings_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.store.ingest(CHUNKS, VECTORS[:1], embedding_model=MODEL_NAME)

    def test_an_empty_corpus_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.store.ingest([], [], embedding_model=MODEL_NAME)

    def test_load_before_any_ingest_is_none(self) -> None:
        self.assertIsNone(self.store.load(embedding_model=MODEL_NAME))

    def test_a_corpus_from_another_model_is_refused(self) -> None:
        self.store.ingest(CHUNKS, VECTORS, embedding_model="some-other-encoder")
        self.assertIsNone(self.store.load(embedding_model=MODEL_NAME))

    def test_row_count_disagreeing_with_metadata_is_refused(self) -> None:
        self.store.ingest(CHUNKS, VECTORS, embedding_model=MODEL_NAME)
        self.state["chunks"] = self.state["chunks"][:1]
        self.assertIsNone(self.store.load(embedding_model=MODEL_NAME))

    def test_metadata_records_what_was_ingested(self) -> None:
        self.store.ingest(CHUNKS, VECTORS, embedding_model=MODEL_NAME)
        meta = self.store.meta()
        assert meta is not None
        self.assertEqual(meta["chunk_count"], 2)
        self.assertEqual(meta["embedding_dim"], 3)
        self.assertEqual(meta["embedding_model"], MODEL_NAME)
        self.assertEqual(meta["corpus_digest"], corpus_digest(CHUNKS))


class LoadStoredCorpusTests(unittest.TestCase):
    def test_files_backend_never_touches_postgres(self) -> None:
        with patch.dict(os.environ, {"CORPUS_STORE": "files"}):
            self.assertIsNone(load_stored_corpus(MODEL_NAME))

    def test_default_backend_is_files(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CORPUS_STORE", None)
            self.assertEqual(corpus_backend(), "files")

    def test_an_unreachable_database_falls_back_instead_of_raising(self) -> None:
        with patch.dict(os.environ, {"CORPUS_STORE": "postgres", "DATABASE_URL": "postgresql://x/y"}):
            with patch(
                "src.corpus_store.build_corpus_store", side_effect=OSError("connection refused")
            ):
                self.assertIsNone(load_stored_corpus(MODEL_NAME))


class BuildIndexWithStoredVectorsTests(unittest.TestCase):
    def test_precomputed_vectors_skip_the_model(self) -> None:
        vectors = np.asarray(VECTORS, dtype=np.float32)
        with patch("src.retriever._embed", side_effect=AssertionError("should not embed")):
            index = build_index(list(CHUNKS), embeddings=vectors)
        self.assertEqual(len(index.chunks), 2)
        self.assertEqual(index.embeddings.shape, (2, 3))
        self.assertIsNotNone(index.bm25)

    def test_misaligned_vectors_are_refused(self) -> None:
        vectors = np.asarray(VECTORS[:1], dtype=np.float32)
        with self.assertRaises(ValueError):
            build_index(list(CHUNKS), embeddings=vectors)

    def test_no_vectors_still_embeds_as_before(self) -> None:
        with patch(
            "src.retriever._embed", return_value=np.asarray(VECTORS, dtype=np.float32)
        ) as embed:
            index = build_index(list(CHUNKS))
        embed.assert_called_once()
        self.assertEqual(len(index.chunks), 2)


if __name__ == "__main__":
    unittest.main()


class ReadinessReportsTheCorpusInUseTests(unittest.TestCase):
    """`/ready` must describe the corpus the pod is serving, not the copy on
    disk. Counting files would report the image's documents while the pod
    answered from Postgres, and would fail an image that ships none."""

    def test_ready_names_the_backend_it_loaded_from(self) -> None:
        from src import warmup

        with patch("src.warmup.retrieve", return_value=[{"source": "faq", "text": "t"}]):
            with patch(
                "src.warmup.default_index_info",
                return_value={"chunks": 437, "corpus_source": "postgres"},
            ):
                ok, body = warmup.check_retrieval_ready()
        self.assertTrue(ok)
        self.assertEqual(body["corpus_source"], "postgres")
        self.assertEqual(body["chunks"], 437)

    def test_missing_data_dir_is_not_fatal_on_the_postgres_backend(self) -> None:
        from pathlib import Path

        from src import warmup

        with patch.dict(os.environ, {"CORPUS_STORE": "postgres"}):
            with patch("src.warmup.DATA_DIR", Path("/does/not/exist")):
                with patch("src.warmup.retrieve", return_value=[{"source": "faq", "text": "t"}]):
                    with patch(
                        "src.warmup.default_index_info",
                        return_value={"chunks": 3, "corpus_source": "postgres"},
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
            info = retriever.default_index_info()
        self.assertEqual(info, {"chunks": 0, "corpus_source": "not loaded"})


class IngestCliTests(unittest.TestCase):
    """The ingest embeds the whole corpus, which takes the better part of a
    minute. Anything that makes the run pointless must be found before that."""

    def test_missing_dsn_fails_before_embedding_anything(self) -> None:
        from src import corpus_store

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            with patch("src.chunker.load_chunks") as chunked:
                code = corpus_store.main([])
        self.assertEqual(code, 2)
        chunked.assert_not_called()

    def test_dry_run_needs_no_database(self) -> None:
        from src import corpus_store

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            with patch("src.corpus_store.build_corpus_store") as build:
                with patch("src.chunker.load_chunks", return_value=list(CHUNKS)):
                    with patch(
                        "src.retriever._embed",
                        return_value=np.asarray(VECTORS, dtype=np.float32),
                    ):
                        code = corpus_store.main(["--dry-run"])
        self.assertEqual(code, 0)
        build.assert_not_called()

    def test_an_ingest_failure_is_reported_not_raised(self) -> None:
        from src import corpus_store

        store = unittest.mock.MagicMock()
        store.ingest.side_effect = RuntimeError("permission denied for schema public")
        with patch("src.corpus_store.build_corpus_store", return_value=store):
            with patch("src.chunker.load_chunks", return_value=list(CHUNKS)):
                with patch(
                    "src.retriever._embed", return_value=np.asarray(VECTORS, dtype=np.float32)
                ):
                    code = corpus_store.main(["--dsn", "postgresql://x/y"])
        self.assertEqual(code, 1)
