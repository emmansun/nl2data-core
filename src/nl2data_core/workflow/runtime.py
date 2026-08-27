"""Deterministic governed workflow runtime over AI, Memory, and P1 components.

The runtime owns the single ordered stage graph
(``initialize -> memory -> intent -> plan -> validate -> compile -> guard
-> govern -> authorize -> execute -> protect -> persist -> complete``) and
enforces the mandatory gates from :mod:`nl2data_core.workflow.contract`
before every stage entry.  Compilation and the artifact guard are
pre-execution stages: they produce and validate the backend artifact with
shared compiler governance evidence before any governance or
authorization decision, and the full chain is re-verified immediately
before adapter execution.  Each stage is one deterministic node;
branches (clarification, rejection, timeout, cancellation, retry
exhaustion, approval required) are terminal and carry the final public
outcome - never raw provider or task material.

The runtime never imports framework-specific packages (such as LangGraph);
optional backends implement the same contract and cannot weaken gates.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast
from uuid import uuid4

from nl2data.errors import (
    ErrorCategory,
    ErrorCode,
    ErrorRecord,
    NL2DataError,
    as_error_record,
)
from nl2data.models import (
    CancellationRequest,
    CancellationResult,
    CancellationStatus,
    OutcomeStatus,
    QueryClarification,
    QueryClarificationOption,
    QueryOutcome,
    QueryRequest,
)
from nl2data_core.adapters.models import (
    AdapterLimits,
    ValidationContext,
)
from nl2data_core.adapters.protocol import QueryAdapter
from nl2data_core.adapters.sql.compile import compile_sql
from nl2data_core.ai.config import ModelConfig
from nl2data_core.ai.context import SemanticReference
from nl2data_core.ai.instructions import instruction_evidence_fingerprint
from nl2data_core.ai.models import (
    ClarificationRequired,
    RejectedIntent,
    ResolvedIntent,
    ResolvedMultiEntityIntent,
)
from nl2data_core.ai.plan_builder import build_ir_from_resolved_intent
from nl2data_core.ai.protocol import ModelProvider
from nl2data_core.ai.resolver import IntentResolver
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.compilation.contract import (
    ArtifactGuardResult,
    CompilationContext,
    CompilationEvidence,
    CompileResult,
    IRCompiler,
    ResultLineageEvidence,
    artifact_guard_evidence_fingerprint,
    compilation_evidence_fingerprint,
    result_lineage_fingerprint,
    verify_pre_execution_guard,
)
from nl2data_core.engine.ports import NOT_CONFIGURED_MESSAGE
from nl2data_core.governance.authorization import (
    AuthorizationIssuer,
    AuthorizationVerifier,
)
from nl2data_core.governance.decisions import PolicyEvaluator
from nl2data_core.governance.models import (
    EffectiveLimits,
    GovernanceDecision,
    GovernanceFacts,
    PolicyScope,
)
from nl2data_core.memory.context import CurrentTurnContext, build_current_turn_context
from nl2data_core.memory.models import MemoryRecallBudget
from nl2data_core.memory.protocol import MemoryProvider
from nl2data_core.memory.resolver import (
    MultiTurnResolution,
    MultiTurnResolutionKind,
    MultiTurnResolver,
    record_query_reference,
)
from nl2data_core.planning.ir.models import IRViewReference, SemanticQueryIR
from nl2data_core.planning.ir.validation import validate_ir
from nl2data_core.planning.join_planner import PLANNER_IDENTITY, JoinPlanner
from nl2data_core.planning.models import PhysicalBinding
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.tenancy.models import TenantScopeContext
from nl2data_core.tenancy.validation import validate_tenant_scope
from nl2data_core.views.projection import ResolvedViewProjection
from nl2data_core.workflow.contract import (
    ApprovalRequiredError,
    RuntimeCancelledError,
    RuntimeGateError,
    RuntimeOutcomeStatus,
    RuntimeRecoverableError,
    RuntimeRetryExhaustedError,
    RuntimeTimeoutError,
    StageResult,
    StaleCheckpointError,
    WorkflowCancellation,
    WorkflowDeadline,
    WorkflowExecutionContext,
    authorization_evidence_fingerprint,
    validate_stage_entry,
)
from nl2data_core.workflow.durable import (
    IdempotencyConflictError,
    IdempotencyStatus,
    IdempotencyStore,
    terminal_outcome_fingerprint,
)
from nl2data_core.workflow.lease import (
    FencedStateStore,
    WorkflowLease,
    WorkflowLeaseStore,
)
from nl2data_core.workflow.models import (
    TERMINAL_STATUSES,
    WorkflowBudget,
    WorkflowGate,
    WorkflowStage,
    WorkflowState,
    WorkflowStateError,
    WorkflowStatus,
)
from nl2data_core.workflow.protection import ResultProtectionError, protect_result
from nl2data_core.workflow.runner import (
    QueryExecutionComponents,
    QueryExecutionRunner,
    _outcome,
)
from nl2data_core.workflow.shared_errors import SharedStoreError
from nl2data_core.workflow.store import StateStore
from nl2data_core.workflow.transitions import checkpoint, transition

#: Stages after external adapter execution: resuming from them is ambiguous
#: because the runtime cannot claim the external work never started.
_AMBIGUOUS_STAGES = frozenset(
    {
        WorkflowStage.EXECUTE,
        WorkflowStage.PROTECT,
        WorkflowStage.PERSIST,
        WorkflowStage.COMPLETE,
    }
)

#: Resume validation failures that normalize to a public rejection.  A
#: recoverable checkpoint stays a failure because external work may have
#: started and requires operator reconciliation.
_RESUME_REJECTED_ERRORS = (
    RuntimeTimeoutError,
    RuntimeCancelledError,
    RuntimeRetryExhaustedError,
    StaleCheckpointError,
)

#: Compatibility keys accumulated by the runtime after a checkpoint was
#: written; they never participate in the base configuration identity.
_RUNTIME_ACCUMULATED_COMPAT_KEYS = frozenset({"ir"})

#: Safe compiler identity shape (module-declared constant).
_COMPILER_IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$")


def _compiler_identity(compiler: object) -> str:
    """A safe compiler identity: module constant, module name, fallback."""
    module = inspect.getmodule(compiler)
    identity = getattr(module, "COMPILER_IDENTITY", None) if module is not None else None
    if isinstance(identity, str) and _COMPILER_IDENTITY_PATTERN.fullmatch(identity):
        return identity
    if module is not None and module.__name__:
        return re.sub(r"[^A-Za-z0-9_\-\.]", "_", module.__name__)[:128]
    return "compiler"


def _compiler_version(compiler: object) -> str:
    """The module-declared compiler version, defaulted when absent."""
    module = inspect.getmodule(compiler)
    version = getattr(module, "COMPILER_VERSION", None) if module is not None else None
    if isinstance(version, str) and version:
        return version[:64]
    return "1.0.0"


def _has_context_parameter(compiler: object) -> bool:
    """Whether a callable compiler consumes the shared compilation context."""
    if not callable(compiler):
        return False
    try:
        return "context" in inspect.signature(compiler).parameters
    except (TypeError, ValueError):
        return False


class _ContextCompiler(Protocol):
    """A standalone context-aware compiler callable (e.g. ``compile_sql``)."""

    def __call__(
        self, ir: SemanticQueryIR, *, context: CompilationContext
    ) -> CompileResult: ...


class _BoundCompiler:
    """Binds one compiler implementation to the shared IRCompiler protocol.

    Context-based compilers (``compile_sql``/``compile_mongo`` and any
    :class:`IRCompiler`) emit their own evidence; legacy
    ``Callable[[SemanticQueryIR], str]`` compilers are wrapped and emit the
    same safe evidence with module-introspected identity/version and a
    backend-specific artifact fingerprint (a generic fingerprint for
    unknown adapter profiles, fail closed).
    """

    def __init__(
        self,
        compiler: IRCompiler | Callable[[SemanticQueryIR], str] | _ContextCompiler,
    ) -> None:
        self._compiler = compiler
        self._identity = _compiler_identity(compiler)
        self._version = _compiler_version(compiler)
        self._context_aware = isinstance(compiler, IRCompiler) or _has_context_parameter(
            compiler
        )

    def compile(
        self, ir: SemanticQueryIR, *, context: CompilationContext
    ) -> CompileResult:
        compiler = self._compiler
        if isinstance(compiler, IRCompiler):
            return compiler.compile(ir, context=context)
        if self._context_aware:
            return cast(_ContextCompiler, compiler)(ir, context=context)
        artifact = cast(Callable[[SemanticQueryIR], str], compiler)(ir)
        limits = context.effective_limits
        return CompileResult(
            artifact=artifact,
            evidence=CompilationEvidence(
                ir_version=ir.ir_version,
                ir_fingerprint=ir.fingerprint,
                source_id=ir.source_id,
                operation="select",
                field_ids=ir.field_ids(),
                view_fingerprint=context.view_fingerprint,
                bundle_fingerprint=context.bundle_fingerprint,
                policy_fingerprint=context.policy_fingerprint,
                tenant_scope_fingerprint=context.tenant_scope_fingerprint,
                purpose=context.purpose,
                adapter_type=context.adapter_capabilities.adapter_type,
                capability_ids=context.adapter_capabilities.features,
                required_capabilities=frozenset(ir.required_capabilities),
                mandatory_filter_fingerprints=context.mandatory_filter_fingerprints,
                max_rows=limits.max_rows if limits is not None else None,
                max_columns=limits.max_columns if limits is not None else None,
                max_execution_seconds=(
                    limits.max_execution_seconds if limits is not None else None
                ),
                max_result_bytes=limits.max_result_bytes if limits is not None else None,
                compiler_identity=self._identity,
                compiler_version=self._version,
                artifact_fingerprint=self._artifact_fingerprint(artifact, context),
            ),
        )

    def _artifact_fingerprint(self, artifact: str, context: CompilationContext) -> str:
        """The canonical artifact identity for the context's adapter profile."""
        adapter_type = context.adapter_capabilities.adapter_type
        if adapter_type == "sql":
            from nl2data_core.adapters.sql.models import sql_artifact_fingerprint

            binding = context.compiler_context
            dialect = binding.dialect if binding is not None else "sqlite"
            return sql_artifact_fingerprint(artifact, dialect)
        if adapter_type == "mongodb":
            # Canonical JSON keeps the identity stable across equivalent MQL
            # artifact serializations without depending on the backend package.
            return sha256_fingerprint(
                {"artifact": json.loads(artifact), "adapter_type": adapter_type}
            )
        return sha256_fingerprint({"artifact": artifact, "adapter_type": adapter_type})


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _clarification_outcome(request: QueryRequest, outcome: ClarificationRequired) -> QueryOutcome:
    """Public clarification outcome from a model clarification request."""
    clarification = outcome.clarification
    return _outcome(
        request,
        status=OutcomeStatus.CLARIFICATION,
        clarification=QueryClarification(
            clarification_id=clarification.clarification_id,
            question=clarification.question,
            options=tuple(
                QueryClarificationOption(
                    option_id=option.option_id,
                    label=option.label,
                    detail=option.detail,
                )
                for option in clarification.options
            ),
        ),
    )


def _rejected_model_outcome(request: QueryRequest, outcome: RejectedIntent) -> QueryOutcome:
    """Public rejection outcome from a normalized model error record."""
    record = outcome.error
    return _outcome(
        request,
        status=OutcomeStatus.REJECTED,
        error=ErrorRecord(
            code=ErrorCode.MODEL_INVOCATION_FAILED,
            category=ErrorCategory.MODEL,
            message=record.message,
            retryable=record.retryable,
            details={**record.safe_dump().get("details", {}), "model_code": record.code.value},
        ),
    )


def _memory_clarification_outcome(
    request: QueryRequest, resolution: MultiTurnResolution
) -> QueryOutcome:
    """Structured public clarification for missing or stale prior context."""
    if resolution.memory_unavailable:
        question = (
            "Earlier context is unavailable; please restate the request with full details."
        )
    else:
        question = (
            "Your request depends on earlier context that is missing or stale; "
            "please restate the request with full details."
        )
    return _outcome(
        request,
        status=OutcomeStatus.CLARIFICATION,
        clarification=QueryClarification(
            clarification_id=f"memory-clarification-{request.request_id}",
            question=question,
            options=(
                QueryClarificationOption(
                    option_id="restate", label="Restate the request with full details"
                ),
            ),
        ),
    )


def _not_configured(request: QueryRequest) -> QueryOutcome:
    """The stable not-configured fallback shared by every runner path."""
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


def _rejected(request: QueryRequest, error: ErrorRecord) -> QueryOutcome:
    return _outcome(request, status=OutcomeStatus.REJECTED, error=error)


def _failed(request: QueryRequest, error: NL2DataError) -> QueryOutcome:
    return _outcome(request, status=OutcomeStatus.FAILED, error=as_error_record(error))


@dataclass(frozen=True)
class _GraphOptions:
    """Immutable options of one graph run, shared by plain and durable paths."""

    workflow_id: str
    scope_fingerprint: str | None
    deadline: WorkflowDeadline
    compatibility: dict[str, str]
    start_stage: WorkflowStage = WorkflowStage.INITIALIZE
    cancellation_requested: bool = False
    durable: _DurableBinding | None = None


class _DurableBinding:
    """Checkpoint/commit facade over one durable workflow execution.

    Every persisted mutation is compare-and-set on the stored revision so a
    concurrent executor can never silently overwrite a checkpoint.  Over a
    fenced shared backend the binding renews the execution lease between
    stages and attaches the current owner and fencing token to every
    mutation, so a worker that lost ownership fails fast instead of
    committing stale work.
    """

    def __init__(
        self,
        *,
        store: StateStore,
        workflow_id: str,
        state: WorkflowState,
        scope_fingerprint: str | None,
        lease: WorkflowLease | None = None,
        lease_store: WorkflowLeaseStore | None = None,
        lease_ttl_seconds: float = 120.0,
        lease_renewal_margin_seconds: float = 20.0,
        now_fn: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._store = store
        self._workflow_id = workflow_id
        self._state = state
        self._scope_fingerprint = scope_fingerprint
        self._lease = lease
        self._lease_store = lease_store
        self._lease_ttl_seconds = lease_ttl_seconds
        self._lease_renewal_margin_seconds = lease_renewal_margin_seconds
        self._now_fn = now_fn
        self._fenced = isinstance(store, FencedStateStore) and lease is not None

    @property
    def state(self) -> WorkflowState:
        """The latest persisted workflow state."""
        return self._state

    def checkpoint(
        self,
        *,
        stage: WorkflowStage,
        compatibility_fingerprints: dict[str, str],
        gate_evidence_fingerprints: frozenset[str],
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Persist a stage checkpoint without changing the workflow status."""
        next_state = checkpoint(
            self._state,
            stage=stage,
            event_id=f"ev-{uuid4().hex[:16]}",
            compatibility_fingerprints=compatibility_fingerprints,
            gate_evidence_fingerprints=gate_evidence_fingerprints,
            metadata=metadata,
        )
        self._persist(next_state)

    def mark_cancelled(self) -> None:
        """Persist a cooperative cancellation request so resume fails fast."""
        next_state = checkpoint(
            self._state,
            stage=self._state.current_stage or WorkflowStage.INITIALIZE,
            event_id=f"ev-{uuid4().hex[:16]}",
            cancellation_requested=True,
        )
        self._persist(next_state)

    def step(self, target: WorkflowStatus) -> None:
        """Persist one validated terminal status transition."""
        next_state = transition(
            self._state, target, event_id=f"ev-{uuid4().hex[:16]}"
        )
        self._persist(next_state)

    def _persist(self, next_state: WorkflowState) -> None:
        revision = self._store.get_revision(
            self._workflow_id, tenant_scope_fingerprint=self._scope_fingerprint
        )
        if self._fenced and self._lease is not None:
            cast(FencedStateStore, self._store).update(
                self._workflow_id,
                self._state.status,
                next_state,
                expected_version=revision,
                tenant_scope_fingerprint=self._scope_fingerprint,
                owner_id=self._lease.owner_id,
                fencing_token=self._lease.fencing_token,
            )
        else:
            self._store.update(
                self._workflow_id,
                self._state.status,
                next_state,
                expected_version=revision,
                tenant_scope_fingerprint=self._scope_fingerprint,
            )
        self._state = next_state

    def renew_if_due(self) -> None:
        """Renew the lease when it is within the renewal margin.

        Called between stages so a long workflow never lets its lease
        expire while work is still in flight.
        """
        if not self._fenced or self._lease is None or self._lease_store is None:
            return
        remaining = (self._lease.expires_at - self._now_fn()).total_seconds()
        if remaining <= self._lease_renewal_margin_seconds:
            self._renew()

    def reverify_ownership(self) -> None:
        """Atomically re-verify lease ownership before external work.

        The unconditional renewal doubles as an atomic ownership check: a
        stale worker's renewal is rejected, so it can never reach adapter
        execution after losing the lease.
        """
        if not self._fenced or self._lease is None or self._lease_store is None:
            return
        self._renew()

    def _renew(self) -> None:
        assert self._lease is not None
        assert self._lease_store is not None
        self._lease = self._lease_store.renew_lease(
            self._workflow_id,
            owner_id=self._lease.owner_id,
            fencing_token=self._lease.fencing_token,
            tenant_scope_fingerprint=self._scope_fingerprint,
        )


class _NodeBase:
    """Base class for deterministic graph nodes.

    A node reads and writes the per-execution channel; it never mutates the
    immutable execution context and never raises raw provider exceptions.
    """

    stage: WorkflowStage

    def __init__(
        self, runtime: DeterministicWorkflowRuntime, channel: dict[str, Any]
    ) -> None:
        self._runtime = runtime
        self._channel = channel

    async def run(self, context: WorkflowExecutionContext) -> StageResult:
        raise NotImplementedError


class _InitializeNode(_NodeBase):
    """Tenant-scope validation and current-turn context preparation."""

    stage = WorkflowStage.INITIALIZE

    async def run(self, context: WorkflowExecutionContext) -> StageResult:
        request = context.request
        runtime = self._runtime
        hint = request.context.tenant_hint if request.context is not None else None
        if hint is None and runtime.tenant_context is None:
            return StageResult(
                stage=self.stage,
                status=RuntimeOutcomeStatus.SUCCEEDED,
                next_stage=WorkflowStage.MEMORY,
            )
        validation = validate_tenant_scope(runtime.tenant_context, client_tenant_hint=hint)
        if validation.valid:
            return StageResult(
                stage=self.stage,
                status=RuntimeOutcomeStatus.SUCCEEDED,
                next_stage=WorkflowStage.MEMORY,
            )
        return StageResult(
            stage=self.stage,
            status=RuntimeOutcomeStatus.REJECTED,
            outcome=_rejected(
                request,
                ErrorRecord(
                    code=ErrorCode.TENANT_CONTEXT_REJECTED,
                    category=ErrorCategory.GOVERNANCE,
                    message="tenant-scoped execution was denied by trusted context validation",
                    details={"reasons": "; ".join(validation.reasons)},
                ),
            ),
        )


class _MemoryNode(_NodeBase):
    """Memory recall; clarification branches when prior context is required."""

    stage = WorkflowStage.MEMORY

    async def run(self, context: WorkflowExecutionContext) -> StageResult:
        runtime = self._runtime
        resolution = MultiTurnResolver(
            provider=runtime.memory,
            view=runtime.view,
            semantic_references=runtime.references,
            turn=self._channel["turn"],
            recall_budget=runtime.memory_budget,
            now=runtime._now(),
            resolved_view=runtime.projection,
        ).resolve(context.request)
        if resolution.kind is MultiTurnResolutionKind.CLARIFICATION:
            return StageResult(
                stage=self.stage,
                status=RuntimeOutcomeStatus.CLARIFICATION,
                outcome=_memory_clarification_outcome(context.request, resolution),
            )
        context_extra = None
        if (
            resolution.kind is MultiTurnResolutionKind.PROJECTED
            and resolution.projection is not None
        ):
            context_extra = resolution.projection.safe_payload()
        self._channel["resolution"] = resolution
        self._channel["context_extra"] = context_extra
        return StageResult(
            stage=self.stage,
            status=RuntimeOutcomeStatus.SUCCEEDED,
            next_stage=WorkflowStage.INTENT,
        )


class _IntentNode(_NodeBase):
    """Intent resolution through the model provider with bounded retries."""

    stage = WorkflowStage.INTENT

    async def run(self, context: WorkflowExecutionContext) -> StageResult:
        runtime = self._runtime
        request = context.request
        provider = runtime.provider
        if provider is None:
            return StageResult(
                stage=self.stage,
                status=RuntimeOutcomeStatus.FAILED,
                outcome=_not_configured(request),
            )
        try:
            resolver = IntentResolver(
                view=runtime.view,
                semantic_references=runtime.references,
                config=runtime.config,
                min_confidence=runtime.min_confidence,
                projection=runtime.projection,
                policy_fingerprint=runtime.policy_scope.policy_fingerprint,
                tenant_scope_fingerprint=(
                    runtime.tenant_context.scope_fingerprint
                    if runtime.tenant_context is not None
                    else None
                ),
            )
            outcome = await resolver.resolve(
                request, provider, context_extra=self._channel.get("context_extra")
            )
            bundle = resolver.instruction_bundle
            if bundle is not None:
                gate_evidence: dict[WorkflowGate, str] = self._channel["gate_evidence"]
                gate_evidence[WorkflowGate.INSTRUCTION] = instruction_evidence_fingerprint(
                    bundle
                )
        except Exception as error:
            return StageResult(
                stage=self.stage,
                status=RuntimeOutcomeStatus.FAILED,
                outcome=_outcome(
                    request,
                    status=OutcomeStatus.FAILED,
                    error=as_error_record(error),
                ),
            )
        if isinstance(outcome, ResolvedIntent):
            self._channel["intent_outcome"] = outcome
            return StageResult(
                stage=self.stage,
                status=RuntimeOutcomeStatus.SUCCEEDED,
                next_stage=WorkflowStage.PLAN,
            )
        if isinstance(outcome, ResolvedMultiEntityIntent):
            self._channel["intent_outcome"] = outcome
            return StageResult(
                stage=self.stage,
                status=RuntimeOutcomeStatus.SUCCEEDED,
                next_stage=WorkflowStage.PLAN,
            )
        if isinstance(outcome, ClarificationRequired):
            return StageResult(
                stage=self.stage,
                status=RuntimeOutcomeStatus.CLARIFICATION,
                outcome=_clarification_outcome(request, outcome),
            )
        return StageResult(
            stage=self.stage,
            status=RuntimeOutcomeStatus.REJECTED,
            outcome=_rejected_model_outcome(request, outcome),
        )


class _PlanNode(_NodeBase):
    """IR building from the validated structured intent."""

    stage = WorkflowStage.PLAN

    async def run(self, context: WorkflowExecutionContext) -> StageResult:
        runtime = self._runtime
        outcome = self._channel["intent_outcome"]
        if not isinstance(outcome, (ResolvedIntent, ResolvedMultiEntityIntent)):
            raise RuntimeGateError(
                "plan stage requires a resolved intent",
                details={"stage": self.stage.value},
            )

        join_plan = None
        if isinstance(outcome, ResolvedMultiEntityIntent):
            if runtime.join_planner is None:
                return StageResult(
                    stage=self.stage,
                    status=RuntimeOutcomeStatus.REJECTED,
                    outcome=_rejected(
                        context.request,
                        ErrorRecord(
                            code=ErrorCode.MULTI_ENTITY_UNSUPPORTED,
                            category=ErrorCategory.VALIDATION,
                            message="multi-entity planning is not configured for this runtime",
                            retryable=False,
                        ),
                    ),
                )
            planner_outcome = runtime.join_planner.plan(outcome.intent)
            if planner_outcome.kind != "plan":
                return StageResult(
                    stage=self.stage,
                    status=RuntimeOutcomeStatus.REJECTED,
                    outcome=_rejected(
                        context.request,
                        ErrorRecord(
                            code=_join_error_code(planner_outcome.kind),
                            category=ErrorCategory.VALIDATION,
                            message=planner_outcome.reason,
                            retryable=False,
                        ),
                    ),
                )
            join_plan = planner_outcome.plan

        try:
            ir = build_ir_from_resolved_intent(
                outcome,
                catalog_fingerprint=runtime.view.catalog_fingerprint,
                view_reference=runtime._view_reference(),
                join_plan=join_plan,
            )
        except Exception as error:
            return StageResult(
                stage=self.stage,
                status=RuntimeOutcomeStatus.FAILED,
                outcome=_outcome(
                    context.request, status=OutcomeStatus.FAILED, error=as_error_record(error)
                ),
            )
        self._channel["ir"] = ir
        self._channel["join_plan"] = join_plan
        self._channel["compat"]["ir"] = sha256_fingerprint(
            {"ir_version": ir.ir_version, "ir_fingerprint": ir.fingerprint}
        )
        return StageResult(
            stage=self.stage,
            status=RuntimeOutcomeStatus.SUCCEEDED,
            next_stage=WorkflowStage.VALIDATE,
        )


def _join_error_code(kind: str) -> ErrorCode:
    if kind == "ambiguous":
        return ErrorCode.JOIN_PATH_AMBIGUOUS
    if kind == "unauthorized":
        return ErrorCode.JOIN_EDGE_UNAUTHORIZED
    return ErrorCode.JOIN_PATH_NOT_FOUND


class _ValidateNode(_NodeBase):
    """IR/view validation only; compilation and guarding are separate stages."""

    stage = WorkflowStage.VALIDATE

    async def run(self, context: WorkflowExecutionContext) -> StageResult:
        runtime = self._runtime
        ir = self._channel["ir"]
        ir_result = validate_ir(ir, view=runtime.view)
        if not ir_result.valid:
            return StageResult(
                stage=self.stage,
                status=RuntimeOutcomeStatus.REJECTED,
                outcome=_rejected(
                    context.request,
                    ErrorRecord(
                        code=ErrorCode.PLAN_VALIDATION_FAILED,
                        category=ErrorCategory.VALIDATION,
                        message="semantic IR failed validation",
                        details={"issue_codes": ",".join(ir_result.issue_codes())},
                    ),
                ),
            )
        gate_evidence: dict[WorkflowGate, str] = self._channel["gate_evidence"]
        plan_evidence: dict[str, object] = {
            "ir": ir.fingerprint,
            "structure": "valid",
            "view": "valid",
        }
        if runtime.view.view_bound:
            assert runtime.view.view_fingerprint is not None
            plan_evidence["view_fingerprint"] = runtime.view.view_fingerprint
        gate_evidence[WorkflowGate.PLAN_VALIDATION] = sha256_fingerprint(plan_evidence)
        return StageResult(
            stage=self.stage,
            status=RuntimeOutcomeStatus.SUCCEEDED,
            next_stage=WorkflowStage.COMPILE,
        )


class _CompileNode(_NodeBase):
    """Compilation with the shared immutable compilation context.

    Produces the compilation gate evidence.  The compiler cannot grant
    authority: it never evaluates governance or issues authorizations, and
    its artifact is guarded by the next stage before any governance or
    authorization decision is made.
    """

    stage = WorkflowStage.COMPILE

    async def run(self, context: WorkflowExecutionContext) -> StageResult:
        runtime = self._runtime
        ir = self._channel["ir"]
        view = runtime.view
        projection = runtime.projection
        compilation_context = CompilationContext(
            ir=ir,
            view=view,
            view_reference=runtime._view_reference(),
            view_fingerprint=(view.view_fingerprint if view.view_bound else None),
            bundle_id=(projection.bundle_id if projection is not None else None),
            bundle_version=(projection.bundle_version if projection is not None else None),
            bundle_fingerprint=(
                projection.bundle_fingerprint if projection is not None else None
            ),
            tenant_scope_fingerprint=context.tenant_scope_fingerprint,
            purpose=None,
            policy_fingerprint=runtime.policy_scope.policy_fingerprint,
            adapter_capabilities=runtime.adapter.capabilities(),
            effective_limits=runtime.effective_limits,
            mandatory_filter_fingerprints=ir.filter_fingerprints(),
            compiler_context=runtime.binding,
            join_plan=self._channel.get("join_plan"),
            planner_identity=(
                PLANNER_IDENTITY
                if self._channel.get("join_plan") is not None
                else None
            ),
        )
        try:
            result = runtime.compiler.compile(ir, context=compilation_context)
        except Exception as error:
            return StageResult(
                stage=self.stage,
                status=RuntimeOutcomeStatus.REJECTED,
                outcome=_rejected(context.request, as_error_record(error)),
            )
        self._channel["compilation_context"] = compilation_context
        self._channel["compiler_result"] = result
        gate_evidence: dict[WorkflowGate, str] = self._channel["gate_evidence"]
        gate_evidence[WorkflowGate.COMPILATION] = compilation_evidence_fingerprint(
            result.evidence
        )
        return StageResult(
            stage=self.stage,
            status=RuntimeOutcomeStatus.SUCCEEDED,
            next_stage=WorkflowStage.GUARD,
        )


class _GuardNode(_NodeBase):
    """Artifact parse/validate; the guard result gates governance onward.

    The guard consumes the IR filter obligations (semantic fingerprint
    space) and physical-to-semantic field bindings; a rejected or
    unguardable artifact stops the workflow before governance and adapter
    execution.
    """

    stage = WorkflowStage.GUARD

    async def run(self, context: WorkflowExecutionContext) -> StageResult:
        runtime = self._runtime
        ir = self._channel["ir"]
        result = self._channel["compiler_result"]
        binding = runtime.binding
        validation_context = ValidationContext(
            snapshot_fingerprint=ir.provenance.catalog_fingerprint,
            required_obligation_fingerprints=ir.filter_fingerprints(),
            field_bindings=(
                {
                    column.physical_name: column.field_id
                    for column in binding.column_bindings
                }
                if binding is not None
                else None
            ),
        )
        try:
            parsed = runtime.adapter.parse(result.artifact, validation_context)
            validated = runtime.adapter.validate(parsed, validation_context)
        except Exception as error:
            return StageResult(
                stage=self.stage,
                status=RuntimeOutcomeStatus.REJECTED,
                outcome=_rejected(context.request, as_error_record(error)),
            )
        guard = ArtifactGuardResult(
            accepted=True,
            fingerprint=validated.fingerprint,
            guard_identity=f"{runtime.adapter.capabilities().adapter_type}-artifact-guard",
            artifact_fingerprint=parsed.fingerprint,
            obligations_verified=validated.obligations_verified,
            bounded_rows=validated.bounded_rows,
        )
        self._channel["parsed"] = parsed
        self._channel["validated"] = validated
        self._channel["validation_context"] = validation_context
        self._channel["artifact_guard"] = guard
        gate_evidence: dict[WorkflowGate, str] = self._channel["gate_evidence"]
        gate_evidence[WorkflowGate.ARTIFACT_GUARD] = artifact_guard_evidence_fingerprint(
            guard
        )
        gate_evidence[WorkflowGate.ARTIFACT_VALIDATION] = validated.fingerprint
        return StageResult(
            stage=self.stage,
            status=RuntimeOutcomeStatus.SUCCEEDED,
            next_stage=WorkflowStage.GOVERN,
        )


class _GovernNode(_NodeBase):
    """Governance evaluation; denial is a terminal rejection branch."""

    stage = WorkflowStage.GOVERN

    async def run(self, context: WorkflowExecutionContext) -> StageResult:
        runtime = self._runtime
        ir = self._channel["ir"]
        scope_fingerprint = context.tenant_scope_fingerprint
        isolation_profile = runtime.isolation_profile
        policy_scope = runtime.policy_scope
        if runtime.tenant_context is not None and (
            policy_scope.tenant_scope_fingerprint != scope_fingerprint
            or policy_scope.isolation_profile != isolation_profile
        ):
            return StageResult(
                stage=self.stage,
                status=RuntimeOutcomeStatus.REJECTED,
                outcome=_rejected(
                    context.request,
                    ErrorRecord(
                        code=ErrorCode.GOVERNANCE_DENIED,
                        category=ErrorCategory.GOVERNANCE,
                        message="tenant-scoped execution requires a matching tenant policy",
                        retryable=False,
                    ),
                ),
            )
        capabilities = runtime.adapter.capabilities()
        decision = runtime.evaluator.evaluate(
            runtime._facts_from_ir(
                ir,
                binding=runtime.binding,
                tenant_scope_fingerprint=scope_fingerprint,
                isolation_profile=isolation_profile,
                view_fingerprint=(
                    runtime.view.view_fingerprint if runtime.view.view_bound else None
                ),
                bundle_fingerprint=(
                    runtime.projection.bundle_fingerprint
                    if runtime.projection is not None
                    else None
                ),
                capability_ids=capabilities.features,
                artifact_fingerprint=self._channel["validated"].fingerprint,
            ),
            policy_scope,
        )
        if decision.decision != GovernanceDecision.ALLOW:
            return StageResult(
                stage=self.stage,
                status=RuntimeOutcomeStatus.REJECTED,
                outcome=_rejected(
                    context.request,
                    ErrorRecord(
                        code=ErrorCode.GOVERNANCE_DENIED,
                        category=ErrorCategory.GOVERNANCE,
                        message="query is denied by policy",
                        details={"reasons": "; ".join(decision.reasons)},
                    ),
                ),
            )
        gate_evidence: dict[WorkflowGate, str] = self._channel["gate_evidence"]
        gate_evidence[WorkflowGate.GOVERNANCE] = sha256_fingerprint(
            {"policy": policy_scope.policy_fingerprint, "decision": "allow"}
        )
        return StageResult(
            stage=self.stage,
            status=RuntimeOutcomeStatus.SUCCEEDED,
            next_stage=WorkflowStage.AUTHORIZE,
        )


class _AuthorizeNode(_NodeBase):
    """Artifact-bound authorization issuance and verification.

    An approval-required hook is a bounded extension point: when set and
    satisfied, the workflow branches to a terminal approval-required
    rejection before any adapter invocation.
    """

    stage = WorkflowStage.AUTHORIZE

    async def run(self, context: WorkflowExecutionContext) -> StageResult:
        runtime = self._runtime
        ir = self._channel["ir"]
        validated = self._channel["validated"]
        scope_fingerprint = context.tenant_scope_fingerprint
        isolation_profile = runtime.isolation_profile
        policy_scope = runtime.policy_scope
        if runtime.approval_required is not None and runtime.approval_required(ir):
            raise ApprovalRequiredError(
                "IR requires human approval before adapter execution",
                details={"stage": self.stage.value},
            )
        capabilities = runtime.adapter.capabilities()
        view_fingerprint = (
            runtime.view.view_fingerprint if runtime.view.view_bound else None
        )
        bundle_fingerprint = (
            runtime.projection.bundle_fingerprint
            if runtime.projection is not None
            else None
        )
        authorization = runtime.issuer.issue(
            policy_scope=policy_scope,
            adapter_type=capabilities.adapter_type,
            source_id=ir.source_id,
            operation="select",
            artifact_fingerprint=validated.fingerprint,
            ir_fingerprint=ir.fingerprint,
            view_fingerprint=view_fingerprint,
            bundle_fingerprint=bundle_fingerprint,
            capability_ids=capabilities.features,
            tenant_scope_fingerprint=scope_fingerprint,
            isolation_profile=isolation_profile,
            effective_limits=runtime.effective_limits,
            mandatory_filter_fingerprints=ir.filter_fingerprints(),
            ttl_seconds=runtime.ttl_seconds,
        )
        verification = runtime.verifier.verify(
            authorization,
            artifact_fingerprint=validated.fingerprint,
            adapter_type=capabilities.adapter_type,
            source_id=ir.source_id,
            operation="select",
            ir_fingerprint=ir.fingerprint,
            view_fingerprint=view_fingerprint,
            bundle_fingerprint=bundle_fingerprint,
            capability_ids=capabilities.features,
            filter_fingerprints=ir.filter_fingerprints(),
            tenant_scope_fingerprint=scope_fingerprint,
            isolation_profile=isolation_profile,
        )
        if not verification.verified:
            return StageResult(
                stage=self.stage,
                status=RuntimeOutcomeStatus.REJECTED,
                outcome=_rejected(
                    context.request,
                    ErrorRecord(
                        code=ErrorCode.AUTHORIZATION_REJECTED,
                        category=ErrorCategory.GOVERNANCE,
                        message="execution authorization could not be verified",
                        details={"reasons": "; ".join(verification.reasons)},
                    ),
                ),
            )
        self._channel["authorization"] = authorization
        gate_evidence: dict[WorkflowGate, str] = self._channel["gate_evidence"]
        gate_evidence[WorkflowGate.AUTHORIZATION] = authorization_evidence_fingerprint(
            authorization
        )
        return StageResult(
            stage=self.stage,
            status=RuntimeOutcomeStatus.SUCCEEDED,
            next_stage=WorkflowStage.EXECUTE,
        )


class _ExecuteNode(_NodeBase):
    """Adapter execution bounded by the workflow deadline.

    The deadline is cooperative: the adapter call is wrapped with the
    remaining budget so a slow adapter cannot outlive the workflow.  Only
    retryable :class:`NL2DataError` failures propagate to the bounded node
    retry loop; everything else becomes a terminal failure outcome.
    """

    stage = WorkflowStage.EXECUTE

    async def run(self, context: WorkflowExecutionContext) -> StageResult:
        runtime = self._runtime
        request = context.request
        validated = self._channel["validated"]
        validation_context = self._channel["validation_context"]
        authorization = self._channel["authorization"]
        guard_reasons = verify_pre_execution_guard(
            context=self._channel["compilation_context"],
            evidence=self._channel["compiler_result"].evidence,
            guard=self._channel["artifact_guard"],
            authorization=authorization,
            now=runtime._now(),
        )
        if guard_reasons:
            return StageResult(
                stage=self.stage,
                status=RuntimeOutcomeStatus.REJECTED,
                outcome=_rejected(
                    request,
                    ErrorRecord(
                        code=ErrorCode.AUTHORIZATION_REJECTED,
                        category=ErrorCategory.GOVERNANCE,
                        message="execution was denied by the pre-execution guard",
                        details={"reasons": "; ".join(guard_reasons)},
                        retryable=False,
                    ),
                ),
            )
        limits = authorization.effective_limits
        bounded = validation_context.model_copy(
            update={
                "limits": (
                    validation_context.limits.model_copy(
                        update={"max_result_rows": limits.max_rows}
                    )
                    if validation_context.limits is not None
                    else AdapterLimits(max_result_rows=limits.max_rows)
                ),
                "execution_timeout_seconds": limits.max_execution_seconds,
                "max_result_bytes": limits.max_result_bytes,
            }
        )
        remaining = context.deadline.remaining_seconds(now=runtime._now())
        timeout = min(remaining, limits.max_execution_seconds)
        if timeout <= 0.0:
            raise RuntimeTimeoutError(
                "workflow deadline expired before adapter execution",
                details={"stage": self.stage.value},
            )
        try:
            execution = await asyncio.wait_for(
                runtime.adapter.execute(validated, bounded), timeout=timeout
            )
        except TimeoutError as error:
            raise RuntimeTimeoutError(
                "adapter execution exceeded the workflow deadline",
                details={"stage": self.stage.value, "timeout_seconds": str(timeout)},
            ) from error
        except NL2DataError as error:
            if error.retryable:
                raise
            return StageResult(
                stage=self.stage,
                status=RuntimeOutcomeStatus.FAILED,
                outcome=_failed(request, error),
            )
        except Exception as error:
            return StageResult(
                stage=self.stage,
                status=RuntimeOutcomeStatus.FAILED,
                outcome=_outcome(
                    request, status=OutcomeStatus.FAILED, error=as_error_record(error)
                ),
            )
        self._channel["execution"] = execution
        return StageResult(
            stage=self.stage,
            status=RuntimeOutcomeStatus.SUCCEEDED,
            next_stage=WorkflowStage.PROTECT,
        )


class _ProtectNode(_NodeBase):
    """Public result protection and successful outcome construction."""

    stage = WorkflowStage.PROTECT

    async def run(self, context: WorkflowExecutionContext) -> StageResult:
        runtime = self._runtime
        request = context.request
        ir = self._channel["ir"]
        execution = self._channel["execution"]
        authorization = self._channel["authorization"]
        try:
            result = protect_result(
                execution,
                ir=ir,
                binding=runtime.binding,
                limits=authorization.effective_limits,
            )
        except ResultProtectionError as error:
            return StageResult(
                stage=self.stage,
                status=RuntimeOutcomeStatus.FAILED,
                outcome=_outcome(
                    request, status=OutcomeStatus.FAILED, error=error.to_record()
                ),
            )
        self._channel["result"] = result
        result_fingerprint = result.fingerprint
        if result_fingerprint is None:
            raise ResultProtectionError(
                "protected result carries no fingerprint",
                details={"result_id": result.result_id},
            )
        lineage = ResultLineageEvidence(
            result_fingerprint=result_fingerprint,
            artifact_fingerprint=self._channel["artifact_guard"].artifact_fingerprint,
            guard_fingerprint=self._channel["artifact_guard"].fingerprint,
            ir_fingerprint=ir.fingerprint,
            view_fingerprint=(
                runtime.view.view_fingerprint if runtime.view.view_bound else None
            ),
            bundle_fingerprint=(
                runtime.projection.bundle_fingerprint
                if runtime.projection is not None
                else None
            ),
            policy_fingerprint=runtime.policy_scope.policy_fingerprint,
            authorization_id=authorization.authorization_id,
            adapter_type=runtime.adapter.capabilities().adapter_type,
            compiler_identity=self._channel["compiler_result"].evidence.compiler_identity,
            compiler_version=self._channel["compiler_result"].evidence.compiler_version,
        )
        lineage_fingerprint = result_lineage_fingerprint(lineage)
        self._channel["result_lineage"] = lineage
        self._channel["result_lineage_fingerprint"] = lineage_fingerprint
        self._channel["outcome"] = _outcome(
            request,
            status=OutcomeStatus.SUCCEEDED,
            result=result,
            workflow_id=context.workflow_id,
            tenant_scope_fingerprint=context.tenant_scope_fingerprint,
        )
        return StageResult(
            stage=self.stage,
            status=RuntimeOutcomeStatus.SUCCEEDED,
            next_stage=WorkflowStage.PERSIST,
            details={"lineage_fingerprint": lineage_fingerprint},
        )


class _PersistNode(_NodeBase):
    """Memory write-back; recording failures never fail the query."""

    stage = WorkflowStage.PERSIST

    async def run(self, context: WorkflowExecutionContext) -> StageResult:
        runtime = self._runtime
        memory = runtime.memory
        if memory is not None:
            with contextlib.suppress(Exception):
                intent_outcome = self._channel["intent_outcome"]
                ir = self._channel["ir"]
                result = self._channel.get("result")
                record_query_reference(
                    provider=memory,
                    turn=self._channel["turn"],
                    intent_fingerprint=intent_outcome.intent.fingerprint,
                    ir_fingerprint=ir.fingerprint,
                    artifact_fingerprint=(
                        result.fingerprint if result is not None else None
                    ),
                    source_id=intent_outcome.intent.source_id,
                    root_entity_id=intent_outcome.intent.root_entity_id,
                    field_ids=intent_outcome.intent.field_ids(),
                    ttl_seconds=runtime.memory_ttl_seconds,
                )
        return StageResult(
            stage=self.stage,
            status=RuntimeOutcomeStatus.SUCCEEDED,
            next_stage=WorkflowStage.COMPLETE,
        )


class _CompleteNode(_NodeBase):
    """Terminal success: returns the protected public outcome."""

    stage = WorkflowStage.COMPLETE

    async def run(self, context: WorkflowExecutionContext) -> StageResult:
        return StageResult(
            stage=self.stage,
            status=RuntimeOutcomeStatus.SUCCEEDED,
            outcome=self._channel["outcome"],
        )


#: Stage identity to node class for the deterministic graph.
_NODES: dict[WorkflowStage, type[_NodeBase]] = {
    WorkflowStage.INITIALIZE: _InitializeNode,
    WorkflowStage.MEMORY: _MemoryNode,
    WorkflowStage.INTENT: _IntentNode,
    WorkflowStage.PLAN: _PlanNode,
    WorkflowStage.VALIDATE: _ValidateNode,
    WorkflowStage.COMPILE: _CompileNode,
    WorkflowStage.GUARD: _GuardNode,
    WorkflowStage.GOVERN: _GovernNode,
    WorkflowStage.AUTHORIZE: _AuthorizeNode,
    WorkflowStage.EXECUTE: _ExecuteNode,
    WorkflowStage.PROTECT: _ProtectNode,
    WorkflowStage.PERSIST: _PersistNode,
    WorkflowStage.COMPLETE: _CompleteNode,
}


class DeterministicWorkflowRuntime:
    """The deterministic governed workflow graph.

    The runtime composes the existing AI, Memory, Tenant, Governance,
    Adapter, Result Protection, StateStore, and idempotency boundaries in
    one explicit ordered graph.  When the full AI path is not configured it
    reports not-configured exactly like every other runner; the P1
    structured-IR fallback remains owned by :class:`QueryExecutionRunner`.

    ``now`` injects the clock for deterministic deadline/cancellation tests;
    ``approval_required`` is a bounded extension point that rejects an IR
    before adapter execution when human approval is required.
    """

    def __init__(
        self,
        *,
        provider: ModelProvider | None = None,
        execution: QueryExecutionRunner | None = None,
        semantic_references: Mapping[str, SemanticReference] | None = None,
        binding: PhysicalBinding | None = None,
        config: ModelConfig | None = None,
        min_confidence: float = 0.6,
        memory: MemoryProvider | None = None,
        memory_budget: MemoryRecallBudget | None = None,
        memory_ttl_seconds: int = 86_400,
        budget: WorkflowBudget | None = None,
        state_store: StateStore | None = None,
        idempotency_ttl_seconds: float = 86_400.0,
        worker_id: str | None = None,
        lease_ttl_seconds: float = 120.0,
        lease_renewal_margin_seconds: float = 20.0,
        approval_required: Callable[[SemanticQueryIR], bool] | None = None,
        ir_compiler: IRCompiler | Callable[[SemanticQueryIR], str] | None = None,
        now: Callable[[], datetime] | None = None,
        projection: ResolvedViewProjection | None = None,
        relationship_graph: object | None = None,
        join_planner: JoinPlanner | None = None,
    ) -> None:
        self._provider = provider
        self._execution = execution
        self._projection = projection
        self._binding = binding
        self._config = config or ModelConfig()
        self._min_confidence = min_confidence
        self._memory = memory
        self._memory_budget = memory_budget
        self._memory_ttl_seconds = memory_ttl_seconds
        self._budget = budget or WorkflowBudget()
        self._state_store = state_store
        self._idempotency_ttl_seconds = idempotency_ttl_seconds
        if worker_id is None:
            worker_id = f"worker-{uuid4().hex[:16]}"
        if not (1.0 <= lease_ttl_seconds <= 86_400.0):
            raise ValueError("lease_ttl_seconds must be between 1 and 86400 seconds")
        if not (0.0 < lease_renewal_margin_seconds < lease_ttl_seconds):
            raise ValueError(
                "lease_renewal_margin_seconds must be positive and below the lease TTL"
            )
        self._worker_id = worker_id
        self._lease_ttl_seconds = lease_ttl_seconds
        self._lease_renewal_margin_seconds = lease_renewal_margin_seconds
        self._approval_required = approval_required
        self._ir_compiler = _BoundCompiler(ir_compiler or compile_sql)
        self._now_fn = now or _utc_now
        self._relationship_graph = relationship_graph
        self._join_planner = join_planner
        self._closed = False
        if projection is not None:
            self._view: AuthorizedView | None = AuthorizedView.from_projection(projection)
            self._references = {
                field.field_id: SemanticReference(
                    field_id=field.field_id,
                    label=field.alias or field.label,
                    description=field.description,
                    data_type=field.data_type,
                    allowed_aggregations=field.allowed_aggregations,
                )
                for entity in projection.entities
                for field in entity.fields
            }
        else:
            self._view = execution.view if execution is not None else None
            self._references = dict(semantic_references or {})

    # -- bound components ---------------------------------------------------

    @property
    def provider(self) -> ModelProvider | None:
        """The bound model provider, or ``None`` for the P1 fallback path."""
        return self._provider

    @property
    def memory(self) -> MemoryProvider | None:
        """The bound memory provider, or ``None`` for stateless recall."""
        return self._memory

    @property
    def references(self) -> dict[str, SemanticReference]:
        """The bound semantic references used for context assembly."""
        return self._references

    @property
    def binding(self) -> PhysicalBinding | None:
        """The bound physical binding used for IR compilation, if any."""
        return self._binding

    @property
    def relationship_graph(self) -> object | None:
        """The governed relationship graph used for multi-entity planning."""
        return self._relationship_graph

    @property
    def join_planner(self) -> JoinPlanner | None:
        """The deterministic join planner bound to the runtime, if any."""
        return self._join_planner

    @property
    def config(self) -> ModelConfig:
        """The bound model invocation configuration."""
        return self._config

    @property
    def min_confidence(self) -> float:
        """The minimum intent confidence accepted by the resolver."""
        return self._min_confidence

    @property
    def memory_budget(self) -> MemoryRecallBudget | None:
        """The bound memory recall budget, if any."""
        return self._memory_budget

    @property
    def memory_ttl_seconds(self) -> int:
        """The write-back TTL applied to recorded query references."""
        return self._memory_ttl_seconds

    @property
    def approval_required(self) -> Callable[[SemanticQueryIR], bool] | None:
        """The bounded approval-required hook, if any."""
        return self._approval_required

    @property
    def compiler(self) -> IRCompiler:
        """The bound IR compiler used for artifact compilation.

        Defaults to the context-based SQL compiler; specialization adapters
        bind their own compiler (for example the structured MQL compiler)
        so the runtime graph stays framework-neutral.  Legacy
        ``Callable[[SemanticQueryIR], str]`` compilers are wrapped and emit
        the same safe compilation evidence.
        """
        return self._ir_compiler

    @property
    def tenant_context(self) -> TenantScopeContext | None:
        """The trusted tenant scope bound to the governed path."""
        if self._execution is None:
            return None
        return self._execution.tenant_context

    @property
    def isolation_profile(self) -> str | None:
        """The isolation profile of the trusted tenant scope, if any."""
        tenant = self.tenant_context
        if tenant is None:
            return None
        return tenant.tenant.isolation_profile.value

    @property
    def view(self) -> AuthorizedView:
        """The authorized view of the governed path (configured only).

        When a resolved-view projection is bound, the view is derived from
        the projection; otherwise it is the execution's configured view.
        """
        assert self._view is not None
        return self._view

    @property
    def projection(self) -> ResolvedViewProjection | None:
        """The resolved-view projection bound to the governed path, if any."""
        return self._projection

    @property
    def policy_scope(self) -> PolicyScope:
        """The policy scope of the governed path (configured only)."""
        execution = self._execution
        assert execution is not None and execution.policy_scope is not None
        return execution.policy_scope

    @property
    def adapter(self) -> QueryAdapter:
        """The adapter bound to the governed path (configured only)."""
        components = self.components
        assert components is not None
        return components.adapter

    @property
    def evaluator(self) -> PolicyEvaluator:
        """The policy evaluator bound to the governed path."""
        components = self.components
        assert components is not None
        return components.evaluator

    @property
    def issuer(self) -> AuthorizationIssuer:
        """The authorization issuer bound to the governed path."""
        components = self.components
        assert components is not None
        return components.issuer

    @property
    def verifier(self) -> AuthorizationVerifier:
        """The authorization verifier bound to the governed path."""
        components = self.components
        assert components is not None
        return components.verifier

    @property
    def effective_limits(self) -> EffectiveLimits:
        """The effective execution limits bound to the governed path."""
        components = self.components
        assert components is not None
        return components.effective_limits

    @property
    def ttl_seconds(self) -> float:
        """The authorization TTL bound to the governed path."""
        components = self.components
        assert components is not None
        return components.ttl_seconds

    @property
    def components(self) -> QueryExecutionComponents | None:
        """The frozen governed components, or ``None`` when not configured."""
        if self._execution is None:
            return None
        return self._execution.components()

    # -- lifecycle ----------------------------------------------------------

    def is_configured(self) -> bool:
        """Whether the full AI path is available; otherwise fallbacks apply."""
        return (
            self._provider is not None
            and self._execution is not None
            and self._execution.is_configured()
        )

    async def execute(
        self,
        request: QueryRequest,
        *,
        cancellation: WorkflowCancellation | None = None,
    ) -> QueryOutcome:
        """Execute one request through the deterministic graph."""
        if not self.is_configured():
            return _not_configured(request)
        if self._state_store is None:
            return await self._execute_plain(request, cancellation=cancellation)
        try:
            return await self._execute_durable(request, cancellation=cancellation)
        except SharedStoreError as error:
            return _outcome(
                request,
                status=(
                    OutcomeStatus.REJECTED
                    if error.is_public_rejected()
                    else OutcomeStatus.FAILED
                ),
                error=error.to_public_record(),
            )
        except NL2DataError as error:
            return _outcome(
                request, status=OutcomeStatus.FAILED, error=as_error_record(error)
            )

    async def close(self) -> None:
        """Release the provider and the governed execution (idempotent)."""
        if self._closed:
            return
        self._closed = True
        try:
            if self._provider is not None:
                await self._provider.close()
        finally:
            try:
                if self._execution is not None:
                    await self._execution.close()
            finally:
                try:
                    await self._close_optional(self._memory)
                finally:
                    await self._close_optional(self._state_store)

    @staticmethod
    async def _close_optional(resource: object | None) -> None:
        """Close an optional synchronous or asynchronous resource once."""
        if resource is None:
            return
        close = getattr(resource, "close", None)
        if not callable(close):
            return
        result = close()
        if inspect.isawaitable(result):
            await result

    @property
    def state_store(self) -> StateStore | None:
        """The bound durable state store, if any."""
        return self._state_store

    def get_workflow(
        self, workflow_id: str, *, tenant_scope_fingerprint: str | None = None
    ) -> WorkflowState | None:
        """Return the stored workflow state or ``None`` when not durable.

        State is only observable when a durable state store is configured;
        without one the runtime reports ``None`` instead of fabricating
        state.
        """
        if self._state_store is None:
            return None
        try:
            return self._state_store.get(
                workflow_id, tenant_scope_fingerprint=tenant_scope_fingerprint
            )
        except SharedStoreError as error:
            raise WorkflowStateError(
                "shared state backend is unavailable",
                retryable=True,
                details={"cause_type": type(error).__name__},
            ) from error

    def cancel(self, request: CancellationRequest) -> CancellationResult:
        """Request cooperative cancellation for a stored non-terminal workflow.

        The cancellation flag is persisted through the compare-and-set
        checkpoint path, so a later resume fails fast with the public
        cancelled outcome before any adapter work.  Without a durable
        store, or for an unknown or already-terminal workflow, no
        cancellation is recorded and the stable result reports why.
        """
        store = self._state_store
        if store is None:
            return CancellationResult(
                status=CancellationStatus.NOT_FOUND,
                workflow_id=request.workflow_id,
                reason=request.reason,
                occurred_at=self._now(),
            )
        try:
            state = store.get(
                request.workflow_id,
                tenant_scope_fingerprint=request.tenant_scope_fingerprint,
            )
        except SharedStoreError as error:
            raise WorkflowStateError(
                "shared state backend is unavailable",
                retryable=True,
                details={"cause_type": type(error).__name__},
            ) from error
        if state is None:
            return CancellationResult(
                status=CancellationStatus.NOT_FOUND,
                workflow_id=request.workflow_id,
                reason=request.reason,
                occurred_at=self._now(),
            )
        if state.status in TERMINAL_STATUSES:
            return CancellationResult(
                status=CancellationStatus.ALREADY_TERMINAL,
                workflow_id=request.workflow_id,
                reason=request.reason,
                occurred_at=self._now(),
            )
        _DurableBinding(
            store=store,
            workflow_id=request.workflow_id,
            state=state,
            scope_fingerprint=request.tenant_scope_fingerprint,
        ).mark_cancelled()
        return CancellationResult(
            status=CancellationStatus.CANCELLED,
            workflow_id=request.workflow_id,
            reason=request.reason,
            occurred_at=self._now(),
        )

    # -- graph execution ----------------------------------------------------

    async def _execute_plain(
        self, request: QueryRequest, *, cancellation: WorkflowCancellation | None
    ) -> QueryOutcome:
        turn, compat = self._prepare_turn(request)
        deadline = WorkflowDeadline.from_budget(self._budget, now=self._now())
        options = _GraphOptions(
            workflow_id=self._resolve_workflow_id(request),
            scope_fingerprint=self._scope_fingerprint(),
            deadline=deadline,
            compatibility=compat,
            cancellation_requested=bool(
                cancellation is not None and cancellation.requested
            ),
        )
        return await self._execute_graph(request, options, turn=turn, cancellation=cancellation)

    async def _execute_durable(
        self, request: QueryRequest, *, cancellation: WorkflowCancellation | None
    ) -> QueryOutcome:
        store = self._state_store
        assert store is not None
        scope_fingerprint = self._scope_fingerprint()
        turn, compat = self._prepare_turn(request)
        idempotency: IdempotencyStore | None = (
            store if isinstance(store, IdempotencyStore) else None
        )
        lease_store: WorkflowLeaseStore | None = (
            store if isinstance(store, WorkflowLeaseStore) else None
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
                return _rejected(request, error.to_record())
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

        checkpoint_state = store.get_checkpoint(
            workflow_id, request.request_id, tenant_scope_fingerprint=scope_fingerprint
        )
        if checkpoint_state is None:
            deadline = WorkflowDeadline.from_budget(self._budget, now=self._now())
            created = WorkflowState(
                workflow_id=workflow_id,
                request_id=request.request_id,
                tenant_scope_fingerprint=scope_fingerprint,
                status=WorkflowStatus.CREATED,
                budget=self._budget,
                compatibility_fingerprints=compat,
                deadline_at=deadline.deadline_at,
            )
            store.create(created)
            state = created
        else:
            if checkpoint_state.status in TERMINAL_STATUSES:
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
            try:
                state = self._validate_resume(checkpoint_state, compat)
            except _RESUME_REJECTED_ERRORS as error:
                return _outcome(
                    request,
                    status=OutcomeStatus.REJECTED,
                    error=as_error_record(error),
                    workflow_id=workflow_id,
                    tenant_scope_fingerprint=scope_fingerprint,
                )
            except RuntimeRecoverableError as error:
                return _outcome(
                    request,
                    status=OutcomeStatus.FAILED,
                    error=as_error_record(error),
                    workflow_id=workflow_id,
                    tenant_scope_fingerprint=scope_fingerprint,
                )
            deadline = (
                WorkflowDeadline(deadline_at=state.deadline_at)
                if state.deadline_at is not None
                else WorkflowDeadline.from_budget(self._budget, now=self._now())
            )

        lease: WorkflowLease | None = None
        if lease_store is not None:
            lease = lease_store.acquire_lease(
                workflow_id,
                owner_id=self._worker_id,
                tenant_scope_fingerprint=scope_fingerprint,
                ttl_seconds=self._lease_ttl_seconds,
            )
        try:
            state = self._advance_to_running(
                store,
                workflow_id,
                request,
                state,
                scope_fingerprint=scope_fingerprint,
                owner_id=lease.owner_id if lease is not None else None,
                fencing_token=lease.fencing_token if lease is not None else None,
            )
            binding = _DurableBinding(
                store=store,
                workflow_id=workflow_id,
                state=state,
                scope_fingerprint=scope_fingerprint,
                lease=lease,
                lease_store=lease_store,
                lease_ttl_seconds=self._lease_ttl_seconds,
                lease_renewal_margin_seconds=self._lease_renewal_margin_seconds,
                now_fn=self._now_fn,
            )
            options = _GraphOptions(
                workflow_id=workflow_id,
                scope_fingerprint=scope_fingerprint,
                deadline=deadline,
                compatibility=compat,
                start_stage=state.current_stage or WorkflowStage.INITIALIZE,
                cancellation_requested=state.cancellation_requested,
                durable=binding,
            )
            outcome = await self._execute_graph(
                request, options, turn=turn, cancellation=cancellation
            )
            if outcome.workflow_id is None:
                outcome = outcome.model_copy(
                    update={
                        "workflow_id": workflow_id,
                        "tenant_scope_fingerprint": scope_fingerprint,
                    }
                )
            if (
                outcome.status is OutcomeStatus.REJECTED
                and outcome.error is not None
                and outcome.error.code is ErrorCode.WORKFLOW_CANCELLED
            ):
                # The public outcome stands; the stored cancellation flag keeps
                # later resumes failing fast when the commit failed.
                with contextlib.suppress(NL2DataError, SharedStoreError):
                    binding.mark_cancelled()
            if outcome.status in (OutcomeStatus.SUCCEEDED, OutcomeStatus.FAILED):
                target = (
                    WorkflowStatus.SUCCEEDED
                    if outcome.status == OutcomeStatus.SUCCEEDED
                    else WorkflowStatus.FAILED
                )
                try:
                    binding.step(target)
                    if (
                        idempotency is not None
                        and outcome.status == OutcomeStatus.SUCCEEDED
                    ):
                        fenced = isinstance(store, FencedStateStore)
                        completion: dict[str, Any] = {}
                        if fenced and lease is not None:
                            completion = {
                                "owner_id": lease.owner_id,
                                "fencing_token": lease.fencing_token,
                            }
                        idempotency.complete_idempotency(
                            request.request_id,
                            workflow_id=workflow_id,
                            terminal_outcome_fingerprint=terminal_outcome_fingerprint(
                                outcome
                            ),
                            tenant_scope_fingerprint=scope_fingerprint,
                            **completion,
                        )
                except NL2DataError:
                    # The public outcome stands; the durable state remains
                    # reconcilable (at-least-once) if the commit failed.
                    pass
            return outcome
        finally:
            if lease is not None and lease_store is not None:
                with contextlib.suppress(SharedStoreError):
                    lease_store.release_lease(
                        workflow_id,
                        owner_id=lease.owner_id,
                        fencing_token=lease.fencing_token,
                        tenant_scope_fingerprint=scope_fingerprint,
                    )

    async def _execute_graph(
        self,
        request: QueryRequest,
        options: _GraphOptions,
        *,
        turn: CurrentTurnContext,
        cancellation: WorkflowCancellation | None,
    ) -> QueryOutcome:
        """Run the ordered graph from ``options.start_stage`` onward.

        Every stage entry re-validates mandatory gates, the cooperative
        deadline, and cancellation.  A stage checkpoint is persisted after
        each successful stage when a durable binding is attached.
        """
        scope_fingerprint = options.scope_fingerprint
        base_evidence = {
            WorkflowGate.TENANT_SCOPE: sha256_fingerprint(
                {"tenant_scope": scope_fingerprint}
            ),
            WorkflowGate.DEADLINE: sha256_fingerprint(
                {"deadline_at": options.deadline.deadline_at.isoformat()}
            ),
        }
        channel: dict[str, Any] = {
            "gate_evidence": dict(base_evidence),
            "compat": dict(options.compatibility),
            "turn": turn,
        }
        cancellation_ctx = cancellation or WorkflowCancellation()
        if options.cancellation_requested:
            cancellation_ctx = WorkflowCancellation(
                requested=True, reason=cancellation_ctx.reason
            )
        context = WorkflowExecutionContext(
            request=request,
            workflow_id=options.workflow_id,
            tenant_scope_fingerprint=scope_fingerprint,
            current_stage=options.start_stage,
            budget=self._budget,
            deadline=options.deadline,
            cancellation=cancellation_ctx,
            compatibility_fingerprints=dict(options.compatibility),
            gate_evidence_fingerprints=frozenset(base_evidence.values()),
        )
        stage = options.start_stage
        while stage is not None:
            try:
                result = await self._run_stage(
                    stage, context, channel, durable=options.durable
                )
            except RuntimeTimeoutError as error:
                return self._branch_outcome(
                    request, options, OutcomeStatus.REJECTED, as_error_record(error)
                )
            except RuntimeCancelledError as error:
                return self._branch_outcome(
                    request, options, OutcomeStatus.REJECTED, as_error_record(error)
                )
            except RuntimeRetryExhaustedError as error:
                return self._branch_outcome(
                    request, options, OutcomeStatus.REJECTED, as_error_record(error)
                )
            except ApprovalRequiredError as error:
                return self._branch_outcome(
                    request, options, OutcomeStatus.REJECTED, as_error_record(error)
                )
            except StaleCheckpointError as error:
                return self._branch_outcome(
                    request, options, OutcomeStatus.REJECTED, as_error_record(error)
                )
            except SharedStoreError as error:
                return self._branch_outcome(
                    request,
                    options,
                    (
                        OutcomeStatus.REJECTED
                        if error.is_public_rejected()
                        else OutcomeStatus.FAILED
                    ),
                    error.to_public_record(),
                )
            except NL2DataError as error:
                return self._branch_outcome(
                    request, options, OutcomeStatus.FAILED, as_error_record(error)
                )
            if result.status is not RuntimeOutcomeStatus.SUCCEEDED:
                outcome = result.outcome
                if outcome is None:
                    raise RuntimeGateError(
                        f"stage '{stage.value}' branched without a final outcome",
                        details={"stage": stage.value},
                    )
                return self._with_identity(outcome, options, scope_fingerprint)
            if result.next_stage is None:
                outcome = result.outcome
                if outcome is None:
                    raise RuntimeGateError(
                        f"stage '{stage.value}' succeeded without a final outcome",
                        details={"stage": stage.value},
                    )
                return self._with_identity(outcome, options, scope_fingerprint)
            stage = result.next_stage
            context = context.model_copy(
                update={
                    "current_stage": stage,
                    "gate_evidence_fingerprints": frozenset(
                        channel["gate_evidence"].values()
                    ),
                }
            )
        raise RuntimeGateError("workflow graph terminated without a final outcome")

    def _with_identity(
        self,
        outcome: QueryOutcome,
        options: _GraphOptions,
        scope_fingerprint: str | None,
    ) -> QueryOutcome:
        """Stamp the run identity onto a node-branch outcome.

        Branch helpers (denial, malformed intent, clarification) build the
        public outcome from the request alone; the durable path already
        repairs this, and the plain path must stay identical.
        """
        if (
            outcome.workflow_id is not None
            and outcome.tenant_scope_fingerprint is not None
        ):
            return outcome
        return outcome.model_copy(
            update={
                "workflow_id": options.workflow_id,
                "tenant_scope_fingerprint": scope_fingerprint,
            }
        )

    async def _run_stage(
        self,
        stage: WorkflowStage,
        context: WorkflowExecutionContext,
        channel: dict[str, Any],
        *,
        durable: _DurableBinding | None,
    ) -> StageResult:
        """Validate entry gates, run one node with bounded retries, checkpoint."""
        validate_stage_entry(
            stage,
            gate_evidence=channel["gate_evidence"],
            deadline=context.deadline,
            cancellation=context.cancellation,
            now=self._now(),
        )
        if durable is not None:
            channel.setdefault("stored_ir_identity", self._stored_ir_identity(durable.state))
        if stage is WorkflowStage.EXECUTE and durable is not None:
            self._reject_stale_ir(durable, channel)
        if durable is not None:
            # Keep the shared lease alive between stages; re-verify it
            # atomically right before external adapter work.
            if stage is WorkflowStage.EXECUTE:
                durable.reverify_ownership()
            else:
                durable.renew_if_due()
        node = _NODES[stage](self, channel)
        remaining = context.deadline.remaining_seconds(now=self._now())
        if remaining <= 0.0:
            raise RuntimeTimeoutError(
                f"workflow deadline expired before stage '{stage.value}'",
                details={"stage": stage.value},
            )
        try:
            result = await asyncio.wait_for(
                self._run_with_retries(node, context, channel), timeout=remaining
            )
        except TimeoutError as error:
            raise RuntimeTimeoutError(
                f"stage '{stage.value}' exceeded the workflow deadline",
                details={"stage": stage.value, "timeout_seconds": str(remaining)},
            ) from error
        if durable is not None and result.status is RuntimeOutcomeStatus.SUCCEEDED:
            stage_metadata: dict[str, str] | None = None
            ir = channel.get("ir")
            if ir is not None:
                stage_metadata = {
                    "ir_version": str(ir.ir_version),
                    "ir_fingerprint": ir.fingerprint,
                }
                if self._view is not None and self._view.view_bound:
                    assert self._view.view_id is not None
                    assert self._view.view_version is not None
                    assert self._view.view_fingerprint is not None
                    stage_metadata["view_id"] = self._view.view_id
                    stage_metadata["view_version"] = str(self._view.view_version)
                    stage_metadata["view_fingerprint"] = self._view.view_fingerprint
            lineage_fingerprint = channel.get("result_lineage_fingerprint")
            if lineage_fingerprint is not None:
                if stage_metadata is None:
                    stage_metadata = {}
                stage_metadata["lineage_fingerprint"] = lineage_fingerprint
            durable.checkpoint(
                stage=result.next_stage or stage,
                compatibility_fingerprints=dict(channel["compat"]),
                gate_evidence_fingerprints=frozenset(channel["gate_evidence"].values()),
                metadata=stage_metadata,
            )
        return result

    async def _run_with_retries(
        self,
        node: _NodeBase,
        context: WorkflowExecutionContext,
        channel: dict[str, Any],
    ) -> StageResult:
        """Run one node with a bounded retry budget for retryable failures.

        Runtime branch errors (timeout, cancellation, approval required)
        always propagate; retryable :class:`NL2DataError` failures retry up
        to the configured ``max_retries``, and the deadline is re-checked
        between attempts so retries cannot outlive the workflow.
        """
        attempts = 0
        while True:
            attempts += 1
            try:
                return await node.run(context)
            except RuntimeTimeoutError:
                raise
            except RuntimeCancelledError:
                raise
            except ApprovalRequiredError:
                raise
            except NL2DataError as error:
                if not error.retryable:
                    return StageResult(
                        stage=node.stage,
                        status=RuntimeOutcomeStatus.FAILED,
                        outcome=_failed(context.request, error),
                    )
                if attempts >= context.budget.max_retries:
                    raise RuntimeRetryExhaustedError(
                        f"node '{node.stage.value}' exhausted its retry budget",
                        details={
                            "stage": node.stage.value,
                            "attempts": str(attempts),
                            "max_retries": str(context.budget.max_retries),
                            "last_code": error.code.value,
                        },
                    ) from error
                validate_stage_entry(
                    node.stage,
                    gate_evidence=channel["gate_evidence"],
                    deadline=context.deadline,
                    cancellation=context.cancellation,
                    now=self._now(),
                )

    # -- durable resume helpers ---------------------------------------------

    def _validate_resume(
        self, state: WorkflowState, current_compat: dict[str, str]
    ) -> WorkflowState:
        """Reject a checkpoint that cannot resume safely.

        Compatibility fingerprints must match the current configuration;
        the stored deadline must not be expired; a requested cancellation
        fails fast; post-execution stages are ambiguous and never
        re-executed; and the bounded resume retry budget is enforced.
        """
        stored = state.compatibility_fingerprints
        for key, fingerprint in sorted(stored.items()):
            if key in _RUNTIME_ACCUMULATED_COMPAT_KEYS:
                continue
            current_fingerprint = current_compat.get(key)
            if current_fingerprint is None or current_fingerprint != fingerprint:
                raise StaleCheckpointError(
                    f"checkpoint is incompatible with the current '{key}' configuration",
                    details={
                        "key": key,
                        "workflow_id": state.workflow_id,
                    },
                )
        if state.deadline_at is not None and state.deadline_at <= self._now():
            raise RuntimeTimeoutError(
                "stored workflow deadline has expired",
                details={
                    "deadline_at": state.deadline_at.isoformat(),
                    "workflow_id": state.workflow_id,
                },
            )
        if state.cancellation_requested:
            raise RuntimeCancelledError(
                "workflow cancellation was requested before resume",
                details={"workflow_id": state.workflow_id},
            )
        if state.current_stage in _AMBIGUOUS_STAGES:
            raise RuntimeRecoverableError(
                "checkpoint is after external execution; re-execution is not safe",
                details={
                    "workflow_id": state.workflow_id,
                    "stage": (
                        state.current_stage.value
                        if state.current_stage is not None
                        else ""
                    ),
                },
            )
        if state.retry_count >= state.budget.max_retries:
            raise RuntimeRetryExhaustedError(
                "workflow retry budget exhausted",
                details={
                    "workflow_id": state.workflow_id,
                    "retry_count": str(state.retry_count),
                    "max_retries": str(state.budget.max_retries),
                },
            )
        return state

    def _advance_to_running(
        self,
        store: StateStore,
        workflow_id: str,
        request: QueryRequest,
        state: WorkflowState,
        *,
        scope_fingerprint: str | None,
        owner_id: str | None = None,
        fencing_token: int | None = None,
    ) -> WorkflowState:
        """Move a checkpoint toward RUNNING through allowed transition edges.

        A RUNNING checkpoint means a previous execution stopped before a
        terminal commit; the retry edge records the recovery attempt so the
        attempt budget still bounds re-execution and the bounded resume
        retry counter advances.  Ownership arguments are forwarded to a
        fenced shared store so every transition is lease-protected.
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
                owner_id=owner_id,
                fencing_token=fencing_token,
            )
            return self._step(
                store,
                workflow_id,
                request,
                queued,
                WorkflowStatus.RUNNING,
                scope_fingerprint=scope_fingerprint,
                owner_id=owner_id,
                fencing_token=fencing_token,
            )
        if status == WorkflowStatus.QUEUED:
            return self._step(
                store,
                workflow_id,
                request,
                state,
                WorkflowStatus.RUNNING,
                scope_fingerprint=scope_fingerprint,
                owner_id=owner_id,
                fencing_token=fencing_token,
            )
        if status == WorkflowStatus.RUNNING:
            queued = self._step(
                store,
                workflow_id,
                request,
                state,
                WorkflowStatus.QUEUED,
                scope_fingerprint=scope_fingerprint,
                owner_id=owner_id,
                fencing_token=fencing_token,
            )
            return self._step(
                store,
                workflow_id,
                request,
                queued,
                WorkflowStatus.RUNNING,
                scope_fingerprint=scope_fingerprint,
                retry_count=queued.retry_count + 1,
                owner_id=owner_id,
                fencing_token=fencing_token,
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
        retry_count: int | None = None,
        owner_id: str | None = None,
        fencing_token: int | None = None,
    ) -> WorkflowState:
        """Persist one validated transition with compare-and-set."""
        revision = store.get_revision(
            workflow_id, tenant_scope_fingerprint=scope_fingerprint
        )
        next_state = transition(
            state,
            target,
            event_id=f"ev-{uuid4().hex[:16]}",
            retry_count=retry_count,
        )
        if isinstance(store, FencedStateStore):
            store.update(
                workflow_id,
                state.status,
                next_state,
                expected_version=revision,
                tenant_scope_fingerprint=scope_fingerprint,
                owner_id=owner_id,
                fencing_token=fencing_token,
            )
        else:
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

    # -- shared helpers ------------------------------------------------------

    def _branch_outcome(
        self,
        request: QueryRequest,
        options: _GraphOptions,
        status: OutcomeStatus,
        error: ErrorRecord,
    ) -> QueryOutcome:
        return _outcome(
            request,
            status=status,
            error=error,
            workflow_id=options.workflow_id,
            tenant_scope_fingerprint=options.scope_fingerprint,
        )

    # -- canonical IR helpers ------------------------------------------------

    @staticmethod
    def _current_ir_identity(channel: dict[str, Any]) -> tuple[int, str] | None:
        """The freshly derived canonical IR identity of the current run."""
        ir = channel.get("ir")
        if ir is None:
            return None
        return (ir.ir_version, ir.fingerprint)

    @staticmethod
    def _stored_ir_identity(state: WorkflowState) -> tuple[int, str] | None:
        """The IR identity recorded by the newest checkpoint event, if any.

        A checkpoint that carries a partial or malformed IR identity is
        treated as stale (fail closed) so resume never silently ignores
        tampered evidence.
        """
        for event in reversed(state.events):
            version = event.metadata.get("ir_version")
            fingerprint = event.metadata.get("ir_fingerprint")
            if version is None and fingerprint is None:
                continue
            if version is None or fingerprint is None:
                raise StaleCheckpointError(
                    "checkpoint carries a partial IR identity",
                    details={"workflow_id": state.workflow_id},
                )
            try:
                return (int(version), fingerprint)
            except ValueError:
                raise StaleCheckpointError(
                    "checkpoint carries an invalid IR version",
                    details={"workflow_id": state.workflow_id, "ir_version": version[:64]},
                ) from None
        return None

    def _reject_stale_ir(self, durable: _DurableBinding, channel: dict[str, Any]) -> None:
        """Reject a resumed run whose IR derivation changed since checkpoint.

        The IR identity recorded by the original run's checkpoint is
        compared against the freshly derived IR before adapter execution; a
        mismatch means the derivation changed and executing the new
        artifact would contradict the checkpointed evidence.  Checkpoints
        written before IR evidence existed (the legacy window) carry no
        identity and resume untouched.
        """
        stored = channel.get("stored_ir_identity")
        current = self._current_ir_identity(channel)
        if stored is None or current is None or stored == current:
            return
        raise StaleCheckpointError(
            "checkpoint IR derivation is stale; refusing adapter execution",
            details={
                "workflow_id": durable.state.workflow_id,
                "stored_ir_fingerprint": stored[1],
                "current_ir_fingerprint": current[1],
            },
        )

    def _prepare_turn(self, request: QueryRequest) -> tuple[CurrentTurnContext, dict[str, str]]:
        """Build the trusted current-turn context and base compatibility map."""
        execution = self._execution
        assert execution is not None
        view = self._view
        assert view is not None
        context = request.context
        conversation_id = (
            context.conversation_id
            if context is not None and context.conversation_id
            else (
                context.workflow_id
                if context is not None and context.workflow_id
                else request.request_id
            )
        )
        session_id = (
            context.workflow_id if context is not None and context.workflow_id else conversation_id
        )
        turn = build_current_turn_context(
            session_id=session_id,
            conversation_id=conversation_id,
            tenant_scope=execution.tenant_context,
            view=view,
            policy_scope=execution.policy_scope,
            adapter_id=execution.adapter_type,
        )
        policy_scope = execution.policy_scope
        assert policy_scope is not None
        semantic_view_fingerprint = turn.semantic_view_fingerprint
        assert semantic_view_fingerprint is not None
        compat: dict[str, str] = {
            "config": self._config.fingerprint,
            "policy": policy_scope.policy_fingerprint,
            "semantic": semantic_view_fingerprint,
        }
        if view.view_bound:
            compat["view"] = self._view_compat_fingerprint()
        catalog_fingerprint = view.catalog_fingerprint
        if catalog_fingerprint is not None:
            compat["catalog"] = catalog_fingerprint
        return turn, compat

    def _view_reference(self) -> IRViewReference | None:
        """The resolved-view reference binding every produced IR, if bound.

        An unbound view (legacy compatibility mode) carries no resolved-view
        identity, so no reference is fabricated and existing unbound IR
        keeps executing unchanged.
        """
        view = self._view
        if view is None or not view.view_bound:
            return None
        assert view.view_id is not None
        assert view.view_version is not None
        assert view.view_fingerprint is not None
        return IRViewReference(
            view_id=view.view_id,
            view_version=view.view_version,
            view_fingerprint=view.view_fingerprint,
        )

    def _view_compat_fingerprint(self) -> str:
        """The checkpoint compatibility fingerprint of the bound resolved view.

        The identity and fingerprint of the resolved view participate in
        resume validation, so a checkpoint recorded under a stale view is
        rejected before any adapter execution.
        """
        view = self._view
        assert view is not None and view.view_bound
        assert view.view_id is not None
        assert view.view_version is not None
        assert view.view_fingerprint is not None
        return sha256_fingerprint(
            {
                "view_id": view.view_id,
                "view_version": view.view_version,
                "view_fingerprint": view.view_fingerprint,
            }
        )

    def _scope_fingerprint(self) -> str | None:
        if self._execution is None or self._execution.tenant_context is None:
            return None
        return self._execution.tenant_context.scope_fingerprint

    def _resolve_workflow_id(self, request: QueryRequest) -> str:
        """The explicit workflow identity from the request, or a fresh one."""
        if request.context is not None and request.context.workflow_id is not None:
            return request.context.workflow_id
        return f"workflow-{uuid4().hex[:16]}"

    def _now(self) -> datetime:
        return self._now_fn()

    @staticmethod
    def _facts_from_ir(
        ir: SemanticQueryIR,
        *,
        binding: PhysicalBinding | None = None,
        tenant_scope_fingerprint: str | None = None,
        isolation_profile: str | None = None,
        view_fingerprint: str | None = None,
        bundle_fingerprint: str | None = None,
        capability_ids: frozenset[str] = frozenset(),
        artifact_fingerprint: str | None = None,
    ) -> GovernanceFacts:
        resource_ids = {binding.object_id} if binding is not None else {ir.root_entity_id}
        return GovernanceFacts(
            source_id=ir.source_id,
            operation="select",
            resource_ids=frozenset(resource_ids),
            field_ids=ir.field_ids(),
            filter_fingerprints=ir.filter_fingerprints(),
            ir_fingerprint=ir.fingerprint,
            view_fingerprint=view_fingerprint,
            bundle_fingerprint=bundle_fingerprint,
            capability_ids=capability_ids,
            artifact_fingerprint=artifact_fingerprint,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
            isolation_profile=isolation_profile,
        )
