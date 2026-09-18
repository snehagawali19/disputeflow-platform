"""PII minimization for prompts, logs, and audit payloads."""

from __future__ import annotations

import re
from typing import Any


EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
IP_RE = re.compile(r"\b(\d{1,3}\.){3}\d{1,3}\b")


def redact_email(value: str | None) -> str:
    if not value:
        return ""
    return EMAIL_RE.sub(lambda m: f"{m.group(1)[:2]}***@{m.group(2)}", value)


def redact_ip(value: str | None) -> str:
    if not value:
        return ""
    parts = value.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.*.*"
    return "***"


def clip(text: str, limit: int = 1200) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "…"


def sanitize_trace(entry: dict[str, Any]) -> dict[str, Any]:
    safe = dict(entry)
    for key in ("prompt", "raw_content", "rebuttal_letter"):
        if key in safe and isinstance(safe[key], str):
            safe[key] = clip(safe[key], 400)
    return safe
