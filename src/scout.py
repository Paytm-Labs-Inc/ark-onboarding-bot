"""Gather full session context from the Ark control plane (Scout)."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from src.ark_client import ArkClient, ArkError, default_client

_MAX_TEXT = 8000


def _truncate(value: Any, limit: int = _MAX_TEXT) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _safe_call(label: str, fn: Any) -> tuple[str, Any]:
    try:
        return label, fn()
    except ArkError as exc:
        return label, {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return label, {"error": f"{type(exc).__name__}: {exc}"}


@dataclass
class ScoutReport:
    session_id: str
    found: bool = True
    session_summary: str = ""
    status: str = ""
    stage: str = ""
    error: str = ""
    failed_stage: str = ""
    flow_name: str = ""
    workspace_name: str = ""
    compute_name: str = ""
    events_tail: str = ""
    output_tail: str = ""
    transcript_excerpt: str = ""
    action_results: str = ""
    flow_definition: str = ""
    workspace_detail: str = ""
    runtime_list: str = ""
    runtime_logs: str = ""
    worktree_stat: str = ""
    worktree_diff: str = ""
    stage_diffs: str = ""
    artifacts: list[str] = field(default_factory=list)
    cost_usd: float | None = None
    raw_show: dict[str, Any] = field(default_factory=dict)
    raw_events: list[dict[str, Any]] = field(default_factory=list)
    raw_action_results: list[dict[str, Any]] = field(default_factory=list)
    raw_runtime_list: list[dict[str, Any]] = field(default_factory=list)
    raw_runtime_events: list[dict[str, Any]] = field(default_factory=list)
    gather_errors: list[str] = field(default_factory=list)

    def retrieval_query(self) -> str:
        parts = [
            self.error,
            self.failed_stage or self.stage,
            self.flow_name,
            self.session_summary,
        ]
        return " ".join(p for p in parts if p).strip() or self.session_id

    def to_context_blob(self) -> str:
        sections = [
            ("Session", self.session_summary or self.status),
            ("Error", self.error),
            ("Stage", self.failed_stage or self.stage),
            ("Flow", self.flow_name),
            ("Workspace", self.workspace_name),
            ("Events", self.events_tail),
            ("Output", self.output_tail),
            ("Transcript", self.transcript_excerpt),
            ("Action results", self.action_results),
            ("Worktree", self.worktree_stat),
            ("Runtime", self.runtime_list),
        ]
        lines = [f"## {title}\n{body}" for title, body in sections if body]
        if self.artifacts:
            lines.append("## Artifacts\n" + "\n".join(f"- {a}" for a in self.artifacts))
        if self.cost_usd is not None:
            lines.append(f"## Cost\n${self.cost_usd:.4f}")
        return "\n\n".join(lines)


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("events", "runtimes", "results", "items", "actions"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
    return []


def _runtime_id(runtime: dict[str, Any]) -> str:
    return str(runtime.get("id") or runtime.get("runtime_id") or "").strip()


def _normalize_read_text(value: Any) -> str:
    """Turn session_read output/transcript results into log text, not session JSON."""
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{") and '"session"' in text:
            try:
                return _normalize_read_text(json.loads(text))
            except json.JSONDecodeError:
                return text
        return text
    if isinstance(value, dict):
        if value.get("error"):
            return ""
        # Some RPC paths echo the session row when no log tail exists yet.
        if isinstance(value.get("session"), dict) and not any(
            key in value for key in ("lines", "output", "text", "transcript", "content", "events")
        ):
            return ""
        for key in ("lines", "output", "text", "transcript", "content"):
            nested = value.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
            if isinstance(nested, list):
                parts = [str(line).strip() for line in nested if str(line).strip()]
                if parts:
                    return "\n".join(parts)
        return ""
    return str(value).strip()


def _fetch_runtime_list(
    client: ArkClient,
    *,
    session_id: str,
    workspace_name: str,
) -> Any:
    """Try the parameter shapes the HTTP RPC surface accepts for runtime_list."""
    attempts: tuple[dict[str, str], ...] = (
        {"session_id": session_id},
        {"sessionId": session_id},
    )
    if workspace_name:
        attempts += ({"workspace_name": workspace_name},)
    last: ArkError | None = None
    for params in attempts:
        try:
            return client.workspace("runtime_list", **params)
        except ArkError as exc:
            last = exc
            if "Invalid params" in str(exc) or "Unknown method" in str(exc):
                continue
            raise
    if last:
        raise last
    return {}


_SUCCESS_STATUSES = frozenset({"completed", "archived"})


def session_succeeded(report: ScoutReport) -> bool:
    """True when the session finished successfully and needs no failure triage."""
    if not report.found:
        return False
    status = (report.status or "").strip().lower()
    if status not in _SUCCESS_STATUSES:
        return False
    if (report.error or "").strip():
        return False
    show = report.raw_show if isinstance(report.raw_show, dict) else {}
    stage_results = show.get("stage_results") or {}
    if isinstance(stage_results, dict):
        for result in stage_results.values():
            if isinstance(result, dict) and result.get("ok") is False:
                return False
    return True


def _extract_error(show: dict[str, Any]) -> tuple[str, str]:
    error = ""
    failed_stage = ""
    if isinstance(show, dict):
        error = str(show.get("error") or show.get("message") or "").strip()
        stage_results = show.get("stage_results") or {}
        if isinstance(stage_results, dict):
            for stage, result in stage_results.items():
                if isinstance(result, dict) and result.get("ok") is False:
                    failed_stage = str(stage)
                    if not error:
                        error = str(result.get("message") or result.get("error") or "")
                    break
        if not failed_stage:
            failed_stage = str(show.get("stage") or "")
    return error, failed_stage


def gather_scout_report(
    session_id: str,
    *,
    client: ArkClient | None = None,
) -> ScoutReport:
    """Parallel-fetch everything Scout needs for one Ark session."""
    client = client or default_client()
    session_id = session_id.strip().lower()
    report = ScoutReport(session_id=session_id)

    try:
        show = client.session_read("show", sessionId=session_id)
    except ArkError as exc:
        report.found = False
        report.error = str(exc)
        report.gather_errors.append(str(exc))
        return report

    if not show:
        report.found = False
        report.error = f"Session {session_id} not found."
        return report

    # RPC show wraps the row: {"session": {...}, "cursor": "..."}.
    show_dict = show if isinstance(show, dict) else {"raw": show}
    if isinstance(show_dict.get("session"), dict):
        show_dict = show_dict["session"]
    report.raw_show = show_dict
    config = show_dict.get("config") if isinstance(show_dict.get("config"), dict) else {}
    report.status = str(show_dict.get("status") or "")
    report.stage = str(show_dict.get("stage") or "")
    report.session_summary = str(show_dict.get("summary") or show_dict.get("name") or "")
    report.flow_name = str(show_dict.get("flow") or config.get("flow") or "")
    report.workspace_name = str(config.get("workspace") or show_dict.get("workspace") or "")
    report.compute_name = str(
        show_dict.get("compute_name") or config.get("compute") or show_dict.get("compute") or ""
    )
    if show_dict.get("error"):
        report.error = str(show_dict["error"])
        report.failed_stage = str(show_dict.get("stage") or "")
    else:
        report.error, report.failed_stage = _extract_error(show_dict)

    flow_name = report.flow_name
    workspace_name = report.workspace_name

    tasks: dict[str, Any] = {
        "events": lambda: client.session_read("events", sessionId=session_id),
        "output": lambda: client.session_read("output", sessionId=session_id, lines=200),
        "transcript": lambda: client.session_read("transcript", sessionId=session_id),
        "action_results": lambda: client.session_read("action_results", sessionId=session_id),
        "worktree_diff": lambda: client.worktree("diff", sessionId=session_id),
        "stage_diffs": lambda: client.worktree("stage_diffs", sessionId=session_id),
        "artifacts": lambda: client.session_artifacts("list", sessionId=session_id),
        "costs": lambda: client.costs("session", sessionId=session_id),
    }
    if flow_name:
        tasks["flow"] = lambda: client.flow("show", name=flow_name)
    if workspace_name:
        tasks["workspace"] = lambda: client.workspace("show", name=workspace_name)
        tasks["runtime_list"] = lambda: _fetch_runtime_list(
            client,
            session_id=session_id,
            workspace_name=workspace_name,
        )

    results: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_safe_call, k, fn): k for k, fn in tasks.items()}
        for future in as_completed(futures):
            label, value = future.result()
            results[label] = value
            if isinstance(value, dict) and value.get("error"):
                report.gather_errors.append(f"{label}: {value['error']}")

    events_raw = results.get("events")
    report.raw_events = _as_dict_list(events_raw)
    report.events_tail = _truncate(events_raw)

    report.output_tail = _truncate(_normalize_read_text(results.get("output")))
    report.transcript_excerpt = _truncate(_normalize_read_text(results.get("transcript")))

    action_results_raw = results.get("action_results")
    report.raw_action_results = _as_dict_list(action_results_raw)
    report.action_results = _truncate(action_results_raw)
    report.flow_definition = _truncate(results.get("flow"))
    report.workspace_detail = _truncate(results.get("workspace"))
    runtime_list_raw = results.get("runtime_list")
    report.raw_runtime_list = _as_dict_list(runtime_list_raw)
    report.runtime_list = _truncate(runtime_list_raw)

    runtime_id = str(show_dict.get("workspace_runtime_id") or "").strip()
    if not runtime_id:
        for runtime in report.raw_runtime_list:
            runtime_id = _runtime_id(runtime)
            if runtime_id:
                break
    if runtime_id:
        try:
            runtime_events = client.workspace("runtime_events", id=runtime_id, limit=200)
            report.raw_runtime_events = _as_dict_list(runtime_events)
            report.runtime_logs = _truncate(runtime_events)
        except ArkError as exc:
            report.gather_errors.append(f"runtime_events: {exc}")
    report.worktree_stat = _truncate(
        (results.get("worktree_diff") or {}).get("stat")
        if isinstance(results.get("worktree_diff"), dict)
        else results.get("worktree_diff")
    )
    report.worktree_diff = _truncate(
        (results.get("worktree_diff") or {}).get("diff")
        if isinstance(results.get("worktree_diff"), dict)
        else ""
    )
    report.stage_diffs = _truncate(results.get("stage_diffs"))

    artifacts_raw = results.get("artifacts") or {}
    if isinstance(artifacts_raw, dict):
        items = artifacts_raw.get("artifacts") or []
        report.artifacts = [
            str(a.get("name", a)) for a in items if isinstance(a, dict)
        ][:20]

    cost_raw = results.get("costs")
    if isinstance(cost_raw, dict):
        for key in ("totalUsd", "total_usd", "usd", "costUsd"):
            if key in cost_raw:
                try:
                    report.cost_usd = float(cost_raw[key])
                except (TypeError, ValueError):
                    pass
                break

    return report
