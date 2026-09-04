"""Fail-closed immutable verification policy profiles."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nl2data_core.canonical import strict_sha256_fingerprint
from nl2data_core.verification.models import VerificationLayer

_BUILTIN_POLICY_IDS = frozenset({"compatibility-v1", "production-v1"})


class VerificationPolicy(BaseModel):
    """Versioned requirements that determine whether suite evidence passes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$")
    policy_version: Literal[1] = 1
    required_layers: frozenset[VerificationLayer]
    minimum_enabled_smoke_cases: int = Field(default=0, ge=0, le=1_000)
    minimum_enabled_semantic_cases: int = Field(default=0, ge=0, le=1_000)
    require_all_enabled_cases_pass: bool = True
    compatibility_label: str | None = Field(default=None, max_length=64)
    fingerprint: str = Field(default="", pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _validate_and_fingerprint(self) -> VerificationPolicy:
        if VerificationLayer.STRUCTURAL not in self.required_layers:
            raise ValueError("every verification policy must require Layer 1")
        if (
            self.minimum_enabled_smoke_cases > 0
            and VerificationLayer.SMOKE not in self.required_layers
        ):
            raise ValueError("a smoke-case minimum requires Layer 2")
        if (
            self.minimum_enabled_semantic_cases > 0
            and VerificationLayer.SEMANTIC not in self.required_layers
        ):
            raise ValueError("a semantic-case minimum requires Layer 3")
        expected = (
            _builtin_payload(self.policy_id) if self.policy_id in _BUILTIN_POLICY_IDS else None
        )
        payload = self.canonical_payload()
        if expected is not None and payload != expected:
            raise ValueError(f"built-in policy identity '{self.policy_id}' cannot be weakened")
        object.__setattr__(self, "fingerprint", strict_sha256_fingerprint(payload))
        return self

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "required_layers": sorted(layer.value for layer in self.required_layers),
            "minimum_enabled_smoke_cases": self.minimum_enabled_smoke_cases,
            "minimum_enabled_semantic_cases": self.minimum_enabled_semantic_cases,
            "require_all_enabled_cases_pass": self.require_all_enabled_cases_pass,
            "compatibility_label": self.compatibility_label,
        }


def _builtin_payload(policy_id: str) -> dict[str, Any]:
    if policy_id == "compatibility-v1":
        return {
            "policy_id": policy_id,
            "policy_version": 1,
            "required_layers": [VerificationLayer.STRUCTURAL.value],
            "minimum_enabled_smoke_cases": 0,
            "minimum_enabled_semantic_cases": 0,
            "require_all_enabled_cases_pass": True,
            "compatibility_label": "structural_only",
        }
    return {
        "policy_id": policy_id,
        "policy_version": 1,
        "required_layers": sorted(layer.value for layer in VerificationLayer),
        "minimum_enabled_smoke_cases": 1,
        "minimum_enabled_semantic_cases": 1,
        "require_all_enabled_cases_pass": True,
        "compatibility_label": None,
    }


COMPATIBILITY_POLICY = VerificationPolicy(**_builtin_payload("compatibility-v1"))
PRODUCTION_POLICY = VerificationPolicy(**_builtin_payload("production-v1"))

BUILTIN_POLICIES: Mapping[str, VerificationPolicy] = MappingProxyType(
    {
        COMPATIBILITY_POLICY.policy_id: COMPATIBILITY_POLICY,
        PRODUCTION_POLICY.policy_id: PRODUCTION_POLICY,
    }
)


def validate_stricter_policy(
    policy: VerificationPolicy, *, baseline: VerificationPolicy = PRODUCTION_POLICY
) -> VerificationPolicy:
    """Accept a custom policy only when it is at least as strict as its baseline."""
    if policy.policy_id in _BUILTIN_POLICY_IDS:
        if policy != baseline:
            raise ValueError("built-in policy identities cannot be used for host policies")
        return policy
    if not policy.required_layers.issuperset(baseline.required_layers):
        raise ValueError("host policy cannot remove required layers")
    if policy.minimum_enabled_smoke_cases < baseline.minimum_enabled_smoke_cases:
        raise ValueError("host policy cannot lower the smoke-case minimum")
    if policy.minimum_enabled_semantic_cases < baseline.minimum_enabled_semantic_cases:
        raise ValueError("host policy cannot lower the semantic-case minimum")
    if baseline.require_all_enabled_cases_pass and not policy.require_all_enabled_cases_pass:
        raise ValueError("host policy cannot permit enabled case failures")
    return policy