"""The P1 workflow execution runner behind the workflow execution port.

Routes one query through the deterministic governed path: semantic IR,
IR validation, governance decision, artifact-bound authorization,
adapter execution, and protected public outcome construction.  When the
required P1 components are absent the runner reports not-configured -
exactly like the P0 fallback - so the engine never fabricates results.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from nl2data.errors import (
    ErrorCategory,
    ErrorCode,
    ErrorRecord,
    NL2DataError,
    as_error_record,
)
from nl2data.models import (
    OutcomeStatus,
    QueryClarification,
    QueryOutcome,
    QueryRequest,
    QueryResult,
)
from nl2data_core.adapters.models import AdapterLimits, ValidationContext
from nl2data_core.adapters.protocol import QueryAdapter
from nl2data_core.adapters.sql.compile import compile_ir
from nl2data_core.ai.models import MultiEntityIntent
from nl2data_core.engine.ports import NOT_CONFIGURED_MESSAGE
from nl2data_core.governance.authorization import AuthorizationIssuer, AuthorizationVerifier
from nl2data_core.governance.decisions import PolicyEvaluator
from nl2data_core.governance.models import (
    EffectiveLimits,
    GovernanceDecision,
    GovernanceFacts,
    PolicyScope,
)
from nl2data_core.planning.ir.models import SemanticQueryIR
from nl2data_core.planning.ir.validation import validate_ir
from nl2data_core.planning.join_planner import JoinPlannerOutcome
from nl2data_core.planning.models import PhysicalBinding
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.tenancy.models import TenantScopeContext
from nl2data_core.tenancy.validation import validate_tenant_scope
from nl2data_core.workflow.durable import (
    IdempotencyConflictError,
    IdempotencyStatus,
    IdempotencyStore,
    terminal_outcome_fingerprint,
)
from nl2data_core.workflow.models import (
    TERMINAL_STATUSES,
    WorkflowState,
    WorkflowStateError,
    WorkflowStatus,
)
from nl2data_core.workflow.protection import ResultProtectionError, protect_result
from nl2data_core.workflow.store import StateStore
from nl2data_core.workflow.transitions import transition


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PlanResolver(Protocol):
    """Maps a public request to a backend-neutral semantic IR."""

    def resolve(self, request: QueryRequest) -> SemanticQueryIR | None:
        """Return the IR for ``request`` or ``None`` when unresolvable."""
        ...


class JoinPlanner(Protocol):
    """Deterministic planner from a governed multi-entity intent to a plan."""

    def plan(self, intent: MultiEntityIntent) -> JoinPlannerOutcome:
        """Return the structured deterministic join-plan outcome."""
        ...


class StaticPlanResolver:
    """Resolves every request to one fixed IR (deterministic conformance)."""

    def __init__(self, ir: SemanticQueryIR | None) -> None:
        self._ir = ir

    def resolve(self, request: QueryRequest) -> SemanticQueryIR | None:
        return self._ir


@dataclass(frozen=True)
class QueryExecutionComponents:
    """Frozen snapshot of the governed components bound to a runner.

    The deterministic runtime consumes this snapshot so it never reaches
    into runner internals; the snapshot itself is immutable and carries
    no raw payloads.
    """

    adapter: QueryAdapter
    policy_scope: PolicyScope
    view: AuthorizedView
    plan_resolver: PlanResolver
    evaluator: PolicyEvaluator
    issuer: AuthorizationIssuer
    verifier: AuthorizationVerifier
    effective_limits: EffectiveLimits
    ttl_seconds: float
    tenant_context: TenantScopeContext | None


def _outcome(
    request: QueryRequest,
    *,
    status: OutcomeStatus,
    error: ErrorRecord | None = None,
    result: QueryResult | None = None,
    clarification: QueryClarification | None = None,
    workflow_id: str | None = None,
    tenant_scope_fingerprint: str | None = None,
) -> QueryOutcome:
    return QueryOutcome(
        status=status,
        request_id=request.request_id,
        workflow_id=workflow_id,
        tenant_scope_fingerprint=tenant_scope_fingerprint,
        result=result,
        clarification=clarification,
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
        tenant_context: TenantScopeContext | None = None,
        state_store: StateStore | None = None,
        idempotency_ttl_seconds: float = 86_400.0,
        binding: PhysicalBinding | None = None,
        ir_compiler: Callable[[SemanticQueryIR], str] | None = None,
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
        self._tenant_context = tenant_context
        self._state_store = state_store
        self._idempotency_ttl_seconds = idempotency_ttl_seconds
        self._binding = binding
        self._ir_compiler = ir_compiler or (
            lambda ir: compile_ir(ir, binding=self._binding)
        )

    def is_configured(self) -> bool:
        """Whether the full P1 path is available; otherwise the fallback applies."""
        return (
            self._adapter is not None
            and self._policy_scope is not None
            and self._view is not None
            and self._plan_resolver is not None
        )

    @property
    def view(self) -> AuthorizedView | None:
        """The authorized view bound to the governed path."""
        return self._view

    @property
    def policy_scope(self) -> PolicyScope | None:
        """The policy scope bound to the governed path."""
        return self._policy_scope

    @property
    def plan_resolver(self) -> PlanResolver | None:
        """The plan resolver bound to the governed path."""
        return self._plan_resolver

    @property
    def state_store(self) -> StateStore | None:
        """The durable state store bound to the governed path, if any."""
        return self._state_store

    def components(self) -> QueryExecutionComponents | None:
        """A frozen snapshot of the bound governed components, or ``None``.

        ``None`` exactly when the runner reports not-configured, so the
        deterministic runtime can fall back to the same P1 behavior.
        """
        adapter = self._adapter
        policy_scope = self._policy_scope
        view = self._view
        plan_resolver = self._plan_resolver
        if adapter is None or policy_scope is None or view is None or plan_resolver is None:
            return None
        return QueryExecutionComponents(
            adapter=adapter,
            policy_scope=policy_scope,
            view=view,
            plan_resolver=plan_resolver,
            evaluator=self._evaluator,
            issuer=self._issuer,
            verifier=self._verifier,
            effective_limits=self._effective_limits,
            ttl_seconds=self._ttl_seconds,
            tenant_context=self._tenant_context,
        )

    @property
    def tenant_context(self) -> TenantScopeContext | None:
        """The trusted tenant scope bound to the governed path."""
        return self._tenant_context

    @property
    def adapter_type(self) -> str | None:
        """The bound adapter's declared type, when an adapter is present."""
        if self._adapter is None:
            return None
        return self._adapter.capabilities().adapter_type

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

        tenant_denial = self._tenant_denial(request)
        if tenant_denial is not None:
            return tenant_denial

        ir = plan_resolver.resolve(request)
        if ir is None:
            return self._rejected(
                request,
                ErrorRecord(
                    code=ErrorCode.PLAN_VALIDATION_FAILED,
                    category=ErrorCategory.VALIDATION,
                    message="no semantic IR could be resolved for the request",
                ),
            )

        return await self.execute_ir(request, ir)

    def _tenant_denial(self, request: QueryRequest) -> QueryOutcome | None:
        """Reject requests whose tenant scope cannot be established safely.

        The trusted context is host-integration input only; a client hint
        is untrusted routing metadata that can neither establish authority
        nor override the trusted context.
        """
        hint = request.context.tenant_hint if request.context is not None else None
        if hint is None and self._tenant_context is None:
            return None
        validation = validate_tenant_scope(
            self._tenant_context,
            client_tenant_hint=hint,
        )
        if validation.valid:
            return None
        return self._rejected(
            request,
            ErrorRecord(
                code=ErrorCode.TENANT_CONTEXT_REJECTED,
                category=ErrorCategory.GOVERNANCE,
                message="tenant-scoped execution was denied by trusted context validation",
                details={"reasons": "; ".join(validation.reasons)},
            ),
        )

    async def execute_ir(self, request: QueryRequest, ir: SemanticQueryIR) -> QueryOutcome:
        """Execute an already-built semantic IR through the governed boundary.

        This is the shared execution boundary for the P1 structured-IR
        path and the AI intent handoff: IR validation, tenant-scope
        validation, governance decision, artifact-bound authorization,
        adapter execution, and protected result construction.
        When a durable ``state_store`` is bound, execution additionally
        persists transition snapshots, enforces idempotency, and replays
        terminal outcome references instead of re-executing completed work.
        """
        tenant_denial = self._tenant_denial(request)
        if tenant_denial is not None:
            return tenant_denial
        if self._adapter is None or self._policy_scope is None or self._view is None:
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
        if self._state_store is None:
            return await self._execute_governed(request, ir)
        try:
            return await self._execute_durable(request, ir)
        except NL2DataError as error:
            return _outcome(
                request, status=OutcomeStatus.FAILED, error=as_error_record(error)
            )

    async def _execute_governed(
        self,
        request: QueryRequest,
        ir: SemanticQueryIR,
        *,
        workflow_id: str | None = None,
    ) -> QueryOutcome:
        """Run validation, governance, authorization, adapter, and protection.

        This is the shared governed execution boundary for the P1
        structured-IR path, the AI intent handoff, and the durable
        composition; ``workflow_id`` is the durable workflow when set.
        """
        scope_fingerprint = (
            self._tenant_context.scope_fingerprint
            if self._tenant_context is not None
            else None
        )
        isolation_profile = (
            self._tenant_context.tenant.isolation_profile.value
            if self._tenant_context is not None
            else None
        )
        adapter = self._adapter
        policy_scope = self._policy_scope
        view = self._view
        if adapter is None or policy_scope is None or view is None:
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

        if self._tenant_context is not None and (
            policy_scope.tenant_scope_fingerprint != scope_fingerprint
            or policy_scope.isolation_profile != isolation_profile
        ):
            return self._rejected(
                request,
                ErrorRecord(
                    code=ErrorCode.GOVERNANCE_DENIED,
                    category=ErrorCategory.GOVERNANCE,
                    message="tenant-scoped execution requires a matching tenant policy",
                    retryable=False,
                ),
            )

        ir_result = validate_ir(ir, view=view)
        if not ir_result.valid:
            return self._rejected(
                request,
                ErrorRecord(
                    code=ErrorCode.PLAN_VALIDATION_FAILED,
                    category=ErrorCategory.VALIDATION,
                    message="semantic IR failed validation",
                    details={"issue_codes": ",".join(ir_result.issue_codes())},
                ),
            )

        decision = self._evaluator.evaluate(
            self._facts_from_ir(
                ir,
                binding=self._binding,
                tenant_scope_fingerprint=scope_fingerprint,
                isolation_profile=isolation_profile,
            ),
            policy_scope,
        )
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
            sql = self._ir_compiler(ir)
            context = ValidationContext(
                snapshot_fingerprint=ir.provenance.catalog_fingerprint
            )
            parsed = adapter.parse(sql, context)
            validated = adapter.validate(parsed, context)
        except Exception as error:
            return self._rejected(request, as_error_record(error))

        capabilities = adapter.capabilities()
        authorization = self._issuer.issue(
            policy_scope=policy_scope,
            adapter_type=capabilities.adapter_type,
            source_id=ir.source_id,
            operation="select",
            artifact_fingerprint=validated.fingerprint,
            tenant_scope_fingerprint=scope_fingerprint,
            isolation_profile=isolation_profile,
            effective_limits=self._effective_limits,
            mandatory_filter_fingerprints=ir.filter_fingerprints(),
            ttl_seconds=self._ttl_seconds,
        )
        verification = self._verifier.verify(
            authorization,
            artifact_fingerprint=validated.fingerprint,
            adapter_type=capabilities.adapter_type,
            source_id=ir.source_id,
            operation="select",
            filter_fingerprints=ir.filter_fingerprints(),
            tenant_scope_fingerprint=scope_fingerprint,
            isolation_profile=isolation_profile,
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
            result = protect_result(
                execution, ir=ir, binding=self._binding, limits=authorization.effective_limits
            )
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
            workflow_id=workflow_id or f"workflow-{uuid4().hex[:16]}",
            tenant_scope_fingerprint=scope_fingerprint,
        )

    async def _execute_durable(
        self, request: QueryRequest, ir: SemanticQueryIR
    ) -> QueryOutcome:
        """Durable composition: persist transitions and enforce idempotency.

        The in-memory store remains the default when no durable path is
        configured; this path only runs with an explicit store.  Idempotency
        records suppress duplicate submissions, and repeated terminal
        requests receive the safe stored outcome reference without
        re-executing external work.  A crash between external execution and
        terminal commit leaves a RUNNING checkpoint with evidence for
        at-least-once reconciliation - never a silent success claim.
        """
        store = self._state_store
        assert store is not None
        scope_fingerprint = (
            self._tenant_context.scope_fingerprint
            if self._tenant_context is not None
            else None
        )
        idempotency: IdempotencyStore | None = (
            store if isinstance(store, IdempotencyStore) else None
        )
        existing_idempotency = (
            idempotency.get_idempotency(
                request.request_id, tenant_scope_fingerprint=scope_fingerprint
            )
            if idempotency is not None
            else None
        )
        workflow_id = (
            existing_idempotency.workflow_id
            if existing_idempotency is not None
            else self._resolve_workflow_id(request)
        )
        if idempotency is not None:
            try:
                record = idempotency.reserve_idempotency(
                    request.request_id,
                    request_id=request.request_id,
                    workflow_id=workflow_id,
                    tenant_scope_fingerprint=scope_fingerprint,
                    expires_at=datetime.now(UTC)
                    + timedelta(seconds=self._idempotency_ttl_seconds),
                )
            except IdempotencyConflictError as error:
                return self._rejected(request, error.to_record())
            if (
                record.status == IdempotencyStatus.COMPLETED
                and record.terminal_outcome_fingerprint is not None
            ):
                return self._duplicate_outcome(
                    request,
                    record.workflow_id,
                    record.terminal_outcome_fingerprint,
                    scope_fingerprint,
                )

        checkpoint = store.get_checkpoint(
            workflow_id, request.request_id, tenant_scope_fingerprint=scope_fingerprint
        )
        if checkpoint is None:
            created = WorkflowState(
                workflow_id=workflow_id,
                request_id=request.request_id,
                tenant_scope_fingerprint=scope_fingerprint,
                status=WorkflowStatus.CREATED,
            )
            store.create(created)
            state = created
        else:
            if checkpoint.status in TERMINAL_STATUSES:
                fingerprint = None
                if idempotency is not None:
                    completed = idempotency.get_idempotency(
                        request.request_id, tenant_scope_fingerprint=scope_fingerprint
                    )
                    if completed is not None:
                        fingerprint = completed.terminal_outcome_fingerprint
                return self._duplicate_outcome(
                    request, workflow_id, fingerprint, scope_fingerprint
                )
            state = checkpoint

        state = self._advance_to_running(
            store, workflow_id, request, state, scope_fingerprint=scope_fingerprint
        )
        outcome = await self._execute_governed(request, ir, workflow_id=workflow_id)
        if outcome.workflow_id is None:
            outcome = outcome.model_copy(
                update={
                    "workflow_id": workflow_id,
                    "tenant_scope_fingerprint": scope_fingerprint,
                }
            )
        if outcome.status in (OutcomeStatus.SUCCEEDED, OutcomeStatus.FAILED):
            target = (
                WorkflowStatus.SUCCEEDED
                if outcome.status == OutcomeStatus.SUCCEEDED
                else WorkflowStatus.FAILED
            )
            try:
                self._step(
                    store,
                    workflow_id,
                    request,
                    state,
                    target,
                    scope_fingerprint=scope_fingerprint,
                )
                if idempotency is not None and outcome.status == OutcomeStatus.SUCCEEDED:
                    idempotency.complete_idempotency(
                        request.request_id,
                        workflow_id=workflow_id,
                        terminal_outcome_fingerprint=terminal_outcome_fingerprint(outcome),
                        tenant_scope_fingerprint=scope_fingerprint,
                    )
            except NL2DataError:
                # The public outcome stands; the durable state remains
                # reconcilable (at-least-once) if the commit failed.
                pass
        return outcome

    @staticmethod
    def _resolve_workflow_id(request: QueryRequest) -> str:
        """The explicit workflow identity from the request, or a fresh one."""
        if request.context is not None and request.context.workflow_id is not None:
            return request.context.workflow_id
        return f"workflow-{uuid4().hex[:16]}"

    def _advance_to_running(
        self,
        store: StateStore,
        workflow_id: str,
        request: QueryRequest,
        state: WorkflowState,
        *,
        scope_fingerprint: str | None,
    ) -> WorkflowState:
        """Move a checkpoint toward RUNNING through allowed transition edges.

        A RUNNING checkpoint means a previous execution stopped before a
        terminal commit; the retry edge records the recovery attempt so the
        attempt budget still bounds re-execution.
        """
        status = state.status
        if status == WorkflowStatus.CREATED:
            queued = self._step(
                store,
                workflow_id,
                request,
                state,
                WorkflowStatus.QUEUED,
                scope_fingerprint=scope_fingerprint,
            )
            return self._step(
                store,
                workflow_id,
                request,
                queued,
                WorkflowStatus.RUNNING,
                scope_fingerprint=scope_fingerprint,
            )
        if status == WorkflowStatus.QUEUED:
            return self._step(
                store,
                workflow_id,
                request,
                state,
                WorkflowStatus.RUNNING,
                scope_fingerprint=scope_fingerprint,
            )
        if status == WorkflowStatus.RUNNING:
            queued = self._step(
                store,
                workflow_id,
                request,
                state,
                WorkflowStatus.QUEUED,
                scope_fingerprint=scope_fingerprint,
            )
            return self._step(
                store,
                workflow_id,
                request,
                queued,
                WorkflowStatus.RUNNING,
                scope_fingerprint=scope_fingerprint,
            )
        raise WorkflowStateError(
            f"workflow '{workflow_id}' is not resumable from '{status.value}'",
            details={"workflow_id": workflow_id, "status": status.value},
        )

    def _step(
        self,
        store: StateStore,
        workflow_id: str,
        request: QueryRequest,
        state: WorkflowState,
        target: WorkflowStatus,
        *,
        scope_fingerprint: str | None,
    ) -> WorkflowState:
        """Persist one validated transition with compare-and-set."""
        revision = store.get_revision(
            workflow_id, tenant_scope_fingerprint=scope_fingerprint
        )
        next_state = transition(state, target, event_id=f"ev-{uuid4().hex[:16]}")
        store.update(
            workflow_id,
            state.status,
            next_state,
            expected_version=revision,
            tenant_scope_fingerprint=scope_fingerprint,
        )
        return next_state

    def _duplicate_outcome(
        self,
        request: QueryRequest,
        workflow_id: str,
        outcome_fingerprint: str | None,
        scope_fingerprint: str | None,
    ) -> QueryOutcome:
        """A replay reference for a completed request, without re-execution."""
        details = {"workflow_id": workflow_id}
        if outcome_fingerprint is not None:
            details["outcome_fingerprint"] = outcome_fingerprint
        return _outcome(
            request,
            status=OutcomeStatus.REJECTED,
            error=ErrorRecord(
                code=ErrorCode.DUPLICATE_REQUEST,
                category=ErrorCategory.WORKFLOW,
                message="duplicate request already completed; the existing terminal "
                "outcome reference is returned",
                retryable=False,
                details=details,
            ),
            workflow_id=workflow_id,
            tenant_scope_fingerprint=scope_fingerprint,
        )

    async def close(self) -> None:
        """Release the bound adapter (idempotent)."""
        if self._adapter is not None:
            await self._adapter.close()

    @staticmethod
    def _facts_from_ir(
        ir: SemanticQueryIR,
        *,
        binding: PhysicalBinding | None = None,
        tenant_scope_fingerprint: str | None = None,
        isolation_profile: str | None = None,
    ) -> GovernanceFacts:
        resource_ids = {binding.object_id} if binding is not None else {ir.root_entity_id}
        return GovernanceFacts(
            source_id=ir.source_id,
            operation="select",
            resource_ids=frozenset(resource_ids),
            field_ids=ir.field_ids(),
            filter_fingerprints=ir.filter_fingerprints(),
            tenant_scope_fingerprint=tenant_scope_fingerprint,
            isolation_profile=isolation_profile,
        )

    @staticmethod
    def _rejected(request: QueryRequest, error: ErrorRecord) -> QueryOutcome:
        return _outcome(request, status=OutcomeStatus.REJECTED, error=error)
