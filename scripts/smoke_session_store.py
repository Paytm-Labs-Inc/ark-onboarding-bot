#!/usr/bin/env python3
"""Smoke test: session store via HTTP + server restart."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8765")


def log(results: list[str], msg: str) -> None:
    print(msg)
    results.append(msg)


# One stable browser identity for the whole run. Sessions are stored under the
# caller's id, so without this every request would be issued a fresh ark_uid by
# the middleware and the thread written by POST /api/ask would belong to a user
# that no later request is. The restart and reset checks would then be asserting
# against someone else's (empty) history and could never pass. Any value the
# cookie validator accepts will do; it stands in for one browser across the run.
SMOKE_BROWSER_ID = "smoke-session-store-0001"


def http(method: str, path: str, body: dict | None = None) -> tuple[int, str]:
    url = BASE + path
    data = None
    headers: dict[str, str] = {"Cookie": f"ark_uid={SMOKE_BROWSER_ID}"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def wait_health(timeout: int = 60) -> bool:
    for _ in range(timeout):
        try:
            with urllib.request.urlopen(BASE + "/health", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(1)
    return False


def restart_server() -> subprocess.Popen[bytes] | None:
    try:
        pids = subprocess.check_output(["lsof", "-t", "-i", ":8765"], text=True).strip().splitlines()
    except subprocess.CalledProcessError:
        pids = []
    for pid in pids:
        try:
            os.kill(int(pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
    time.sleep(2)
    proc = subprocess.Popen(
        [str(REPO_ROOT / ".venv/bin/python"), "-m", "src.web"],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if not wait_health(90):
        proc.kill()
        return None
    return proc


def main() -> int:
    results: list[str] = []
    failures = 0
    store = os.environ.get("SESSION_STORE", "memory")

    if not wait_health(5):
        log(results, "FAIL: server not running. Start with: ./deploy/run_web.sh")
        return 1

    log(results, f"Target: {BASE}")
    log(results, f"SESSION_STORE={store}")

    status, raw = http("POST", "/api/ask", {"question": "How do I enroll a host?"})
    if status != 200:
        log(results, f"FAIL POST /api/ask -> HTTP {status}")
        return 1
    ask1 = json.loads(raw)
    sid = ask1["session_id"]
    log(
        results,
        f"PASS POST /api/ask -> HTTP 200, session_id={sid}, answer_len={len(ask1.get('answer', ''))}",
    )

    status, raw = http("GET", f"/api/session/{sid}")
    if status != 200:
        log(results, f"FAIL GET /api/session before restart -> HTTP {status}")
        failures += 1
    else:
        turns = len(json.loads(raw).get("turns", []))
        log(results, f"PASS GET /api/session before restart -> HTTP 200, turns={turns}")

    log(results, "ACTION: restarting server process")
    child = restart_server()
    if child is None:
        log(results, "FAIL: server did not restart")
        return 1
    log(results, "PASS server restarted")

    status, raw = http("GET", f"/api/session/{sid}")
    if store == "redis":
        ok = status == 200
        detail = f"HTTP {status}"
        if status == 200:
            detail += f", turns={len(json.loads(raw).get('turns', []))}"
        log(results, f"{'PASS' if ok else 'FAIL'} GET /api/session after restart (redis) -> {detail}")
        failures += 0 if ok else 1
    elif status == 404:
        log(results, "PASS GET /api/session after restart -> HTTP 404 (expected for memory store)")
    elif status == 200:
        log(results, "PASS GET /api/session after restart -> HTTP 200 (session survived)")
    else:
        log(results, f"FAIL GET /api/session after restart -> HTTP {status}")
        failures += 1

    status, raw = http(
        "POST",
        "/api/ask",
        {"question": "What was my previous question?", "session_id": sid},
    )
    if status != 200:
        log(results, f"FAIL POST /api/ask follow-up -> HTTP {status}")
        failures += 1
    else:
        ask2 = json.loads(raw)
        log(
            results,
            f"PASS POST /api/ask follow-up -> HTTP 200, answer_len={len(ask2.get('answer', ''))}",
        )

    # 404 is a PASS here, not a failure. `sid` is the pre-restart thread, and
    # with SESSION_STORE=memory the restart is SUPPOSED to have lost it -- the
    # check three steps up asserts exactly that. /api/reset now 404s on a
    # missing session (it did not before the session API landed), so demanding
    # 200 made this script report OVERALL: FAIL on every memory-mode run while
    # passing under redis. A check that is always red for one backend is a
    # check nobody reads, and this script is the only end-to-end evidence the
    # Redis rollout has.
    status, _ = http("POST", "/api/reset", {"session_id": sid})
    if status not in (200, 404):
        log(results, f"FAIL POST /api/reset -> HTTP {status}")
        failures += 1
    elif status == 404:
        log(results, "PASS POST /api/reset -> HTTP 404 (thread already gone, expected for memory)")
    else:
        log(results, "PASS POST /api/reset -> HTTP 200")

    status, _ = http("GET", f"/api/session/{sid}")
    if status == 404:
        log(results, "PASS GET after reset -> HTTP 404")
    else:
        log(results, f"FAIL GET after reset -> HTTP {status} (expected 404)")
        failures += 1

    child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()

    log(results, f"OVERALL: {'PASS' if failures == 0 else 'FAIL'} ({failures} failures)")
    return failures


if __name__ == "__main__":
    sys.exit(main())
