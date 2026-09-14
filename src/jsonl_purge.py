"""Erase one session's records from a JSONL log, in place and atomically.

A user delete is meant to erase everywhere, but the two observability logs were
never on the delete path: both carry the question text and a `session_id` that
links it back to a thread, so a deleted chat left its questions on the volume
indefinitely. This is the shared half of that erase. It lives in one module so
the query log and the feedback log cannot drift apart in how they do it.

Not housekeeping: every caller here is servicing an explicit user delete.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from pathlib import Path


def purge_session_records(
    path: Path,
    session_id: str,
    *,
    lock: threading.Lock,
    lock_timeout: float,
) -> int:
    """Drop every record carrying *session_id*. Returns how many were removed.

    Takes the appending writer's own lock rather than a lock of its own. That
    is load-bearing: `os.replace` swaps the inode, so an append running
    concurrently would write into the file this rewrote away and vanish at the
    rename. Sharing the writer's lock is what makes the two mutually exclusive.
    """
    if not session_id:
        return 0
    if not lock.acquire(timeout=lock_timeout):
        raise OSError(f"{path.name} is busy; the delete could not erase it")
    try:
        if not path.is_file():
            return 0
        removed = 0
        # Same directory, so the replace below is a rename within one
        # filesystem and therefore atomic: a reader sees the whole old file or
        # the whole new one, and a crash mid-write cannot truncate the log.
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out, path.open(
                "r", encoding="utf-8"
            ) as src:
                for line in src:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        record = json.loads(stripped)
                    except json.JSONDecodeError:
                        # Unparseable, so there is no session id to match on.
                        # Keep it: a truncated line is an interleaved write,
                        # not a record anyone can be held to have authored.
                        out.write(stripped + "\n")
                        continue
                    if isinstance(record, dict) and record.get("session_id") == session_id:
                        removed += 1
                        continue
                    out.write(stripped + "\n")
                out.flush()
                os.fsync(out.fileno())
            if not removed:
                # Nothing matched. Leave the original untouched rather than
                # replacing it with a rewritten copy: no reason to churn the
                # inode, and no window in which a reader could lose an append.
                os.unlink(tmp_name)
                return 0
            # mkstemp creates 0600. Carry the log's own mode across so the
            # purge does not quietly re-permission a file other things read.
            os.chmod(tmp_name, stat.S_IMODE(os.stat(path).st_mode))
            os.replace(tmp_name, path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return removed
    finally:
        lock.release()
