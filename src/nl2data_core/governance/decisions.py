"""Deterministic default-deny governance evaluation.

Evaluation is pure and side-effect free: typed facts in, typed decision
out.  Anything not explicitly allowed - or missing entirely - is denied;
operations outside the supported set are reported as unsupported rather
than guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass

from nl2data_core.governance.models import (
    SUPPORTED_OPERATIONS,
    GovernanceDecision,
    GovernanceDecisionResult,
    GovernanceFacts,
    PolicyScope,
)


@dataclass(frozen=True)
class PolicyEvaluator:
    """Evaluates typed governance facts against an explicit policy scope."""

    def evaluate(
        self, facts: GovernanceFacts | None, scope: PolicyScope | None
    ) -> GovernanceDecisionResult:
        """Return an allow/deny/unsupported decision.

        Missing inputs are denied without broadening access.
        """
        if facts is None:
            return GovernanceDecisionResult(
                decision=GovernanceDecision.DENY,
                reasons=("missing governance facts",),
            )
        if scope is None:
            return GovernanceDecisionResult(
                decision=GovernanceDecision.DENY,
                reasons=("missing policy scope",),
            )

        reasons: list[str] = []

        if facts.operation not in SUPPORTED_OPERATIONS:
            return GovernanceDecisionResult(
                decision=GovernanceDecision.UNSUPPORTED,
                reasons=(f"operation '{facts.operation}' is not supported by governance",),
                policy_fingerprint=scope.policy_fingerprint,
            )

        if not facts.source_id:
            reasons.append("missing source fact")
        elif facts.source_id not in scope.source_ids:
            reasons.append(f"source '{facts.source_id}' is not in policy scope")

        if not facts.resource_ids:
            reasons.append("missing resource facts")
        else:
            for resource in sorted(facts.resource_ids):
                if resource not in scope.resource_ids:
                    reasons.append(f"resource '{resource}' is not in policy scope")

        if not facts.field_ids:
            reasons.append("missing field facts")
        else:
            for field in sorted(facts.field_ids):
                if field not in scope.field_ids:
                    reasons.append(f"field '{field}' is not in policy scope")

        if facts.operation not in scope.operation_ids:
            reasons.append(f"operation '{facts.operation}' is not in policy scope")

        if scope.tenant_scope_fingerprint is not None:
            if facts.tenant_scope_fingerprint is None:
                reasons.append(
                    "tenant-scoped execution requires a trusted tenant scope fingerprint"
                )
            elif facts.tenant_scope_fingerprint != scope.tenant_scope_fingerprint:
                reasons.append("tenant scope fingerprint does not match the policy scope")
            if scope.isolation_profile is None:
                reasons.append("tenant-scoped policy requires an isolation profile")
        if (
            scope.isolation_profile is not None
            and facts.isolation_profile != scope.isolation_profile
        ):
            reasons.append("isolation profile does not match the policy scope")

        if reasons:
            return GovernanceDecisionResult(
                decision=GovernanceDecision.DENY,
                reasons=tuple(reasons),
                policy_fingerprint=scope.policy_fingerprint,
            )
        return GovernanceDecisionResult(
            decision=GovernanceDecision.ALLOW,
            reasons=("explicitly allowed by policy scope",),
            policy_fingerprint=scope.policy_fingerprint,
        )
