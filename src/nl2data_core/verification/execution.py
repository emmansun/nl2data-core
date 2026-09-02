"""Governed transient execution for verification smoke and semantic cases."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from threading import Event
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nl2data_core.assembly.manifest import AcceptedAssertionManifest

# Import from the owning module, not the bundles package facade: the
# facade re-exports bundles.catalog, which lazily imports verification.suite
# for production evidence checks, and a facade import here would close an
# import cycle caught by the architecture graph once relative imports are
# resolved.
from nl2data_core.bundles.models import SemanticModelBundle
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.evaluation.models import EvaluationRunContext
from nl2data_core.evaluation.sqlite_executor import SqliteCaseExecutor
from nl2data_core.fixtures.sqlite import SQLiteFixtureProfile
from nl2data_core.governance.models import EffectiveLimits, PolicyScope
from nl2data_core.planning.ir.models import SemanticQueryIR
from nl2data_core.planning.models import PhysicalBinding
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.verification.policy import VerificationPolicy

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_MAX_OBSERVATION_ROWS = 1_000
_MAX_OBSERVATION_COLUMNS = 1_000
_PROTECTED_SCALARS = (str, int, float, bool, type(None))


class VerificationObservationStatus(StrEnum):
    """Transient executor outcomes before assertion reduction."""

    SUCCEEDED = "succeeded"
    ERROR = "error"
    UNAVAILABLE = "unavailable"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class VerificationCancellation:
    """Thread-safe cooperative cancellation shared across suite execution."""

    def __init__(self) -> None:
        self._event = Event()

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    def request(self) -> None:
        self._event.set()


class VerificationExecutionContext(BaseModel):
    """Frozen candidate, scope, policy, and deadline bindings for execution."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    candidate: SemanticModelBundle
    manifest: AcceptedAssertionManifest
    view: AuthorizedView
    policy: VerificationPolicy
    policy_scope: PolicyScope
    effective_limits: EffectiveLimits = Field(default_factory=EffectiveLimits)
    tenant_scope_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    source_scope_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    deadline_at: datetime
    cancellation: VerificationCancellation = Field(default_factory=VerificationCancellation)

    @model_validator(mode="after")
    def _validate_bindings(self) -> VerificationExecutionContext:
        if (
            self.manifest.bundle_id != self.candidate.bundle_id
            or self.manifest.bundle_fingerprint != self.candidate.fingerprint
        ):
            raise ValueError("manifest must bind to the frozen candidate Bundle")
        source_id = self.candidate.descriptor.source_id
        if self.view.source_id != source_id or source_id not in self.policy_scope.source_ids:
            raise ValueError("view and policy source scope must bind to the candidate Bundle")
        if self.policy_scope.tenant_scope_fingerprint not in {
            None,
            self.tenant_scope_fingerprint,
        }:
            raise ValueError("policy tenant scope must bind to the verification context")
        candidate_fields = frozenset(
            field.field_id
            for entity in self.candidate.descriptor.entities
            for field in entity.fields
        )
        if not self.view.field_ids.issubset(candidate_fields):
            raise ValueError("authorized view fields must come from the candidate Bundle")
        return self

    def remaining_seconds(self, *, now: datetime | None = None) -> float:
        current = now or datetime.now(UTC)
        deadline = self.deadline_at
        if deadline.tzinfo is None:
            raise ValueError("verification deadlines must be timezone-aware")
        return max(0.0, (deadline - current).total_seconds())


class VerificationObservation(BaseModel):
    """Bounded transient protected values reduced before persistence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: VerificationObservationStatus
    executor_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    executor_capability_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    bundle_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    ir_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    fixture_setup_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    selection_ids: tuple[str, ...] = Field(
        default_factory=tuple, max_length=_MAX_OBSERVATION_COLUMNS
    )
    rows: tuple[tuple[Any, ...], ...] = Field(
        default_factory=tuple, max_length=_MAX_OBSERVATION_ROWS
    )
    result_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    error_code: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    cleanup_issue_code: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @field_validator("rows")
    @classmethod
    def _validate_rows(cls, value: tuple[tuple[Any, ...], ...]) -> tuple[tuple[Any, ...], ...]:
        for row in value:
            if len(row) > _MAX_OBSERVATION_COLUMNS:
                raise ValueError("verification observation row is too wide")
            if any(not isinstance(cell, _PROTECTED_SCALARS) for cell in row):
                raise ValueError("verification observations permit protected scalars only")
        return value

    @model_validator(mode="after")
    def _validate_and_fingerprint(self) -> VerificationObservation:
        if any(len(row) != len(self.selection_ids) for row in self.rows):
            raise ValueError("observation rows must match the selection shape")
        object.__setattr__(
            self,
            "fingerprint",
            sha256_fingerprint(
                {
                    "status": self.status.value,
                    "executor_id": self.executor_id,
                    "executor_capability_fingerprint": self.executor_capability_fingerprint,
                    "bundle_fingerprint": self.bundle_fingerprint,
                    "ir_fingerprint": self.ir_fingerprint,
                    "fixture_setup_fingerprint": self.fixture_setup_fingerprint,
                    "selection_ids": self.selection_ids,
                    "row_count": len(self.rows),
                    "result_fingerprint": self.result_fingerprint,
                    "error_code": self.error_code,
                    "cleanup_issue_code": self.cleanup_issue_code,
                }
            ),
        )
        return self


class VerificationFixtureSession(Protocol):
    """Replaceable lifecycle for one controlled fixture."""

    @property
    def fixture_id(self) -> str: ...

    @property
    def setup_fingerprint(self) -> str: ...

    def setup(self) -> None: ...

    def reset(self) -> None: ...

    def dispose(self) -> None: ...


class VerificationExecutor(Protocol):
    """Replaceable governed executor for frozen semantic IR."""

    @property
    def executor_id(self) -> str: ...

    @property
    def capability_ids(self) -> frozenset[str]: ...

    @property
    def capability_fingerprint(self) -> str: ...

    async def open_session(
        self, fixture_profile_id: str, context: VerificationExecutionContext
    ) -> VerificationFixtureSession: ...

    async def execute(
        self,
        ir: SemanticQueryIR,
        session: VerificationFixtureSession,
        context: VerificationExecutionContext,
    ) -> VerificationObservation: ...


class SQLiteVerificationFixtureSession:
    """Verification fixture session backed by the existing SQLite profile."""

    def __init__(self, profile: SQLiteFixtureProfile) -> None:
        self.profile = profile

    @property
    def fixture_id(self) -> str:
        return self.profile.spec.fixture_id

    @property
    def setup_fingerprint(self) -> str:
        return self.profile.spec.setup_fingerprint

    def setup(self) -> None:
        self.profile.provision()

    def reset(self) -> None:
        self.profile.reset()

    def dispose(self) -> None:
        self.profile.dispose()


def execution_key(
    ir: SemanticQueryIR,
    *,
    fixture_profile_id: str,
    context: VerificationExecutionContext,
    executor: VerificationExecutor,
) -> str:
    """Fingerprint every input that can affect one governed observation."""
    return sha256_fingerprint(
        {
            "bundle_fingerprint": context.candidate.fingerprint,
            "manifest_fingerprint": sha256_fingerprint(context.manifest.canonical_payload()),
            "ir_fingerprint": ir.fingerprint,
            "view_fingerprint": context.view.view_fingerprint,
            "policy_fingerprint": context.policy.fingerprint,
            "policy_scope_fingerprint": context.policy_scope.policy_fingerprint,
            "effective_limits": context.effective_limits.model_dump(mode="json"),
            "tenant_scope_fingerprint": context.tenant_scope_fingerprint,
            "source_scope_fingerprint": context.source_scope_fingerprint,
            "fixture_profile_id": fixture_profile_id,
            "executor_id": executor.executor_id,
            "executor_capability_fingerprint": executor.capability_fingerprint,
        }
    )


class VerificationExecutionCache:
    """Coalesce identical in-flight and completed executions by deterministic key."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Future[VerificationObservation]] = {}

    async def execute_once(
        self,
        key: str,
        factory: Callable[[], Awaitable[VerificationObservation]],
    ) -> VerificationObservation:
        task = self._tasks.get(key)
        if task is None:
            task = asyncio.ensure_future(factory())
            self._tasks[key] = task
        return await task

    def release(self) -> None:
        self._tasks.clear()


class SQLiteReferenceVerificationExecutor:
    """Reference executor composed from existing governed SQLite boundaries."""

    executor_id = "nl2data-sqlite-verification"
    capability_ids = frozenset(
        {"aggregation", "filtering", "grouping", "ordering", "protected_results"}
    )

    def __init__(
        self,
        *,
        fixture_profiles: Mapping[str, SQLiteFixtureProfile],
        binding: PhysicalBinding,
    ) -> None:
        self._fixture_profiles = dict(fixture_profiles)
        self._binding = binding
        self._capability_fingerprint = sha256_fingerprint(
            {
                "executor_id": self.executor_id,
                "executor_version": 1,
                "capabilities": sorted(self.capability_ids),
                "observation_version": 1,
            }
        )

    @property
    def capability_fingerprint(self) -> str:
        return self._capability_fingerprint

    async def open_session(
        self, fixture_profile_id: str, context: VerificationExecutionContext
    ) -> VerificationFixtureSession:
        del context
        profile = self._fixture_profiles.get(fixture_profile_id)
        if profile is None:
            raise LookupError("verification fixture profile is unavailable")
        return SQLiteVerificationFixtureSession(profile)

    async def execute(
        self,
        ir: SemanticQueryIR,
        session: VerificationFixtureSession,
        context: VerificationExecutionContext,
    ) -> VerificationObservation:
        if not isinstance(session, SQLiteVerificationFixtureSession):
            raise TypeError("SQLite verification requires a SQLite fixture session")
        if context.cancellation.requested:
            return self._empty_observation(
                ir, session, context, VerificationObservationStatus.CANCELLED, "cancelled"
            )
        remaining = min(
            context.remaining_seconds(), context.effective_limits.max_execution_seconds
        )
        if remaining <= 0:
            return self._empty_observation(
                ir, session, context, VerificationObservationStatus.TIMED_OUT, "timed_out"
            )
        if ir.source_id != context.candidate.descriptor.source_id:
            return self._empty_observation(
                ir, session, context, VerificationObservationStatus.ERROR, "candidate_drift"
            )
        if not set(ir.required_capabilities).issubset(self.capability_ids):
            return self._empty_observation(
                ir,
                session,
                context,
                VerificationObservationStatus.UNAVAILABLE,
                "capability_mismatch",
            )
        executor = SqliteCaseExecutor(
            policy_scope=context.policy_scope,
            view=context.view,
            binding=self._binding,
            effective_limits=context.effective_limits,
            max_rows=context.effective_limits.max_rows,
        )
        run_context = EvaluationRunContext(
            run_id=f"verify-{ir.ir_id}",
            fixture_id=session.fixture_id,
            profile="sqlite",
        )
        try:
            evidence = await asyncio.wait_for(
                executor.execute(ir, session.profile, run_context),
                timeout=remaining,
            )
        except TimeoutError:
            return self._empty_observation(
                ir, session, context, VerificationObservationStatus.TIMED_OUT, "timed_out"
            )
        if evidence.error is not None:
            return self._empty_observation(
                ir,
                session,
                context,
                VerificationObservationStatus.ERROR,
                evidence.error.code.value,
            )
        selection_ids = self._selection_ids(ir, evidence.columns)
        return VerificationObservation(
            status=VerificationObservationStatus.SUCCEEDED,
            executor_id=self.executor_id,
            executor_capability_fingerprint=self.capability_fingerprint,
            bundle_fingerprint=context.candidate.fingerprint,
            ir_fingerprint=ir.fingerprint,
            fixture_setup_fingerprint=session.setup_fingerprint,
            selection_ids=selection_ids,
            rows=evidence.rows,
            result_fingerprint=evidence.result_fingerprint,
        )

    async def run_case(
        self,
        ir: SemanticQueryIR,
        *,
        fixture_profile_id: str,
        context: VerificationExecutionContext,
    ) -> VerificationObservation:
        """Execute one case with guaranteed reset and disposal."""
        try:
            session = await self.open_session(fixture_profile_id, context)
        except (LookupError, OSError):
            return self._unavailable_without_session(ir, context)
        try:
            session.setup()
            observation = await self.execute(ir, session, context)
        except (OSError, ValueError):
            observation = self._empty_observation(
                ir,
                session,
                context,
                VerificationObservationStatus.UNAVAILABLE,
                "fixture_unavailable",
            )
        cleanup_issue: str | None = None
        try:
            session.reset()
        except (OSError, ValueError):
            cleanup_issue = "fixture_reset_failed"
        try:
            session.dispose()
        except (OSError, ValueError):
            cleanup_issue = cleanup_issue or "fixture_disposal_failed"
        if cleanup_issue is not None:
            observation = VerificationObservation.model_validate(
                {**observation.model_dump(), "cleanup_issue_code": cleanup_issue}
            )
        return observation

    def _selection_ids(
        self, ir: SemanticQueryIR, columns: tuple[str, ...]
    ) -> tuple[str, ...]:
        names: dict[str, str] = {}
        for selection in ir.selections:
            physical_name = self._binding.physical_name(selection.field_id)
            if selection.alias is not None:
                output_name = selection.alias
            elif physical_name is not None and selection.aggregation != "none":
                output_name = f"{selection.aggregation}_{physical_name}"
            else:
                output_name = physical_name or selection.field_id
            names[output_name] = selection.selection_id
        return tuple(names[column] for column in columns)

    def _empty_observation(
        self,
        ir: SemanticQueryIR,
        session: VerificationFixtureSession,
        context: VerificationExecutionContext,
        status: VerificationObservationStatus,
        error_code: str,
    ) -> VerificationObservation:
        return VerificationObservation(
            status=status,
            executor_id=self.executor_id,
            executor_capability_fingerprint=self.capability_fingerprint,
            bundle_fingerprint=context.candidate.fingerprint,
            ir_fingerprint=ir.fingerprint,
            fixture_setup_fingerprint=session.setup_fingerprint,
            error_code=error_code,
        )

    def _unavailable_without_session(
        self, ir: SemanticQueryIR, context: VerificationExecutionContext
    ) -> VerificationObservation:
        return VerificationObservation(
            status=VerificationObservationStatus.UNAVAILABLE,
            executor_id=self.executor_id,
            executor_capability_fingerprint=self.capability_fingerprint,
            bundle_fingerprint=context.candidate.fingerprint,
            ir_fingerprint=ir.fingerprint,
            fixture_setup_fingerprint=sha256_fingerprint({"fixture": "unavailable"}),
            error_code="fixture_unavailable",
        )