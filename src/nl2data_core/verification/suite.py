"""Fixed-order fail-closed Verification Suite orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.verification.execution import (
    VerificationExecutionCache,
    VerificationExecutionContext,
)
from nl2data_core.verification.models import (
    VerificationLayer,
    VerificationLayerEvidence,
    VerificationPlan,
    VerificationStatus,
    VerificationSuiteEvidence,
)
from nl2data_core.verification.policy import VerificationPolicy
from nl2data_core.verification.semantic import SemanticContractEvaluator
from nl2data_core.verification.smoke import (
    RunnableVerificationExecutor,
    SmokeVerificationEvaluator,
)

SUITE_RUNNER_ID = "nl2data-core-verification-suite"
SUITE_RUNNER_VERSION = 1


def _not_run(layer: VerificationLayer, issue_code: str) -> VerificationLayerEvidence:
    return VerificationLayerEvidence(
        layer=layer,
        status=VerificationStatus.NOT_RUN,
        issue_codes=(issue_code,),
    )


def publication_verification_classification(
    evidence: VerificationSuiteEvidence | None,
) -> str:
    """Classify old publications explicitly instead of fabricating verification."""
    return "legacy_unverified" if evidence is None else evidence.policy_profile


def compatibility_suite_evidence(
    *,
    structural_evidence: VerificationLayerEvidence,
    draft_id: str,
    draft_revision: int,
    bundle_fingerprint: str,
    manifest_fingerprint: str,
    tenant_scope_fingerprint: str,
    source_scope_fingerprint: str,
) -> VerificationSuiteEvidence:
    """Build explicit structural-only evidence without async execution."""
    from nl2data_core.verification.policy import COMPATIBILITY_POLICY

    status = (
        VerificationStatus.PASSED
        if structural_evidence.status is VerificationStatus.PASSED
        else VerificationStatus.FAILED
    )
    return VerificationSuiteEvidence(
        status=status,
        policy_profile=COMPATIBILITY_POLICY.policy_id,
        policy_version=COMPATIBILITY_POLICY.policy_version,
        policy_fingerprint=COMPATIBILITY_POLICY.fingerprint,
        runner_id=SUITE_RUNNER_ID,
        runner_version=SUITE_RUNNER_VERSION,
        draft_id=draft_id,
        draft_revision=draft_revision,
        bundle_fingerprint=bundle_fingerprint,
        manifest_fingerprint=manifest_fingerprint,
        tenant_scope_fingerprint=tenant_scope_fingerprint,
        source_scope_fingerprint=source_scope_fingerprint,
        layers=(
            structural_evidence,
            _not_run(VerificationLayer.SMOKE, "not_required"),
            _not_run(VerificationLayer.SEMANTIC, "not_required"),
        ),
        issue_codes=(() if status is VerificationStatus.PASSED else ("layer_1_failed",)),
    )


class VerificationSuiteRunner:
    """Run Layer 1, Layer 2, and Layer 3 in fixed fail-closed order."""

    runner_id = SUITE_RUNNER_ID
    runner_version = SUITE_RUNNER_VERSION

    def __init__(self, *, executor: RunnableVerificationExecutor) -> None:
        self._executor = executor

    async def run(
        self,
        *,
        plan: VerificationPlan | None,
        policy: VerificationPolicy,
        structural_evidence: VerificationLayerEvidence,
        context: VerificationExecutionContext,
        draft_id: str,
        draft_revision: int,
    ) -> VerificationSuiteEvidence:
        issues = self._plan_policy_issues(plan, policy)
        if context.policy.fingerprint != policy.fingerprint:
            issues.append("context_policy_mismatch")
        layers: list[VerificationLayerEvidence] = [structural_evidence]
        if structural_evidence.status is not VerificationStatus.PASSED:
            layers.extend(
                (
                    _not_run(VerificationLayer.SMOKE, "layer_1_failed"),
                    _not_run(VerificationLayer.SEMANTIC, "layer_1_failed"),
                )
            )
            issues.append("layer_1_failed")
            return self._evidence(
                status=VerificationStatus.FAILED,
                plan=plan,
                policy=policy,
                context=context,
                draft_id=draft_id,
                draft_revision=draft_revision,
                layers=tuple(layers),
                issue_codes=tuple(dict.fromkeys(issues)),
                executor_used=False,
            )
        if issues or plan is None:
            layers.extend(
                (
                    _not_run(VerificationLayer.SMOKE, "not_required"),
                    _not_run(VerificationLayer.SEMANTIC, "not_required"),
                )
            )
            status = VerificationStatus.FAILED if issues else VerificationStatus.PASSED
            return self._evidence(
                status=status,
                plan=plan,
                policy=policy,
                context=context,
                draft_id=draft_id,
                draft_revision=draft_revision,
                layers=tuple(layers),
                issue_codes=tuple(issues),
                executor_used=False,
            )

        cache = VerificationExecutionCache()
        smoke = SmokeVerificationEvaluator(executor=self._executor, cache=cache)
        semantic = SemanticContractEvaluator(executor=self._executor, cache=cache)
        executor_used = bool(plan.smoke_cases or plan.semantic_cases)
        try:
            smoke_layer = await self._run_layer(
                smoke.evaluate_layer(plan.smoke_cases, context),
                layer=VerificationLayer.SMOKE,
                timeout_seconds=min(
                    plan.deadlines.layer_ms / 1000,
                    context.remaining_seconds(),
                ),
            )
            layers.append(smoke_layer)
            if (
                VerificationLayer.SMOKE in policy.required_layers
                and smoke_layer.status is not VerificationStatus.PASSED
            ):
                layers.append(_not_run(VerificationLayer.SEMANTIC, "layer_2_failed"))
                issues.append("layer_2_failed")
            else:
                semantic_layer = await self._run_layer(
                    semantic.evaluate_layer(plan.semantic_cases, context),
                    layer=VerificationLayer.SEMANTIC,
                    timeout_seconds=min(
                        plan.deadlines.layer_ms / 1000,
                        context.remaining_seconds(),
                    ),
                )
                layers.append(semantic_layer)
                if (
                    VerificationLayer.SEMANTIC in policy.required_layers
                    and semantic_layer.status is not VerificationStatus.PASSED
                ):
                    issues.append("layer_3_failed")
        finally:
            cache.release()
        status = (
            VerificationStatus.PASSED
            if not issues
            and all(
                next(item for item in layers if item.layer is required).status
                is VerificationStatus.PASSED
                for required in policy.required_layers
            )
            else VerificationStatus.FAILED
        )
        return self._evidence(
            status=status,
            plan=plan,
            policy=policy,
            context=context,
            draft_id=draft_id,
            draft_revision=draft_revision,
            layers=tuple(layers),
            issue_codes=tuple(issues),
            executor_used=executor_used,
        )

    async def _run_layer(
        self,
        evaluation: Awaitable[VerificationLayerEvidence],
        *,
        layer: VerificationLayer,
        timeout_seconds: float,
    ) -> VerificationLayerEvidence:
        try:
            return await asyncio.wait_for(evaluation, timeout=max(0.0, timeout_seconds))
        except TimeoutError:
            return VerificationLayerEvidence(
                layer=layer,
                status=VerificationStatus.TIMED_OUT,
                issue_codes=("layer_deadline_exhausted",),
            )

    def _plan_policy_issues(
        self, plan: VerificationPlan | None, policy: VerificationPolicy
    ) -> list[str]:
        if plan is None:
            return [] if policy.policy_id == "compatibility-v1" else ["plan_required"]
        issues: list[str] = []
        if (
            plan.policy_profile != policy.policy_id
            or plan.policy_version != policy.policy_version
        ):
            issues.append("policy_plan_mismatch")
        enabled_smoke = sum(case.enabled for case in plan.smoke_cases)
        enabled_semantic = sum(case.enabled for case in plan.semantic_cases)
        if enabled_smoke < policy.minimum_enabled_smoke_cases:
            issues.append("smoke_case_minimum_not_met")
        if enabled_semantic < policy.minimum_enabled_semantic_cases:
            issues.append("semantic_case_minimum_not_met")
        return issues

    def _evidence(
        self,
        *,
        status: VerificationStatus,
        plan: VerificationPlan | None,
        policy: VerificationPolicy,
        context: VerificationExecutionContext,
        draft_id: str,
        draft_revision: int,
        layers: tuple[VerificationLayerEvidence, ...],
        issue_codes: tuple[str, ...],
        executor_used: bool,
    ) -> VerificationSuiteEvidence:
        values = {
            "status": status,
            "policy_profile": policy.policy_id,
            "policy_version": policy.policy_version,
            "policy_fingerprint": policy.fingerprint,
            "plan_fingerprint": plan.fingerprint if plan is not None else None,
            "runner_id": self.runner_id,
            "runner_version": self.runner_version,
            "draft_id": draft_id,
            "draft_revision": draft_revision,
            "bundle_fingerprint": context.candidate.fingerprint,
            "manifest_fingerprint": sha256_fingerprint(context.manifest.canonical_payload()),
            "tenant_scope_fingerprint": context.tenant_scope_fingerprint,
            "source_scope_fingerprint": context.source_scope_fingerprint,
            "layers": layers,
            "issue_codes": issue_codes,
        }
        if executor_used:
            values.update(
                {
                    "executor_id": self._executor.executor_id,
                    "executor_capability_fingerprint": self._executor.capability_fingerprint,
                }
            )
        return VerificationSuiteEvidence.model_validate(values)


def validate_bound_evidence(
    evidence: VerificationSuiteEvidence,
    *,
    plan: VerificationPlan | None,
    policy: VerificationPolicy,
    context: VerificationExecutionContext,
    draft_id: str,
    draft_revision: int,
    executor: RunnableVerificationExecutor | None,
) -> bool:
    """Symmetrically validate every reusable suite evidence identity."""
    expected_executor_id = executor.executor_id if executor is not None else None
    expected_capabilities = executor.capability_fingerprint if executor is not None else None
    canonical_evidence = VerificationSuiteEvidence.model_validate(evidence.model_dump())
    return (
        evidence.status is VerificationStatus.PASSED
        and evidence.fingerprint == canonical_evidence.fingerprint
        and _satisfies_policy(evidence, plan=plan, policy=policy)
        and evidence.plan_fingerprint == (plan.fingerprint if plan is not None else None)
        and evidence.policy_profile == policy.policy_id
        and evidence.policy_version == policy.policy_version
        and evidence.policy_fingerprint == policy.fingerprint
        and evidence.draft_id == draft_id
        and evidence.draft_revision == draft_revision
        and evidence.bundle_fingerprint == context.candidate.fingerprint
        and evidence.manifest_fingerprint
        == sha256_fingerprint(context.manifest.canonical_payload())
        and evidence.tenant_scope_fingerprint == context.tenant_scope_fingerprint
        and evidence.source_scope_fingerprint == context.source_scope_fingerprint
        and evidence.runner_id == SUITE_RUNNER_ID
        and evidence.runner_version == SUITE_RUNNER_VERSION
        and evidence.executor_id == expected_executor_id
        and evidence.executor_capability_fingerprint == expected_capabilities
    )


def evidence_satisfies_policy(
    evidence: VerificationSuiteEvidence,
    *,
    policy: VerificationPolicy,
) -> bool:
    """Check safe persisted evidence against a policy without execution context."""
    if evidence.status is not VerificationStatus.PASSED:
        return False
    canonical_evidence = VerificationSuiteEvidence.model_validate(evidence.model_dump())
    return evidence.fingerprint == canonical_evidence.fingerprint and _satisfies_policy(
        evidence,
        plan=None,
        policy=policy,
    )


def _satisfies_policy(
    evidence: VerificationSuiteEvidence,
    *,
    plan: VerificationPlan | None,
    policy: VerificationPolicy,
) -> bool:
    layers = {layer.layer: layer for layer in evidence.layers}
    if not policy.required_layers.issubset(layers):
        return False
    if any(
        layers[layer].status is not VerificationStatus.PASSED
        for layer in policy.required_layers
    ):
        return False
    if plan is None:
        if policy.required_layers == {VerificationLayer.STRUCTURAL}:
            return True
        if evidence.plan_fingerprint is None:
            return False
        return all(
            bool(layers[layer].cases)
            and all(
                case.status is VerificationStatus.PASSED
                for case in layers[layer].cases
            )
            for layer in policy.required_layers
            if layer is not VerificationLayer.STRUCTURAL
        )

    expected_cases = {
        VerificationLayer.SMOKE: plan.smoke_cases,
        VerificationLayer.SEMANTIC: plan.semantic_cases,
    }
    minimums = {
        VerificationLayer.SMOKE: policy.minimum_enabled_smoke_cases,
        VerificationLayer.SEMANTIC: policy.minimum_enabled_semantic_cases,
    }
    for layer, cases in expected_cases.items():
        if layer not in policy.required_layers:
            continue
        enabled_cases = {case.case_id for case in cases if case.enabled}
        if len(enabled_cases) < minimums[layer]:
            return False
        observed_cases = {case.case_id: case for case in layers[layer].cases}
        if not enabled_cases.issubset(observed_cases):
            return False
        if policy.require_all_enabled_cases_pass and any(
            observed_cases[case_id].status is not VerificationStatus.PASSED
            for case_id in enabled_cases
        ):
            return False
    return True
