"""Safe structured outcomes of Semantic View resolution.

Resolution returns either a resolved projection or a structured
denial/unavailable outcome with bounded issue codes.  Issues carry only
opaque member ids and safe reason codes - never hidden fields, physical
names, credentials, or policy internals - so a denial reveals nothing
beyond the reason it was denied.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .projection import ResolvedViewProjection

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"

#: Bounded number of issues reported by one resolution attempt.
_MAX_ISSUES = 64


class ResolutionIssue(BaseModel):
    """One structured resolution issue with a safe reason code.

    ``member_id`` is an opaque semantic member reference when the issue
    concerns one member; it never reveals excluded or hidden members
    beyond the referenced id itself.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=256)
    member_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)

    def safe_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "member_id": self.member_id,
        }


class ResolutionOutcome(BaseModel):
    """Immutable result of one resolution attempt.

    ``resolved`` carries the authorized projection; ``denied`` and
    ``unavailable`` carry structured issues and never a projection, so a
    failed resolution can never leak partial semantic members.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["resolved", "denied", "unavailable"]
    projection: ResolvedViewProjection | None = None
    issues: tuple[ResolutionIssue, ...] = Field(default_factory=tuple, max_length=_MAX_ISSUES)

    @model_validator(mode="after")
    def _consistent(self) -> ResolutionOutcome:
        if self.kind == "resolved":
            if self.projection is None:
                raise ValueError("resolved outcomes must carry a projection")
            if self.issues:
                raise ValueError("resolved outcomes must not carry issues")
        else:
            if self.projection is not None:
                raise ValueError("non-resolved outcomes must not carry a projection")
            if not self.issues:
                raise ValueError("non-resolved outcomes must carry at least one issue")
        return self

    @property
    def resolved(self) -> bool:
        """Whether resolution produced an authorized projection."""
        return self.kind == "resolved"

    @property
    def denied(self) -> bool:
        """Whether resolution was denied by a trusted access constraint."""
        return self.kind == "denied"

    def issue_codes(self) -> list[str]:
        """The bounded issue codes of this outcome."""
        return [issue.code for issue in self.issues]

    def safe_payload(self) -> dict[str, Any]:
        """Serialize with safe codes, projection, and member references."""
        return {
            "kind": self.kind,
            "projection": self.projection.safe_payload() if self.projection else None,
            "issues": [issue.safe_payload() for issue in self.issues],
        }


def denied(code: str, message: str, *, member_id: str | None = None) -> ResolutionOutcome:
    """A fail-closed denial outcome with a safe reason code."""
    return ResolutionOutcome(
        kind="denied",
        issues=(ResolutionIssue(code=code, message=message, member_id=member_id),),
    )


def unavailable(code: str, message: str, *, member_id: str | None = None) -> ResolutionOutcome:
    """An unavailable outcome (missing, stale, or bounded-invalid)."""
    return ResolutionOutcome(
        kind="unavailable",
        issues=(ResolutionIssue(code=code, message=message, member_id=member_id),),
    )
