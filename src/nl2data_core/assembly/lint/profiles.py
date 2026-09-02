"""Built-in lint profiles and the deterministic rule severity catalog.

Each built-in rule declares the severity it emits under every profile, or
``None`` when the rule does not run for that profile.  Only ``error``
severity is blocking for the selected profile.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from .models import LintProfileId, LintSeverity

LINT_PROFILE_VERSION = 1

#: Severity of every built-in rule per profile; ``None`` disables the rule.
RULE_SEVERITIES: Mapping[str, Mapping[LintProfileId, LintSeverity | None]] = {
    # Naming and ambiguity
    "SAL001": {
        LintProfileId.COMPATIBILITY: LintSeverity.WARNING,
        LintProfileId.RECOMMENDED: LintSeverity.WARNING,
        LintProfileId.PRODUCTION: LintSeverity.ERROR,
    },
    # Description quality
    "SAL002": {
        LintProfileId.COMPATIBILITY: None,
        LintProfileId.RECOMMENDED: LintSeverity.WARNING,
        LintProfileId.PRODUCTION: LintSeverity.WARNING,
    },
    "SAL003": {
        LintProfileId.COMPATIBILITY: None,
        LintProfileId.RECOMMENDED: LintSeverity.WARNING,
        LintProfileId.PRODUCTION: LintSeverity.WARNING,
    },
    # Governance readiness
    "SAL004": {
        LintProfileId.COMPATIBILITY: None,
        LintProfileId.RECOMMENDED: LintSeverity.WARNING,
        LintProfileId.PRODUCTION: LintSeverity.ERROR,
    },
    "SAL005": {
        LintProfileId.COMPATIBILITY: None,
        LintProfileId.RECOMMENDED: LintSeverity.WARNING,
        LintProfileId.PRODUCTION: LintSeverity.ERROR,
    },
    "SAL006": {
        LintProfileId.COMPATIBILITY: None,
        LintProfileId.RECOMMENDED: LintSeverity.WARNING,
        LintProfileId.PRODUCTION: LintSeverity.ERROR,
    },
    # Semantic consistency
    "SAL007": {
        LintProfileId.COMPATIBILITY: LintSeverity.WARNING,
        LintProfileId.RECOMMENDED: LintSeverity.WARNING,
        LintProfileId.PRODUCTION: LintSeverity.ERROR,
    },
    "SAL008": {
        LintProfileId.COMPATIBILITY: None,
        LintProfileId.RECOMMENDED: LintSeverity.WARNING,
        LintProfileId.PRODUCTION: LintSeverity.WARNING,
    },
    "SAL009": {
        LintProfileId.COMPATIBILITY: None,
        LintProfileId.RECOMMENDED: LintSeverity.INFO,
        LintProfileId.PRODUCTION: LintSeverity.WARNING,
    },
    # Verification-plan readiness
    "SAL010": {
        LintProfileId.COMPATIBILITY: None,
        LintProfileId.RECOMMENDED: LintSeverity.WARNING,
        LintProfileId.PRODUCTION: LintSeverity.ERROR,
    },
    "SAL011": {
        LintProfileId.COMPATIBILITY: None,
        LintProfileId.RECOMMENDED: LintSeverity.INFO,
        LintProfileId.PRODUCTION: LintSeverity.WARNING,
    },
}


class LintProfile(BaseModel):
    """One versioned lint profile with its rule severity mapping."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: LintProfileId
    version: int = Field(default=LINT_PROFILE_VERSION, ge=1, le=1_000)

    def severity_for(self, code: str) -> LintSeverity | None:
        """Return the configured severity for one rule code, or ``None``."""
        return RULE_SEVERITIES.get(code, {}).get(self.profile)


LINT_PROFILES: Mapping[LintProfileId, LintProfile] = {
    profile_id: LintProfile(profile=profile_id) for profile_id in LintProfileId
}


def lint_rule_codes() -> tuple[str, ...]:
    """Return every built-in rule code in the stable ``SAL###`` catalog."""
    return tuple(sorted(RULE_SEVERITIES))
