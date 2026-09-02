"""Safe message construction, scalar truncation, and secret redaction.

Lint messages are bounded safe summaries.  Secret-like scalar content is
redacted, and every message is truncated to the diagnostic message bound.
"""

from __future__ import annotations

import re

from .models import MAX_LINT_MESSAGE_CHARS

MAX_SCALAR_SUMMARY_CHARS = 48
MIN_DESCRIPTION_CHARS = 16

_SECRET_MARKER_PATTERN = re.compile(
    r"(?i)password|passwd|secret|token|api[_-]?key|authorization|credential|private[_-]?key"
)
REDACTED_SCALAR_SUMMARY = "[redacted]"

_PLACEHOLDER_DESCRIPTIONS = frozenset(
    {
        "tbd",
        "tbd.",
        "todo",
        "to do",
        "fixme",
        "n/a",
        "na",
        "none",
        "placeholder",
        "describe here",
        "description",
        "pending",
        "wip",
        "example",
        "sample",
        "test",
        "unknown",
    }
)


def safe_scalar_summary(value: str, *, max_chars: int = MAX_SCALAR_SUMMARY_CHARS) -> str:
    """Return a bounded, secret-redacted summary of one scalar value."""
    if _SECRET_MARKER_PATTERN.search(value) is not None:
        return REDACTED_SCALAR_SUMMARY
    collapsed = " ".join(value.split())
    if len(collapsed) > max_chars:
        return collapsed[: max_chars - 1] + "…"
    return collapsed


def bounded_message(text: str) -> str:
    """Truncate one diagnostic message to the stable message bound."""
    if len(text) > MAX_LINT_MESSAGE_CHARS:
        return text[: MAX_LINT_MESSAGE_CHARS - 1] + "…"
    return text


def is_missing_description(value: str) -> bool:
    """Whether a description is absent or only whitespace."""
    return not value.strip()


def is_weak_description(value: str, *, min_chars: int = MIN_DESCRIPTION_CHARS) -> bool:
    """Whether a description is missing or too short to be meaningful."""
    return len(value.strip()) < min_chars


def is_placeholder_description(value: str) -> bool:
    """Whether a description is a deterministic placeholder spelling."""
    return value.strip().lower() in _PLACEHOLDER_DESCRIPTIONS
