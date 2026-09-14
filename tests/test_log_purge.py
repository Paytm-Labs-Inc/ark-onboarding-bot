"""A user delete has to erase the observability logs too, not just the thread.

Bhurva's ruling is that a delete erases everywhere. The two JSONL logs were the
surface it never reached: both keep the question verbatim alongside the
`session_id` that ties it to a person's thread, and `feedback.jsonl` keeps the
answer in full and is rendered by /reviews to anyone with the shared token.

Every test here was written red first against the unpatched delete path and
only then made to pass. The ones that matter most are the two that pin the
ORDER, because a purge that runs after the store delete still leaves the
question text behind whenever it fails, and a suite that only checks the happy
path would go green on exactly that bug.
"""

from __future__ import annotations

import json
import os
import stat
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src import feedback as feedback_mod
from src import query_log as query_log_mod
from src.chat import reset_session
from src.jsonl_purge import purge_session_records
from src.session_store import MemorySessionStore, StoredSession, StoredTurn, reset_session_store


def _write_lines(path: Path, records: list[dict | str]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            if isinstance(record, str):
                handle.write(record + "\n")
            else:
                handle.write(json.dumps(record) + "\n")


def _read_lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class PurgeSessionRecordsTests(unittest.TestCase):
    """The shared rewrite itself."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "log.jsonl"
        self.lock = threading.Lock()

    def purge(self, session_id: str) -> int:
        return purge_session_records(
            self.path, session_id, lock=self.lock, lock_timeout=2.0
        )

    def test_removes_only_the_named_session(self) -> None:
        _write_lines(
            self.path,
            [
                {"session_id": "keep-1", "question": "how do I enroll a host?"},
                {"session_id": "drop-me", "question": "my private question"},
                {"session_id": "keep-2", "question": "what is a workspace?"},
                {"session_id": "drop-me", "question": "another private one"},
            ],
        )

        self.assertEqual(self.purge("drop-me"), 2)

        remaining = self.path.read_text(encoding="utf-8")
        self.assertNotIn("my private question", remaining)
        self.assertNotIn("another private one", remaining)
        self.assertIn("how do I enroll a host?", remaining)
        self.assertIn("what is a workspace?", remaining)
        self.assertEqual(len(_read_lines(self.path)), 2)

    def test_missing_file_is_not_an_error(self) -> None:
        # A pod that has answered nothing yet still has to accept a delete.
        self.assertEqual(self.purge("anything"), 0)

    def test_empty_session_id_matches_nothing(self) -> None:
        # Records carry session_id: None from the CLI and from Slack. An empty
        # argument must not be read as "purge everything unattributed".
        _write_lines(
            self.path,
            [{"session_id": None, "question": "cli question"}, {"question": "no key"}],
        )
        self.assertEqual(self.purge(""), 0)
        self.assertEqual(len(_read_lines(self.path)), 2)

    def test_no_match_leaves_the_file_byte_identical(self) -> None:
        _write_lines(self.path, [{"session_id": "keep", "question": "q"}])
        before = self.path.read_bytes()
        before_inode = os.stat(self.path).st_ino

        self.assertEqual(self.purge("absent"), 0)

        self.assertEqual(self.path.read_bytes(), before)
        # Not merely equal content: an untouched file should not be churned
        # through a rename, which is a window an appender could fall into.
        self.assertEqual(os.stat(self.path).st_ino, before_inode)

    def test_unparseable_line_survives(self) -> None:
        # A truncated line is an interleaved write, not a record we can
        # attribute to anyone, so there is no session id to match it on.
        _write_lines(
            self.path,
            [
                '{"session_id": "drop", "question": "gone"}',
                '{"session_id": "drop", "quest',
                '{"session_id": "keep", "question": "stays"}',
            ],
        )

        self.assertEqual(self.purge("drop"), 1)

        lines = _read_lines(self.path)
        self.assertEqual(len(lines), 2)
        self.assertIn('{"session_id": "drop", "quest', lines)

    def test_keeps_the_logs_file_mode(self) -> None:
        # mkstemp creates 0600. Replacing a group-readable log with one would
        # silently lock out anything else that reads it.
        _write_lines(
            self.path,
            [{"session_id": "drop", "q": "x"}, {"session_id": "keep", "q": "y"}],
        )
        os.chmod(self.path, 0o644)

        self.purge("drop")

        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o644)

    def test_leaves_no_temporary_file_behind(self) -> None:
        _write_lines(
            self.path,
            [{"session_id": "drop", "q": "x"}, {"session_id": "keep", "q": "y"}],
        )

        self.purge("drop")

        self.assertEqual(
            [p.name for p in self.path.parent.iterdir()], [self.path.name]
        )

    def test_a_busy_log_raises_rather_than_reporting_success(self) -> None:
        # Returning 0 here would read as "nothing to erase" and the delete
        # would carry on and drop the thread, leaving the questions behind.
        _write_lines(self.path, [{"session_id": "drop", "q": "x"}])
        self.lock.acquire()
        self.addCleanup(self.lock.release)

        with self.assertRaises(OSError):
            purge_session_records(
                self.path, "drop", lock=self.lock, lock_timeout=0.01
            )

    def test_takes_the_writers_lock_so_an_append_cannot_be_lost(self) -> None:
        # os.replace swaps the inode. If the purge did not hold the appending
        # writer's own lock, a concurrent append would land in the file the
        # purge rewrote away and vanish at the rename.
        _write_lines(self.path, [{"session_id": "drop", "q": "x"}])
        observed: list[bool] = []
        real_replace = os.replace

        def spy(src: object, dst: object) -> None:
            observed.append(self.lock.locked())
            real_replace(src, dst)

        with patch("src.jsonl_purge.os.replace", spy):
            self.purge("drop")

        self.assertEqual(observed, [True])


class LogModulePurgeTests(unittest.TestCase):
    """Both logs expose the same purge, over their own paths and locks."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_query_log_purge_uses_the_query_log(self) -> None:
        path = self.dir / "query_log.jsonl"
        _write_lines(
            path,
            [
                {"session_id": "gone", "question": "secret", "answer_preview": "a"},
                {"session_id": "stays", "question": "public"},
            ],
        )
        with patch.object(query_log_mod, "QUERY_LOG_PATH", path):
            self.assertEqual(query_log_mod.purge_session("gone"), 1)
        self.assertNotIn("secret", path.read_text(encoding="utf-8"))
        self.assertIn("public", path.read_text(encoding="utf-8"))

    def test_feedback_purge_removes_the_full_answer_too(self) -> None:
        # This log stores the answer in full rather than a preview, and
        # /reviews renders it, so a miss here is the widest of the two.
        path = self.dir / "feedback.jsonl"
        _write_lines(
            path,
            [
                {"session_id": "gone", "question": "q", "answer": "the whole answer"},
                {"session_id": "stays", "question": "q2", "answer": "kept"},
            ],
        )
        with patch.object(feedback_mod, "FEEDBACK_PATH", path):
            self.assertEqual(feedback_mod.purge_session("gone"), 1)
        remaining = path.read_text(encoding="utf-8")
        self.assertNotIn("the whole answer", remaining)
        self.assertIn("kept", remaining)


class DeleteErasesEverywhereTests(unittest.TestCase):
    """The ruling itself, end to end through the real delete path."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.query_path = self.dir / "query_log.jsonl"
        self.feedback_path = self.dir / "feedback.jsonl"

        self.store = MemorySessionStore()
        reset_session_store(self.store)
        self.addCleanup(reset_session_store, None)

        self.store.save(
            StoredSession(
                session_id="chat-1",
                user_id="user-a",
                title="t",
                turns=[StoredTurn(question="my private question", answer="a", citations=[], retrieved_sources=[])],
            )
        )
        _write_lines(
            self.query_path,
            [
                {"session_id": "chat-1", "question": "my private question"},
                {"session_id": "chat-2", "question": "someone else's question"},
            ],
        )
        _write_lines(
            self.feedback_path,
            [
                {"session_id": "chat-1", "question": "my private question", "answer": "a"},
                {"session_id": "chat-2", "question": "someone else's question", "answer": "b"},
            ],
        )
        patcher_q = patch.object(query_log_mod, "QUERY_LOG_PATH", self.query_path)
        patcher_f = patch.object(feedback_mod, "FEEDBACK_PATH", self.feedback_path)
        patcher_q.start()
        patcher_f.start()
        self.addCleanup(patcher_q.stop)
        self.addCleanup(patcher_f.stop)

    def test_deleting_a_chat_erases_its_questions_from_both_logs(self) -> None:
        self.assertTrue(reset_session("chat-1", user_id="user-a"))

        self.assertIsNone(self.store.load("chat-1"))
        for path in (self.query_path, self.feedback_path):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("my private question", text)
            self.assertIn("someone else's question", text)

    def test_another_users_delete_erases_nothing(self) -> None:
        # Ownership is checked before anything is erased, so a wrong user id
        # cannot be used to wipe someone else's log records.
        self.assertFalse(reset_session("chat-1", user_id="user-b"))

        self.assertIsNotNone(self.store.load("chat-1"))
        self.assertIn("my private question", self.query_path.read_text(encoding="utf-8"))
        self.assertIn("my private question", self.feedback_path.read_text(encoding="utf-8"))

    def test_a_failed_purge_leaves_the_thread_in_place(self) -> None:
        # The ordering guarantee. If the purge runs after the store delete,
        # this is the case that silently keeps the question text forever: the
        # thread is gone, so nothing is left to retry the erase against.
        with patch("src.chat.purge_query_log", side_effect=OSError("volume stalled")):
            with self.assertRaises(OSError):
                reset_session("chat-1", user_id="user-a")

        self.assertIsNotNone(self.store.load("chat-1"))
        self.assertIn("my private question", self.query_path.read_text(encoding="utf-8"))

    def test_a_failed_feedback_purge_also_leaves_the_thread_in_place(self) -> None:
        # Same guarantee for the second log. The query log purge has already
        # run by this point, which is the safe direction: erased more than
        # asked, rather than a deleted chat whose answers are still served.
        with patch("src.chat.purge_feedback", side_effect=OSError("volume stalled")):
            with self.assertRaises(OSError):
                reset_session("chat-1", user_id="user-a")

        self.assertIsNotNone(self.store.load("chat-1"))


if __name__ == "__main__":
    unittest.main()
