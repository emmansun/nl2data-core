"""Governed verification executor, fixture lifecycle, and cache tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from nl2data_core.assembly import ASSEMBLY_API_VERSION, AssemblyDraft, AssemblyState
from nl2data_core.assembly.manifest import AcceptedAssertionManifest
from nl2data_core.bundles import (
    BundleProvenance,
    BundleQualityStatus,
    SemanticModelBundle,
    SemanticSourceReference,
)
from nl2data_core.fixtures import SQLiteFixtureProfile
from nl2data_core.governance.models import PolicyScope
from nl2data_core.planning.ir.models import (
    IRFilter,
    IROrdering,
    IRProvenance,
    IRSelection,
    SemanticQueryIR,
)
from nl2data_core.planning.models import ColumnBinding, PhysicalBinding
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.verification import (
    COMPATIBILITY_POLICY,
    PRODUCTION_POLICY,
    AggregateTotalContract,
    ErrorCodeAssertion,
    ExactProtectedResultContract,
    IsNullAssertion,
    MappingOutcomeContract,
    NullBehaviorContract,
    OutcomeAssertion,
    ResultShapeAssertion,
    RowCountAssertion,
    RowCountEqualityContract,
    RowCountRangeContract,
    ScalarEqualityContract,
    ScalarEqualsAssertion,
    SemanticContractCase,
    SmokeQueryCase,
    StructuredErrorCodeContract,
    TaggedExpectedScalar,
    VerificationLayerEvidence,
    VerificationPlan,
)
from nl2data_core.verification.execution import (
    SQLiteReferenceVerificationExecutor,
    VerificationExecutionCache,
    VerificationExecutionContext,
    VerificationObservation,
    VerificationObservationStatus,
    execution_key,
)
from nl2data_core.verification.semantic import SemanticContractEvaluator
from nl2data_core.verification.smoke import SmokeVerificationEvaluator
from nl2data_core.verification.suite import (
    VerificationSuiteRunner,
    evidence_satisfies_policy,
    publication_verification_classification,
    validate_bound_evidence,
)
from nl2data_core.views import (
    SemanticDescriptor,
    SemanticEntityDescriptor,
    SemanticFieldDescriptor,
)

_SCOPE = "sha256:" + "a" * 64


def _candidate() -> SemanticModelBundle:
    descriptor = SemanticDescriptor(
        descriptor_id="sales",
        version=1,
        source_id="sales",
        entities=(
            SemanticEntityDescriptor(
                entity_id="order",
                label="Order",
                fields=tuple(
                    SemanticFieldDescriptor(field_id=field_id, label=field_id, data_type=data_type)
                    for field_id, data_type in (
                        ("order_id", "int"),
                        ("amount", "float"),
                        ("region", "str"),
                    )
                ),
            ),
        ),
    )
    return SemanticModelBundle(
        bundle_id="sales",
        model_version="1.0.0",
        descriptor=descriptor,
        sources=(SemanticSourceReference(reference_id="sales", source_id="sales"),),
        provenance=BundleProvenance(
            owner_reference="team-analytics",
            quality=BundleQualityStatus.APPROVED,
        ),
    )


def _manifest(candidate: SemanticModelBundle) -> AcceptedAssertionManifest:
    draft = AssemblyDraft(
        apiVersion=ASSEMBLY_API_VERSION,
        draft_id="draft-execution",
        bundle_id="sales",
        source_id="sales",
        model_version="1.0.0",
        state=AssemblyState.APPROVED,
        author_reference="author-1",
    )
    return AcceptedAssertionManifest.from_draft(
        draft, bundle_fingerprint=candidate.fingerprint
    )


def _view() -> AuthorizedView:
    return AuthorizedView(
        source_id="sales",
        root_entity_ids=frozenset({"order"}),
        field_ids=frozenset({"order_id", "amount", "region"}),
    )


def _policy_scope() -> PolicyScope:
    return PolicyScope(
        policy_id="verification-policy",
        source_ids=frozenset({"sales"}),
        resource_ids=frozenset({"orders"}),
        operation_ids=frozenset({"select"}),
        field_ids=_view().field_ids,
    )


def _context(**overrides: object) -> VerificationExecutionContext:
    candidate = _candidate()
    values: dict[str, object] = {
        "candidate": candidate,
        "manifest": _manifest(candidate),
        "view": _view(),
        "policy": COMPATIBILITY_POLICY,
        "policy_scope": _policy_scope(),
        "tenant_scope_fingerprint": _SCOPE,
        "source_scope_fingerprint": _SCOPE,
        "deadline_at": datetime.now(UTC) + timedelta(seconds=10),
    }
    values.update(overrides)
    return VerificationExecutionContext(**values)  # type: ignore[arg-type]


def _ir(**overrides: object) -> SemanticQueryIR:
    values: dict[str, object] = {
        "ir_id": "verification-ir",
        "source_id": "sales",
        "root_entity_id": "order",
        "selections": (
            IRSelection(selection_id="order", field_id="order_id", alias="oid"),
            IRSelection(selection_id="amount", field_id="amount", alias="amt"),
        ),
        "filters": (IRFilter(filter_id="region", field_id="region", operator="eq", value="emea"),),
        "orderings": (IROrdering(ordering_id="amount", field_id="amount", direction="desc"),),
        "limit": 3,
        "provenance": IRProvenance(source_id="sales", root_entity_id="order"),
    }
    values.update(overrides)
    return SemanticQueryIR(**values)  # type: ignore[arg-type]


def _binding() -> PhysicalBinding:
    return PhysicalBinding(
        object_id="orders",
        dialect="sqlite",
        column_bindings=(
            ColumnBinding(field_id="order_id", physical_name="order_id"),
            ColumnBinding(field_id="amount", physical_name="amount"),
            ColumnBinding(field_id="region", physical_name="region"),
        ),
    )


def _executor(tmp_path: Path) -> SQLiteReferenceVerificationExecutor:
    return SQLiteReferenceVerificationExecutor(
        fixture_profiles={"sqlite-v1": SQLiteFixtureProfile(tmp_path / "verification.db")},
        binding=_binding(),
    )


class ResetFailingProfile(SQLiteFixtureProfile):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self.disposed = False

    def reset(self) -> None:
        raise ValueError("sensitive cleanup detail")

    def dispose(self) -> None:
        self.disposed = True
        super().dispose()


def test_execution_context_rejects_manifest_candidate_and_scope_drift() -> None:
    other = _candidate().model_copy(update={"bundle_id": "other"})
    with pytest.raises(ValidationError, match="manifest"):
        _context(candidate=other)
    with pytest.raises(ValidationError, match="tenant scope"):
        _context(
            policy_scope=_policy_scope().model_copy(
                update={"tenant_scope_fingerprint": "sha256:" + "b" * 64}
            )
        )


@pytest.mark.asyncio
async def test_sqlite_reference_executor_runs_governed_query_and_disposes(
    tmp_path: Path,
) -> None:
    executor = _executor(tmp_path)
    observation = await executor.run_case(
        _ir(), fixture_profile_id="sqlite-v1", context=_context()
    )
    assert observation.status is VerificationObservationStatus.SUCCEEDED
    assert observation.selection_ids == ("order", "amount")
    assert observation.rows == ((18, 180.0), (17, 170.0), (16, 160.0))
    assert observation.result_fingerprint is not None
    assert not (tmp_path / "verification.db").exists()


@pytest.mark.asyncio
async def test_unavailable_capability_cancellation_and_timeout_are_bounded(
    tmp_path: Path,
) -> None:
    executor = _executor(tmp_path)
    unavailable = await executor.run_case(
        _ir(), fixture_profile_id="missing", context=_context()
    )
    capability = await executor.run_case(
        _ir(required_capabilities=("vector_search",)),
        fixture_profile_id="sqlite-v1",
        context=_context(),
    )
    cancelled_context = _context()
    cancelled_context.cancellation.request()
    cancelled = await executor.run_case(
        _ir(), fixture_profile_id="sqlite-v1", context=cancelled_context
    )
    timed_out = await executor.run_case(
        _ir(),
        fixture_profile_id="sqlite-v1",
        context=_context(deadline_at=datetime.now(UTC) - timedelta(seconds=1)),
    )
    assert unavailable.status is VerificationObservationStatus.UNAVAILABLE
    assert capability.error_code == "capability_mismatch"
    assert cancelled.status is VerificationObservationStatus.CANCELLED
    assert timed_out.status is VerificationObservationStatus.TIMED_OUT


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_mask_primary_outcome(tmp_path: Path) -> None:
    profile = ResetFailingProfile(tmp_path / "cleanup.db")
    executor = SQLiteReferenceVerificationExecutor(
        fixture_profiles={"sqlite-v1": profile}, binding=_binding()
    )
    context = _context()
    context.cancellation.request()
    observation = await executor.run_case(
        _ir(), fixture_profile_id="sqlite-v1", context=context
    )
    assert observation.status is VerificationObservationStatus.CANCELLED
    assert observation.cleanup_issue_code == "fixture_reset_failed"
    assert profile.disposed
    assert "sensitive" not in observation.model_dump_json()


@pytest.mark.asyncio
async def test_execution_cache_coalesces_identical_work_and_releases() -> None:
    cache = VerificationExecutionCache()
    calls = 0

    async def factory() -> VerificationObservation:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return VerificationObservation(
            status="succeeded",
            executor_id="stub",
            executor_capability_fingerprint=_SCOPE,
            bundle_fingerprint=_SCOPE,
            ir_fingerprint=_SCOPE,
            fixture_setup_fingerprint=_SCOPE,
        )

    first, second = await asyncio.gather(
        cache.execute_once(_SCOPE, factory),
        cache.execute_once(_SCOPE, factory),
    )
    assert first is second
    assert calls == 1
    cache.release()
    await cache.execute_once(_SCOPE, factory)
    assert calls == 2


def test_execution_key_and_observation_identity_exclude_values(tmp_path: Path) -> None:
    executor = _executor(tmp_path)
    context = _context()
    assert execution_key(
        _ir(), fixture_profile_id="sqlite-v1", context=context, executor=executor
    ) == execution_key(
        _ir(), fixture_profile_id="sqlite-v1", context=context, executor=executor
    )
    base = {
        "status": "succeeded",
        "executor_id": "stub",
        "executor_capability_fingerprint": _SCOPE,
        "bundle_fingerprint": _SCOPE,
        "ir_fingerprint": _SCOPE,
        "fixture_setup_fingerprint": _SCOPE,
        "selection_ids": ("value",),
        "result_fingerprint": _SCOPE,
    }
    first = VerificationObservation(**base, rows=(("secret-a",),))
    second = VerificationObservation(**base, rows=(("secret-b",),))
    assert first.fingerprint == second.fingerprint


class StubVerificationExecutor:
    executor_id = "stub-verification"
    capability_ids = frozenset({"aggregation", "filtering"})
    capability_fingerprint = _SCOPE

    def __init__(self, observation: VerificationObservation) -> None:
        self.observation = observation
        self.calls = 0

    async def open_session(self, fixture_profile_id, context):
        raise NotImplementedError

    async def execute(self, ir, session, context):
        raise NotImplementedError

    async def run_case(self, ir, *, fixture_profile_id, context):
        self.calls += 1
        return self.observation


def _observation(
    *,
    status: str = "succeeded",
    rows: tuple[tuple[object, ...], ...] = ((18, 180.0, None),),
    error_code: str | None = None,
) -> VerificationObservation:
    return VerificationObservation(
        status=status,
        executor_id="stub-verification",
        executor_capability_fingerprint=_SCOPE,
        bundle_fingerprint=_candidate().fingerprint,
        ir_fingerprint=_ir().fingerprint,
        fixture_setup_fingerprint=_SCOPE,
        selection_ids=("order", "amount", "nullable"),
        rows=rows,
        result_fingerprint=_SCOPE if status == "succeeded" else None,
        error_code=error_code,
    )


def _smoke_case(*assertions, query: SemanticQueryIR | None = None) -> SmokeQueryCase:
    return SmokeQueryCase(
        case_id="smoke-case",
        query=query or _ir(),
        fixture_profile_id="sqlite-v1",
        assertions=assertions,
    )


@pytest.mark.asyncio
async def test_smoke_evaluator_supports_all_success_assertions() -> None:
    executor = StubVerificationExecutor(_observation())
    evaluator = SmokeVerificationEvaluator(executor=executor)
    evidence = await evaluator.evaluate_case(
        _smoke_case(
            OutcomeAssertion(assertion_id="outcome", expected="success"),
            ResultShapeAssertion(
                assertion_id="shape", selection_ids=("order", "amount", "nullable")
            ),
            RowCountAssertion(assertion_id="rows", minimum=1, maximum=1),
            ScalarEqualsAssertion(
                assertion_id="scalar-int",
                selection_id="order",
                expected=TaggedExpectedScalar(kind="int", value=18),
            ),
            ScalarEqualsAssertion(
                assertion_id="scalar-decimal",
                selection_id="amount",
                expected=TaggedExpectedScalar(kind="decimal", value="180"),
            ),
            IsNullAssertion(assertion_id="null", selection_id="nullable"),
        ),
        _context(),
    )
    assert evidence.status.value == "passed"
    assert evidence.passed_assertion_count == 6
    assert "180" not in evidence.model_dump_json()
    assert "nullable" not in evidence.model_dump_json()


@pytest.mark.asyncio
async def test_smoke_evaluator_accepts_expected_structured_error() -> None:
    executor = StubVerificationExecutor(
        _observation(status="error", rows=(), error_code="governance_denied")
    )
    evidence = await SmokeVerificationEvaluator(executor=executor).evaluate_case(
        _smoke_case(
            OutcomeAssertion(assertion_id="outcome", expected="error"),
            ErrorCodeAssertion(
                assertion_id="code", expected_code="governance_denied"
            ),
        ),
        _context(),
    )
    assert evidence.status.value == "passed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("observation_status", "expected_status"),
    [
        ("error", "failed"),
        ("unavailable", "unavailable"),
        ("timed_out", "timed_out"),
        ("cancelled", "failed"),
    ],
)
async def test_smoke_evaluator_maps_every_nonpass_status(
    observation_status: str, expected_status: str
) -> None:
    executor = StubVerificationExecutor(_observation(status=observation_status, rows=()))
    evidence = await SmokeVerificationEvaluator(executor=executor).evaluate_case(
        _smoke_case(OutcomeAssertion(assertion_id="outcome", expected="success")),
        _context(),
    )
    assert evidence.status.value == expected_status


@pytest.mark.asyncio
async def test_smoke_preflight_rejects_unbounded_drift_and_capabilities() -> None:
    executor = StubVerificationExecutor(_observation())
    evaluator = SmokeVerificationEvaluator(executor=executor)
    unbounded = await evaluator.evaluate_case(
        _smoke_case(
            OutcomeAssertion(assertion_id="outcome", expected="success"),
            query=_ir(limit=None),
        ),
        _context(),
    )
    drifted = await evaluator.evaluate_case(
        _smoke_case(
            OutcomeAssertion(assertion_id="outcome", expected="success"),
            query=_ir(source_id="other"),
        ),
        _context(),
    )
    capabilities = await evaluator.evaluate_case(
        SmokeQueryCase(
            case_id="capabilities",
            query=_ir(),
            fixture_profile_id="sqlite-v1",
            capability_requirements={"capabilities": ("vector_search",)},
            assertions=(OutcomeAssertion(assertion_id="outcome", expected="success"),),
        ),
        _context(),
    )
    assert unbounded.issue_codes == ("unbounded_query_limit",)
    assert drifted.issue_codes == ("candidate_drift",)
    assert capabilities.issue_codes == ("capability_mismatch",)
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_smoke_layer_is_sorted_and_shares_identical_execution() -> None:
    executor = StubVerificationExecutor(_observation())
    evaluator = SmokeVerificationEvaluator(executor=executor)
    assertion = OutcomeAssertion(assertion_id="outcome", expected="success")
    cases = (
        _smoke_case(assertion).model_copy(update={"case_id": "b"}),
        _smoke_case(assertion).model_copy(update={"case_id": "a"}),
    )
    layer = await evaluator.evaluate_layer(cases, _context())
    assert tuple(case.case_id for case in layer.cases) == ("a", "b")
    assert executor.calls == 1
    evaluator.release()


def _semantic_case(*contracts) -> SemanticContractCase:
    return SemanticContractCase(
        case_id="semantic-case",
        query=_ir(),
        fixture_profile_id="sqlite-v1",
        contracts=contracts,
    )


@pytest.mark.asyncio
async def test_semantic_evaluator_supports_every_closed_success_contract() -> None:
    executor = StubVerificationExecutor(_observation())
    evidence = await SemanticContractEvaluator(executor=executor).evaluate_case(
        _semantic_case(
            ExactProtectedResultContract(
                assertion_id="exact", expected_fingerprint=_SCOPE
            ),
            ScalarEqualityContract(
                assertion_id="scalar",
                selection_id="order",
                expected=TaggedExpectedScalar(kind="int", value=18),
            ),
            RowCountEqualityContract(assertion_id="row-eq", expected=1),
            RowCountRangeContract(assertion_id="row-range", minimum=1, maximum=2),
            AggregateTotalContract(
                assertion_id="aggregate",
                selection_id="amount",
                expected=TaggedExpectedScalar(kind="decimal", value="180"),
            ),
            MappingOutcomeContract(
                assertion_id="mapping",
                selection_id="order",
                expected=TaggedExpectedScalar(kind="int", value=18),
            ),
        ),
        _context(),
    )
    assert evidence.status.value == "passed"
    assert evidence.passed_assertion_count == 6
    assert "180" not in evidence.model_dump_json()


@pytest.mark.asyncio
async def test_semantic_evaluator_supports_null_behavior() -> None:
    executor = StubVerificationExecutor(_observation(rows=((18, None, None),)))
    evidence = await SemanticContractEvaluator(executor=executor).evaluate_case(
        _semantic_case(
            NullBehaviorContract(
                assertion_id="null", selection_id="amount", expected_null=True
            )
        ),
        _context(),
    )
    assert evidence.status.value == "passed"


@pytest.mark.asyncio
async def test_semantic_evaluator_supports_structured_error_contract() -> None:
    executor = StubVerificationExecutor(
        _observation(status="error", rows=(), error_code="division_by_zero")
    )
    evidence = await SemanticContractEvaluator(executor=executor).evaluate_case(
        _semantic_case(
            StructuredErrorCodeContract(
                assertion_id="error", expected_code="division_by_zero"
            )
        ),
        _context(),
    )
    assert evidence.status.value == "passed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "contract",
    [
        MappingOutcomeContract(
            assertion_id="mapping",
            selection_id="order",
            expected=TaggedExpectedScalar(kind="int", value=19),
        ),
        AggregateTotalContract(
            assertion_id="aggregate",
            selection_id="amount",
            expected=TaggedExpectedScalar(kind="decimal", value="181"),
        ),
        NullBehaviorContract(
            assertion_id="null", selection_id="amount", expected_null=True
        ),
        ScalarEqualityContract(
            assertion_id="type",
            selection_id="amount",
            expected=TaggedExpectedScalar(kind="int", value=180),
        ),
    ],
)
async def test_semantic_drift_produces_safe_failure_evidence(contract) -> None:
    executor = StubVerificationExecutor(_observation())
    evidence = await SemanticContractEvaluator(executor=executor).evaluate_case(
        _semantic_case(contract), _context()
    )
    assert evidence.status.value == "failed"
    assert evidence.issue_codes == ("semantic_contract_mismatch",)
    assert "181" not in evidence.model_dump_json()
    assert "nullable" not in evidence.model_dump_json()


@pytest.mark.asyncio
async def test_semantic_preflight_rejects_unknown_selection_before_execution() -> None:
    executor = StubVerificationExecutor(_observation())
    evidence = await SemanticContractEvaluator(executor=executor).evaluate_case(
        _semantic_case(
            ScalarEqualityContract(
                assertion_id="unknown",
                selection_id="physical_column",
                expected=TaggedExpectedScalar(kind="int", value=1),
            )
        ),
        _context(),
    )
    assert evidence.issue_codes == ("unknown_semantic_selection",)
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_smoke_and_semantic_contracts_share_execution_but_not_evaluation() -> None:
    executor = StubVerificationExecutor(_observation())
    cache = VerificationExecutionCache()
    smoke = SmokeVerificationEvaluator(executor=executor, cache=cache)
    semantic = SemanticContractEvaluator(executor=executor, cache=cache)
    context = _context()
    smoke_evidence = await smoke.evaluate_case(
        _smoke_case(OutcomeAssertion(assertion_id="outcome", expected="success")),
        context,
    )
    semantic_evidence = await semantic.evaluate_case(
        _semantic_case(RowCountEqualityContract(assertion_id="rows", expected=2)),
        context,
    )
    assert smoke_evidence.status.value == "passed"
    assert semantic_evidence.status.value == "failed"
    assert executor.calls == 1
    cache.release()


def _production_plan() -> VerificationPlan:
    return VerificationPlan(
        policy_profile="production-v1",
        smoke_cases=(
            _smoke_case(
                OutcomeAssertion(assertion_id="outcome", expected="success")
            ),
        ),
        semantic_cases=(
            _semantic_case(RowCountEqualityContract(assertion_id="rows", expected=1)),
        ),
    )


def _structural(*, passed: bool = True) -> VerificationLayerEvidence:
    return VerificationLayerEvidence(
        layer="layer_1",
        status="passed" if passed else "failed",
        issue_codes=() if passed else ("structural_failure",),
    )


@pytest.mark.asyncio
async def test_compatibility_no_plan_is_explicit_structural_only() -> None:
    executor = StubVerificationExecutor(_observation())
    evidence = await VerificationSuiteRunner(executor=executor).run(
        plan=None,
        policy=COMPATIBILITY_POLICY,
        structural_evidence=_structural(),
        context=_context(),
        draft_id="draft-1",
        draft_revision=3,
    )
    assert evidence.status.value == "passed"
    assert tuple(layer.status.value for layer in evidence.layers) == (
        "passed",
        "not_run",
        "not_run",
    )
    assert evidence.executor_id is None
    assert publication_verification_classification(evidence) == "compatibility-v1"
    assert publication_verification_classification(None) == "legacy_unverified"


@pytest.mark.asyncio
async def test_production_runs_all_layers_shares_execution_and_is_deterministic() -> None:
    plan = _production_plan()
    context = _context(policy=PRODUCTION_POLICY)
    first_executor = StubVerificationExecutor(_observation())
    first = await VerificationSuiteRunner(executor=first_executor).run(
        plan=plan,
        policy=PRODUCTION_POLICY,
        structural_evidence=_structural(),
        context=context,
        draft_id="draft-1",
        draft_revision=3,
    )
    second_executor = StubVerificationExecutor(_observation())
    second = await VerificationSuiteRunner(executor=second_executor).run(
        plan=plan,
        policy=PRODUCTION_POLICY,
        structural_evidence=_structural(),
        context=context,
        draft_id="draft-1",
        draft_revision=3,
    )
    assert first.status.value == "passed"
    assert tuple(layer.status.value for layer in first.layers) == (
        "passed",
        "passed",
        "passed",
    )
    assert first_executor.calls == second_executor.calls == 1
    assert first.fingerprint == second.fingerprint
    assert validate_bound_evidence(
        first,
        plan=plan,
        policy=PRODUCTION_POLICY,
        context=context,
        draft_id="draft-1",
        draft_revision=3,
        executor=first_executor,
    )
    assert evidence_satisfies_policy(first, policy=PRODUCTION_POLICY)
    forged = first.model_copy(
        update={
            "layers": (first.layers[0],),
        }
    )
    assert not validate_bound_evidence(
        forged,
        plan=plan,
        policy=PRODUCTION_POLICY,
        context=context,
        draft_id="draft-1",
        draft_revision=3,
        executor=first_executor,
    )
    assert not evidence_satisfies_policy(forged, policy=PRODUCTION_POLICY)


@pytest.mark.asyncio
async def test_layer_one_and_policy_failures_short_circuit_execution() -> None:
    executor = StubVerificationExecutor(_observation())
    runner = VerificationSuiteRunner(executor=executor)
    structural_failure = await runner.run(
        plan=_production_plan(),
        policy=PRODUCTION_POLICY,
        structural_evidence=_structural(passed=False),
        context=_context(policy=PRODUCTION_POLICY),
        draft_id="draft-1",
        draft_revision=3,
    )
    missing_cases = await runner.run(
        plan=VerificationPlan(policy_profile="production-v1"),
        policy=PRODUCTION_POLICY,
        structural_evidence=_structural(),
        context=_context(policy=PRODUCTION_POLICY),
        draft_id="draft-1",
        draft_revision=3,
    )
    context_mismatch = await runner.run(
        plan=_production_plan(),
        policy=PRODUCTION_POLICY,
        structural_evidence=_structural(),
        context=_context(),
        draft_id="draft-1",
        draft_revision=3,
    )
    assert structural_failure.layers[1].status.value == "not_run"
    assert "smoke_case_minimum_not_met" in missing_cases.issue_codes
    assert "semantic_case_minimum_not_met" in missing_cases.issue_codes
    assert "context_policy_mismatch" in context_mismatch.issue_codes
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_required_nonpass_and_total_deadline_fail_closed() -> None:
    unavailable_executor = StubVerificationExecutor(
        _observation(status="unavailable", rows=())
    )
    unavailable = await VerificationSuiteRunner(executor=unavailable_executor).run(
        plan=_production_plan(),
        policy=PRODUCTION_POLICY,
        structural_evidence=_structural(),
        context=_context(policy=PRODUCTION_POLICY),
        draft_id="draft-1",
        draft_revision=3,
    )
    timeout_executor = StubVerificationExecutor(_observation())
    timed_out = await VerificationSuiteRunner(executor=timeout_executor).run(
        plan=_production_plan(),
        policy=PRODUCTION_POLICY,
        structural_evidence=_structural(),
        context=_context(
            policy=PRODUCTION_POLICY,
            deadline_at=datetime.now(UTC) - timedelta(milliseconds=1),
        ),
        draft_id="draft-1",
        draft_revision=3,
    )
    assert unavailable.status.value == "failed"
    assert unavailable.layers[1].status.value == "unavailable"
    assert unavailable.layers[2].status.value == "not_run"
    assert timed_out.status.value == "failed"
    assert timed_out.layers[1].status.value == "timed_out"
    assert timeout_executor.calls == 0


@pytest.mark.asyncio
async def test_bound_evidence_rejects_every_stale_identity() -> None:
    plan = _production_plan()
    context = _context(policy=PRODUCTION_POLICY)
    executor = StubVerificationExecutor(_observation())
    evidence = await VerificationSuiteRunner(executor=executor).run(
        plan=plan,
        policy=PRODUCTION_POLICY,
        structural_evidence=_structural(),
        context=context,
        draft_id="draft-1",
        draft_revision=3,
    )
    assert not validate_bound_evidence(
        evidence,
        plan=plan,
        policy=PRODUCTION_POLICY,
        context=context,
        draft_id="draft-1",
        draft_revision=4,
        executor=executor,
    )
    drifted_executor = StubVerificationExecutor(_observation())
    drifted_executor.capability_fingerprint = "sha256:" + "b" * 64
    assert not validate_bound_evidence(
        evidence,
        plan=plan,
        policy=PRODUCTION_POLICY,
        context=context,
        draft_id="draft-1",
        draft_revision=3,
        executor=drifted_executor,
    )