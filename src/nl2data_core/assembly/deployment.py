"""Ephemeral deployment secret resolution for publish-time verification."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from .models import DeploymentBinding


class DeploymentSecretResolver(Protocol):
    """Host resolver returning a credential only for the current call."""

    def resolve(self, binding: DeploymentBinding) -> str: ...


class DeploymentConnectionVerifier(Protocol):
    """Host verifier consuming an ephemeral resolved credential."""

    def verify(self, binding: DeploymentBinding, resolved_secret: str) -> bool: ...


class DeploymentVerificationResult(BaseModel):
    """Safe verification result that never contains a resolved credential."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    binding_id: str
    reference_scheme: str
    valid: bool
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")


def verify_deployment_binding(
    binding: DeploymentBinding,
    *,
    resolver: DeploymentSecretResolver,
    verifier: DeploymentConnectionVerifier,
) -> DeploymentVerificationResult:
    """Resolve, verify, discard, and return only a bounded safe summary."""
    try:
        resolved_secret = resolver.resolve(binding)
        if not isinstance(resolved_secret, str) or not resolved_secret:
            return DeploymentVerificationResult(
                binding_id=binding.binding_id,
                reference_scheme=binding.reference_scheme,
                valid=False,
                reason_code="secret_unavailable",
            )
        valid = verifier.verify(binding, resolved_secret)
    except Exception:
        return DeploymentVerificationResult(
            binding_id=binding.binding_id,
            reference_scheme=binding.reference_scheme,
            valid=False,
            reason_code="verification_failed",
        )
    finally:
        resolved_secret = ""
    return DeploymentVerificationResult(
        binding_id=binding.binding_id,
        reference_scheme=binding.reference_scheme,
        valid=valid,
        reason_code="verified" if valid else "connection_rejected",
    )