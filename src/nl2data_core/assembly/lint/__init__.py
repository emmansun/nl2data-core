"""Deterministic semantic assembly lint rules, profiles, and engine."""

from .engine import lint_authoring, lint_draft
from .messages import (
    MAX_SCALAR_SUMMARY_CHARS,
    REDACTED_SCALAR_SUMMARY,
    bounded_message,
    safe_scalar_summary,
)
from .models import (
    LINT_DIAGNOSTIC_CODE_PATTERN,
    MAX_LINT_DIAGNOSTICS,
    LintDiagnostic,
    LintProfileId,
    LintReference,
    LintResult,
    LintResultSummary,
    LintSeverity,
    LintSourceLocation,
    LintTargetPath,
)
from .profiles import LINT_PROFILES, LintProfile, lint_rule_codes
from .snapshot import snapshot_from_authoring, snapshot_from_draft

__all__ = [
    "LINT_DIAGNOSTIC_CODE_PATTERN",
    "LINT_PROFILES",
    "MAX_LINT_DIAGNOSTICS",
    "MAX_SCALAR_SUMMARY_CHARS",
    "LintDiagnostic",
    "LintProfile",
    "LintProfileId",
    "LintReference",
    "LintResult",
    "LintResultSummary",
    "LintSeverity",
    "LintSourceLocation",
    "LintTargetPath",
    "REDACTED_SCALAR_SUMMARY",
    "bounded_message",
    "lint_authoring",
    "lint_draft",
    "lint_rule_codes",
    "safe_scalar_summary",
    "snapshot_from_authoring",
    "snapshot_from_draft",
]
