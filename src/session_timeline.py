"""Structured session diagnosis timeline from Scout data."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from src.scout import ScoutReport

_PROVISIONING_PREFIXES = (
    "workspace_",
    "repo_",
    "tool_",
    "service_",
    "codegraph_",
    "prepare_",
)

_PLACEHOLDER_NOTES = (
    "(nothing returned)",
    "(provisioning step detail not available from Scout)",
    "(no agent output",
    "(no transcript",
)


@dataclass
class TimelineHeadline:
    session_id: str
    status: str
    failed_stage: str
    error: str
    summary: str

    def opening(self) -> str:
        stage = self.failed_stage or "unknown"
        status = self.status or "unknown"
        parts = [f"Session {self.session_id} {status} at the {stage} stage."]
        if self.summary:
            parts.append(f"Task: {self.summary}")
        if self.error:
            parts.append(f"Error: {self.error}")
        return "\n".join(parts)


@dataclass
class TimelineSection:
    title: str
    items: list[str] = field(default_factory=list)
    empty_note: str = "(nothing returned)"

    def has_content(self) -> bool:
        return bool(self.items)


@dataclass
class SessionTimeline:
    headline: TimelineHeadline
    event_trail: TimelineSection
    provisioning: TimelineSection
    actions: TimelineSection
    output: TimelineSection
    transcript: TimelineSection
    meta: TimelineSection
    warnings: list[str] = field(default_factory=list)


def _strip_md(text: str) -> str:
    """Remove markdown markers — the web UI renders plain text only."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text.strip()


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("events", "runtimes", "results", "items", "actions"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
    return []


def _event_detail(event: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("message", "reason", "error", "detail"):
        text = str(event.get(key) or "").strip()
        if text:
            parts.append(text)
    data = event.get("data")
    if isinstance(data, dict):
        for key in ("message", "error", "stage"):
            text = str(data.get(key) or "").strip()
            if text and text not in parts:
                parts.append(text)
    return " — ".join(parts)


def _format_event(event: dict[str, Any]) -> str:
    kind = str(event.get("type") or event.get("event") or "event")
    stage = str(event.get("stage") or event.get("data", {}).get("stage") or "").strip()
    at = str(event.get("at") or event.get("timestamp") or event.get("created_at") or "").strip()
    detail = _event_detail(event)
    label = kind.replace("_", " ")
    if stage:
        label += f" ({stage})"
    if detail:
        label += f" — {detail}"
    if at:
        label = f"{at} · {label}"
    return label


def _is_provisioning_event(event: dict[str, Any]) -> bool:
    kind = str(event.get("type") or event.get("event") or "").lower()
    return any(kind.startswith(prefix) for prefix in _PROVISIONING_PREFIXES)


def _format_runtime_event(event: dict[str, Any]) -> str:
    kind = str(event.get("type") or event.get("event") or "runtime").replace("_", " ")
    detail = _event_detail(event)
    status = str(event.get("status") or "").strip()
    line = kind
    if status:
        line += f" ({status})"
    if detail:
        line += f" — {detail}"
    return line


def _format_action(result: dict[str, Any]) -> str:
    name = str(
        result.get("action")
        or result.get("name")
        or result.get("stage")
        or "action"
    )
    ok = result.get("ok")
    if ok is True:
        status = "passed"
    elif ok is False:
        status = "failed"
    else:
        status = str(result.get("status") or "unknown")
    message = str(
        result.get("message")
        or result.get("error")
        or result.get("stderr")
        or result.get("stdout")
        or ""
    ).strip()
    line = f"{name}: {status}"
    if message:
        line += f" — {message[:300]}"
    exit_code = result.get("exit_code")
    if exit_code is not None and ok is False:
        line += f" (exit {exit_code})"
    return line


def _tail_lines(text: str, limit: int = 8) -> list[str]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    return lines[-limit:]


def _headline(report: ScoutReport) -> TimelineHeadline:
    return TimelineHeadline(
        session_id=report.session_id,
        status=report.status,
        failed_stage=report.failed_stage or report.stage,
        error=report.error,
        summary=report.session_summary,
    )


def _synthetic_event_lines(report: ScoutReport) -> list[str]:
    show = report.raw_show if isinstance(report.raw_show, dict) else {}
    items: list[str] = []
    if report.status:
        stage = report.failed_stage or report.stage or "unknown"
        items.append(f"Session marked {report.status} at stage {stage}")
    if show.get("agent"):
        items.append(f"Agent {show['agent']} on compute {report.compute_name or 'unknown'}")
    if show.get("created_at") and show.get("ended_at"):
        items.append(f"Ran from {show['created_at']} to {show['ended_at']}")
    if show.get("workspace_runtime_id"):
        items.append(f"Workspace runtime {show['workspace_runtime_id']}")
    return items


def _event_trail(report: ScoutReport) -> TimelineSection:
    events = report.raw_events or _as_dict_list(_parse_json_maybe(report.events_tail))
    items = [_format_event(event) for event in events[:40]]
    if not items:
        items = _synthetic_event_lines(report)
    return TimelineSection("What happened", items)


def _provisioning(report: ScoutReport) -> TimelineSection:
    items: list[str] = []
    runtime_events = report.raw_runtime_events
    if runtime_events:
        items.extend(_format_runtime_event(event) for event in runtime_events[:30])
    else:
        session_events = report.raw_events or _as_dict_list(_parse_json_maybe(report.events_tail))
        prov = [_format_event(event) for event in session_events if _is_provisioning_event(event)]
        items.extend(prov[:20])

    if report.workspace_name:
        items.insert(
            0,
            f"Workspace {report.workspace_name} on compute {report.compute_name or 'unknown'}",
        )
    return TimelineSection("Setup & provisioning", items)


def _actions(report: ScoutReport) -> TimelineSection:
    results = report.raw_action_results or _as_dict_list(
        _parse_json_maybe(report.action_results)
    )
    items = [_format_action(result) for result in results[:20]]
    return TimelineSection("Verify & actions", items)


def _looks_like_session_json(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("{") and '"session"' in stripped and '"stage"' in stripped


def _output(report: ScoutReport) -> TimelineSection:
    text = report.output_tail.strip()
    if not text or _looks_like_session_json(text):
        return TimelineSection("Recent output", [])
    return TimelineSection("Recent output", _tail_lines(text, limit=10))


def _transcript(report: ScoutReport) -> TimelineSection:
    text = report.transcript_excerpt.strip()
    if not text or _looks_like_session_json(text):
        return TimelineSection("Agent transcript", [])
    return TimelineSection("Agent transcript", _tail_lines(text, limit=8))


def _meta(report: ScoutReport) -> TimelineSection:
    items: list[str] = []
    if report.flow_name:
        items.append(f"Flow: {report.flow_name}")
    if report.workspace_name:
        items.append(f"Workspace: {report.workspace_name}")
    if report.compute_name:
        items.append(f"Compute: {report.compute_name}")
    if report.artifacts:
        items.append("Artifacts: " + ", ".join(report.artifacts[:8]))
    if report.cost_usd is not None:
        items.append(f"Cost: ${report.cost_usd:.4f}")
    return TimelineSection("Session details", items)


def _parse_json_maybe(text: str) -> Any:
    if not text or not text.strip().startswith(("[", "{")):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def build_timeline(report: ScoutReport) -> SessionTimeline:
    """Build a structured diagnosis timeline from a Scout report."""
    return SessionTimeline(
        headline=_headline(report),
        event_trail=_event_trail(report),
        provisioning=_provisioning(report),
        actions=_actions(report),
        output=_output(report),
        transcript=_transcript(report),
        meta=_meta(report),
        warnings=list(report.gather_errors),
    )


def _is_useful_item(item: str) -> bool:
    return not any(item.startswith(note) for note in _PLACEHOLDER_NOTES)


def _render_section(section: TimelineSection) -> list[str]:
    useful = [_strip_md(item) for item in section.items if _is_useful_item(item)]
    if not useful:
        return []
    lines = [section.title + ":"]
    lines.extend(f"  • {_strip_md(item)}" for item in useful)
    return lines


def timeline_to_markdown(timeline: SessionTimeline) -> str:
    """Render the timeline as plain, readable text for the chat UI."""
    lines = [timeline.headline.opening(), ""]

    for section in (
        timeline.event_trail,
        timeline.provisioning,
        timeline.actions,
        timeline.output,
        timeline.transcript,
        timeline.meta,
    ):
        block = _render_section(section)
        if block:
            lines.extend(block)
            lines.append("")

    return "\n".join(lines).strip()


def timeline_to_dict(timeline: SessionTimeline) -> dict[str, Any]:
    """JSON-serialisable timeline for the web UI."""
    return {
        "headline": asdict(timeline.headline),
        "sections": [
            {"title": s.title, "items": s.items}
            for s in (
                timeline.event_trail,
                timeline.provisioning,
                timeline.actions,
                timeline.output,
                timeline.transcript,
                timeline.meta,
            )
        ],
        "warnings": timeline.warnings,
    }


def prepend_timeline(report: ScoutReport, body: str) -> str:
    """Prepend the diagnosis timeline above case-specific content."""
    diagnosis = timeline_to_markdown(build_timeline(report))
    body = _strip_md(body.strip())
    if not body:
        return diagnosis
    return f"{diagnosis}\n\n{body}"
