"""Structured JSON completions via the configured answer backend."""

from __future__ import annotations

import json
import re
from typing import Any

from src.answer import _call_model, _parse_json_response


def completion_json(prompt: str, *, model: str | None = None) -> dict[str, Any]:
    """Call the LLM and parse a JSON object from the response."""
    raw = _call_model(prompt, model=model)
    try:
        return _parse_json_response(raw)
    except (json.JSONDecodeError, ValueError, KeyError):
        # Fallback: extract first {...} block.
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise
