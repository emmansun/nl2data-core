"""Verification-plan binding across assembly draft lifecycle transitions."""

from __future__ import annotations

import pytest

from nl2data_core.assembly import ASSEMBLY_API_VERSION, AssemblyDraft, AssemblyState
from nl2data_core.bundles import SemanticModelBundle
from nl2data_core.planning.ir.fixtures import golden_ir
from nl2data_core.verification import (
    CapabilityRequirements,
    OutcomeAssertion,
    RowCountEqualityContract,
    SemanticContractCase,
    SmokeQueryCase,
    VerificationDeadlines,
    VerificationPlan,
)


def _draft(*, plan: VerificationPlan | None = None) -> AssemblyDraft:
    return AssemblyDraft(
        apiVersion=ASSEMBLY_API_VERSION,
        draft_id="draft-plan",
        bundle_id="sales",
        source_id="sales",
        model_version="1.0.0",
        author_reference="author-1",
        verification_plan=plan,
    )


def _smoke(
    *, enabled: bool = True, profile: str = "sqlite-v1", capability: str = "aggregation"
) -> SmokeQueryCase:
    return SmokeQueryCase(
        case_id="smoke-1",
        enabled=enabled,
        query=golden_ir(),
        fixture_profile_id=profile,
        capability_requirements=CapabilityRequirements(capabilities=(capability,)),
        assertions=(OutcomeAssertion(assertion_id="outcome", expected="success"),),
    )


def _semantic() -> SemanticContractCase:
    return SemanticContractCase(
        case_id="semantic-1",
        query=golden_ir(),
        fixture_profile_id="sqlite-v1",
        contracts=(RowCountEqualityContract(assertion_id="rows", expected=1),),
    )


def _plan(**overrides: object) -> VerificationPlan:
    values: dict[str, object] = {
        "policy_profile": "production-v1",
        "smoke_cases": (_smoke(),),
        "semantic_cases": (_semantic(),),
    }
    values.update(overrides)
    return VerificationPlan(**values)  # type: ignore[arg-type]


def _approved(plan: VerificationPlan) -> AssemblyDraft:
    review = _draft(plan=plan).transition(expected_revision=0, state=AssemblyState.REVIEW)
    return review.transition(expected_revision=1, state=AssemblyState.APPROVED)


def test_plan_is_omitted_when_unset_and_bundle_schema_has_no_plan_fields() -> None:
    payload = _draft().file_payload()
    assert "verification_plan" not in payload
    assert "approved_verification_plan_fingerprint" not in payload
    assert "verification_plan" not in SemanticModelBundle.model_fields


def test_approval_captures_exact_plan_fingerprint_and_round_trips() -> None:
    approved = _approved(_plan())
    assert approved.approved_verification_plan_fingerprint == approved.verification_plan.fingerprint
    assert AssemblyDraft.model_validate(approved.file_payload()) == approved


@pytest.mark.parametrize(
    "changed_plan",
    [
        None,
        _plan(policy_profile="host-production-v1"),
        _plan(deadlines=VerificationDeadlines(case_ms=29_000)),
        _plan(smoke_cases=(_smoke(enabled=False),)),
        _plan(smoke_cases=(_smoke(profile="postgres-v1"),)),
        _plan(smoke_cases=(_smoke(capability="grouping"),)),
        _plan(smoke_cases=()),
        _plan(semantic_cases=()),
    ],
)
def test_every_actual_plan_change_reopens_approval(
    changed_plan: VerificationPlan | None,
) -> None:
    approved = _approved(_plan())
    changed = approved.mutate(
        expected_revision=approved.draft_revision,
        verification_plan=changed_plan,
    )
    assert changed.state is AssemblyState.REVIEW
    assert changed.draft_revision == approved.draft_revision + 1
    assert changed.approved_by is None
    assert changed.approved_verification_plan_fingerprint is None


def test_plan_addition_advances_revision_and_identical_plan_is_a_noop() -> None:
    original = _draft()
    plan = _plan()
    changed = original.mutate(expected_revision=0, verification_plan=plan)
    unchanged = changed.mutate(expected_revision=1, verification_plan=_plan())
    assert changed.draft_revision == 1
    assert unchanged is changed


def test_approved_semantic_content_remains_frozen() -> None:
    approved = _approved(_plan())
    with pytest.raises(ValueError, match="frozen"):
        approved.mutate(expected_revision=2, model_version="2.0.0")