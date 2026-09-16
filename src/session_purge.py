"""Erase one session's observability records — the shared half of a delete.

Two paths remove a thread: the explicit delete a user asks for, and the
retention sweep that drops an archived chat once it ages out. Both owe the
same erase, so both call this. It lives in its own module because the store
performs one of those deletes and cannot import `src.chat`, which performs
the other.
"""

from __future__ import annotations

from src.feedback import purge_session as purge_feedback
from src.query_log import purge_session as purge_query_log


def purge_session_logs(session_id: str) -> None:
    """Drop this session's records from the query log and the feedback log.

    Raises OSError when a log is busy, and a caller must read that as "the
    delete did not happen": run this BEFORE dropping the thread. Either order
    can fail halfway, and this is the order whose halfway state is the safe
    one — the thread is still there and the erase can be retried. The other
    order leaves the thread gone and the question text on the volume, which is
    the rule broken with nothing left to retry against.
    """
    purge_query_log(session_id)
    purge_feedback(session_id)
