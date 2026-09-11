# Create an Ark session for Case 2 testing

Case 2 needs a session that **gets past triage** and **fails on a code/test error** — not an arkd startup crash.

Your current sessions fail at **triage** with `Cannot find module './986.js'` → Ask Ark correctly shows **Case 3 (infra)**.

## Step 1 — Avoid the broken Cursor runtime

Your failed sessions use `"launch_executor": "cursor"`. The Cursor executor bundle on your Mac is broken.

When dispatching, **do not** pass `runtime` for cursor. Let Ark use the default Claude/runtime path.

If you use the Ark CLI:

```bash
# Good — no --runtime cursor
ark session start ark-feature \
  --workspace modeltest-modeltest-ark-onboarding-bot \
  --compute aneetta-mac \
  --summary "Case2 test"
```

## Step 2 — Plant a small intentional test failure

On branch `case2-debug-test` (push to the repo your workspace clones):

```bash
cd ~/ark-onboarding-bot
git checkout -b case2-debug-test
```

Create `tests/test_case2_probe.py`:

```python
"""Intentional failure for Ask Ark Case 2 testing. Delete after demo."""

import unittest


class Case2ProbeTests(unittest.TestCase):
    def test_intentional_probe_failure(self) -> None:
        self.fail("Intentional Case 2 probe failure — fix this assertion for the demo")


if __name__ == "__main__":
    unittest.main()
```

Push the branch:

```bash
git add tests/test_case2_probe.py
git commit -m "test: intentional Case 2 probe failure for session debug demo"
git push -u origin case2-debug-test
```

Make sure your workspace clones this branch (or merge to the branch Ark uses).

## Step 3 — Dispatch the session

```bash
cd ~/ark-onboarding-bot
chmod +x scripts/dispatch-case2-test.sh
./scripts/dispatch-case2-test.sh
```

Copy the `s-...` session id from the output.

## Step 4 — Wait for failure

Poll until status is `failed` and stage is past `triage` (e.g. `verify` or `implement`):

```bash
curl -s "$ARK_API_URL" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"session/read","params":{"op":"show","sessionId":"s-PASTE-ID"}}'
```

Look for an error mentioning `test_case2_probe` or `AssertionError` — not `986.js`.

## Step 5 — Test in Ask Ark

1. Restart `python -m src.web`
2. Paste the session id into Ask Ark
3. You should see:
   - Diagnosis timeline at the top
   - **Needs fix** badge
   - A fix plan
   - **Approve plan** / **Reject plan** buttons

## Step 6 — Test approve (optional)

Set in `.env`:

```bash
ARK_DEFAULT_WORKSPACE=modeltest-modeltest-ark-onboarding-bot
ARK_DEFAULT_COMPUTE=aneetta-mac
ARK_FIX_FLOW=ark-feature
```

Click **Approve plan** — Ark should start a fix dispatch session.

## If it still shows Case 3

| Symptom | Fix |
|---------|-----|
| Error mentions `986.js` / `ark-darwin` | Still dying at triage — fix arkd or avoid cursor runtime |
| Classifier says cannot_fix | Error may look like infra; check Scout timeline for test failure text |
| No Approve plan button | Session classified as Case 1 or 3 — need verify-stage test failure |

## Quick local UI test (no Ark session)

Unit tests already cover Case 2 without a real session:

```bash
python3 -m unittest tests.test_session_debug.SessionDebugTests.test_debug_session_needs_fix_shows_plan_gate -v
```

## Cleanup after demo

```bash
git checkout main
git branch -D case2-debug-test
git push origin --delete case2-debug-test
# remove tests/test_case2_probe.py
```
