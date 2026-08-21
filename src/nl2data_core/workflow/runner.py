"""The P1 workflow execution runner behind the workflow execution port.

Routes one query through the deterministic governed path: semantic plan,
plan validation, governance decision, artifact-bound authorization,
adapter execution, and protected public outcome construction.  When the
required P1 components are absent the runner reports not-configured -
exactly like the P0 fallback - so the engine never fabricates results.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from nl2data.errors import (
    ErrorCategory,
    ErrorCode,
    ErrorRecord,
    as_error_record,
)
from nl2data.models import OutcomeStatus, QueryOutcome, QueryRequest, QueryResult
from nl2data_core.adapters.models import AdapterLimits, ValidationContext
from nl2data_core.adapters.protocol import QueryAdapter
from nl2data_core.adapters.sql.compile import compile_plan
from nl2data_core.engine.ports import NOT_CONFIGURED_MESSAGE
from nl2data_core.governance.authorization import AuthorizationIssuer, AuthorizationVerifier
from nl2data_core.governance.decisions import PolicyEvaluator
from nl2data_core.governance.models import (
    EffectiveLimits,
    GovernanceDecision,
    GovernanceFacts,
    PolicyScope,
)
from nl2data_core.planning.models import (
    SemanticQueryPlan,
    validate_plan_structure,
)
from nl2data_core.planning.validation import (
    AuthorizedView,
    validate_plan_against_view,
)
from nl2data_core.workflow.protection import ResultProtectionError, protect_result


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PlanResolver(Protocol):
    """Maps a public request to a backend-neutral semantic plan."""

    def resolve(self, request: QueryRequest) -> SemanticQueryPlan | None:
        """Return the plan for ``request`` or ``None`` when unresolvable."""
        ...


class StaticPlanResolver:
    """Resolves every request to one fixed plan (deterministic conformance)."""

    def __init__(self, plan: SemanticQueryPlan | None) -> None:
        self._plan = plan

    def resolve(self, request: QueryRequest) -> SemanticQueryPlan | None:
        return self._plan


def _outcome(
    request: QueryRequest,
    *,
    status: OutcomeStatus,
    error: ErrorRecord | None = None,
    result: QueryResult | None = None,
    workflow_id: str | None = None,
) -> QueryOutcome:
    return QueryOutcome(
        status=status,
        request_id=request.request_id,
        workflow_id=workflow_id,
        result=result,
        error=error,
        attempts_used=1,
    )


class QueryExecutionRunner:
    """Deterministic governed query path behind the workflow port.

    Every component is injected so the path stays testable and
    adapter-neutral; governance never interprets SQL and the adapter never
    interprets policy.
    """

    def __init__(
        self,
        *,
        adapter: QueryAdapter | None = None,
        policy_scope: PolicyScope | None = None,
        view: AuthorizedView | None = None,
        plan_resolver: PlanResolver | None = None,
        evaluator: PolicyEvaluator | None = None,
        issuer: AuthorizationIssuer | None = None,
        verifier: AuthorizationVerifier | None = None,
        effective_limits: EffectiveLimits | None = None,
        ttl_seconds: float = 60.0,
    ) -> None:
        self._adapter = adapter
        self._policy_scope = policy_scope
        self._view = view
        self._plan_resolver = plan_resolver
        self._evaluator = evaluator or PolicyEvaluator()
        self._issuer = issuer or AuthorizationIssuer()
        self._verifier = verifier or AuthorizationVerifier()
        self._effective_limits = effective_limits or EffectiveLimits()
        self._ttl_seconds = ttl_seconds

    def is_configured(self) -> bool:
        """Whether the full P1 path is available; otherwise the fallback applies."""
        return (
            self._adapter is not None
            and self._policy_scope is not None
            and self._view is not None
            and self._plan_resolver is not None
        )

    async def execute(self, request: QueryRequest) -> QueryOutcome:
        """Execute one query through the governed path."""
        adapter = self._adapter
        policy_scope = self._policy_scope
        view = self._view
        plan_resolver = self._plan_resolver
        if adapter is None or policy_scope is None or view is None or plan_resolver is None:
            return _outcome(
                request,
                status=OutcomeStatus.NOT_CONFIGURED,
                error=ErrorRecord(
                    code=ErrorCode.NOT_CONFIGURED,
                    category=ErrorCategory.NOT_CONFIGURED,
                    message=NOT_CONFIGURED_MESSAGE,
                    retryable=False,
                ),
            )

        plan = plan_resolver.resolve(request)
        if plan is None:
            return self._rejected(
                request,
                ErrorRecord(
                    code=ErrorCode.PLAN_VALIDATION_FAILED,
                    category=ErrorCategory.VALIDATION,
                    message="no semantic plan could be resolved for the request",
                ),
            )

        structure = validate_plan_structure(plan)
        view_result = validate_plan_against_view(plan, view=view)
        if not structure.valid or not view_result.valid:
            issue_codes = sorted(set(structure.issue_codes() + view_result.issue_codes()))
            return self._rejected(
                request,
                ErrorRecord(
                    code=ErrorCode.PLAN_VALIDATION_FAILED,
                    category=ErrorCategory.VALIDATION,
                    message="semantic plan failed validation",
                    details={"issue_codes": ",".join(issue_codes)},
                ),
            )

        decision = self._evaluator.evaluate(self._facts_from_plan(plan), policy_scope)
        if decision.decision != GovernanceDecision.ALLOW:
            return self._rejected(
                request,
                ErrorRecord(
                    code=ErrorCode.GOVERNANCE_DENIED,
                    category=ErrorCategory.GOVERNANCE,
                    message="query is denied by policy",
                    details={"reasons": "; ".join(decision.reasons)},
                ),
            )

        try:
            sql = compile_plan(plan)
            context = ValidationContext(snapshot_fingerprint=plan.lineage.catalog_fingerprint)
            parsed = adapter.parse(sql, context)
            validated = adapter.validate(parsed, context)
        except Exception as error:
            return self._rejected(request, as_error_record(error))

        capabilities = adapter.capabilities()
        authorization = self._issuer.issue(
            policy_scope=policy_scope,
            adapter_type=capabilities.adapter_type,
            source_id=plan.source_id,
            operation="select",
            artifact_fingerprint=validated.fingerprint,
            effective_limits=self._effective_limits,
            mandatory_filter_fingerprints=plan.filter_fingerprints(),
            ttl_seconds=self._ttl_seconds,
        )
        verification = self._verifier.verify(
            authorization,
            artifact_fingerprint=validated.fingerprint,
            adapter_type=capabilities.adapter_type,
            source_id=plan.source_id,
            operation="select",
            filter_fingerprints=plan.filter_fingerprints(),
        )
        if not verification.verified:
            return self._rejected(
                request,
                ErrorRecord(
                    code=ErrorCode.AUTHORIZATION_REJECTED,
                    category=ErrorCategory.GOVERNANCE,
                    message="execution authorization could not be verified",
                    details={"reasons": "; ".join(verification.reasons)},
                ),
            )

        context = context.model_copy(
            update={
                "limits": context.limits.model_copy(
                    update={"max_result_rows": authorization.effective_limits.max_rows}
                )
                if context.limits is not None
                else AdapterLimits(max_result_rows=authorization.effective_limits.max_rows),
                "execution_timeout_seconds": authorization.effective_limits.max_execution_seconds,
                "max_result_bytes": authorization.effective_limits.max_result_bytes,
            }
        )
        try:
            execution = await adapter.execute(validated, context)
        except Exception as error:
            return _outcome(
                request,
                status=OutcomeStatus.FAILED,
                error=as_error_record(error),
            )

        try:
            result = protect_result(execution, plan=plan, limits=authorization.effective_limits)
        except ResultProtectionError as error:
            return _outcome(
                request,
                status=OutcomeStatus.FAILED,
                error=error.to_record(),
            )

        return _outcome(
            request,
            status=OutcomeStatus.SUCCEEDED,
            result=result,
            workflow_id=f"workflow-{uuid4().hex[:16]}",
        )

    async def close(self) -> None:
        """Release the bound adapter (idempotent)."""
        if self._adapter is not None:
            await self._adapter.close()

    @staticmethod
    def _facts_from_plan(plan: SemanticQueryPlan) -> GovernanceFacts:
        resource_ids = (
            {plan.binding.object_id} if plan.binding is not None else {plan.root_entity_id}
        )
        return GovernanceFacts(
            source_id=plan.source_id,
            operation="select",
            resource_ids=frozenset(resource_ids),
            field_ids=plan.field_ids(),
            filter_fingerprints=plan.filter_fingerprints(),
        )

    @staticmethod
    def _rejected(request: QueryRequest, error: ErrorRecord) -> QueryOutcome:
        return _outcome(request, status=OutcomeStatus.REJECTED, error=error)
