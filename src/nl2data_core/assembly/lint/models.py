"""Bounded, deterministic diagnostics for semantic assembly lint.

Lint diagnostics are stable contracts: a stable ``SAL###`` code, severity,
selected profile, semantic target path, optional authoring source location,
bounded safe message, and optional safe references.  They never carry
credentials, physical bindings, SQL/MQL, raw rows, native objects, or
unrestricted scalar values.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

LINT_DIAGNOSTIC_CODE_PATTERN = r"^SAL\d{3}$"
MAX_LINT_DIAGNOSTICS = 100
MAX_LINT_MESSAGE_CHARS = 256
MAX_LINT_REFERENCES = 8
MAX_LINT_REFERENCE_CHARS = 256
MAX_LINT_TARGET_PARTS = 64


class LintSeverity(StrEnum):
    """Severity of one lint diagnostic; only ``error`` is blocking."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class LintProfileId(StrEnum):
    """Built-in lint profile identifiers."""

    COMPATIBILITY = "compatibility"
    RECOMMENDED = "recommended"
    PRODUCTION = "production"


class _LintModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LintSourceLocation(_LintModel):
    """One bounded 1-based authoring source location."""

    line: int = Field(ge=1, le=2**31 - 1)
    column: int = Field(ge=1, le=2**31 - 1)


class LintTargetPath(_LintModel):
    """Stable semantic target path, stable across parse/export/parse trips."""

    parts: tuple[str | int, ...] = Field(default_factory=tuple, max_length=MAX_LINT_TARGET_PARTS)

    def render(self) -> str:
        path = "$"
        for part in self.parts:
            path += f"[{part}]" if isinstance(part, int) else f".{part}"
        return path


class LintReference(_LintModel):
    """One bounded safe reference, such as another semantic member path."""

    kind: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    value: str = Field(min_length=1, max_length=MAX_LINT_REFERENCE_CHARS)


class LintDiagnostic(_LintModel):
    """One deterministic lint finding with bounded safe content."""

    code: str = Field(pattern=LINT_DIAGNOSTIC_CODE_PATTERN)
    severity: LintSeverity
    profile: LintProfileId
    target_path: LintTargetPath = Field(default_factory=LintTargetPath)
    source_location: LintSourceLocation | None = None
    message: str = Field(min_length=1, max_length=MAX_LINT_MESSAGE_CHARS)
    references: tuple[LintReference, ...] = Field(
        default_factory=tuple,
        max_length=MAX_LINT_REFERENCES,
    )


class LintResultSummary(_LintModel):
    """Bounded profile-level summary of one lint run."""

    profile: LintProfileId
    profile_version: int = Field(ge=1, le=1_000)
    diagnostic_count: int = Field(default=0, ge=0, le=2**31 - 1)
    error_count: int = Field(default=0, ge=0, le=2**31 - 1)
    warning_count: int = Field(default=0, ge=0, le=2**31 - 1)
    info_count: int = Field(default=0, ge=0, le=2**31 - 1)
    has_errors: bool = False
    blocking: bool = False
    truncated: bool = False


class LintResult(_LintModel):
    """Deterministic lint result for one validated assembly input."""

    summary: LintResultSummary
    diagnostics: tuple[LintDiagnostic, ...] = Field(
        default_factory=tuple,
        max_length=MAX_LINT_DIAGNOSTICS,
    )
