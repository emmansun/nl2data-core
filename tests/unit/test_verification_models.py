"""Verification plan, evidence, and policy contract tests."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from nl2data_core.bundles import PublishVerificationSummary
from nl2data_core.planning.ir.fixtures import golden_ir
from nl2data_core.verification import (
    COMPATIBILITY_POLICY,
    PRODUCTION_POLICY,
    CapabilityRequirements,
    OutcomeAssertion,
    RowCountEqualityContract,
    ScalarEqualsAssertion,
    SemanticContractCase,
    SmokeQueryCase,
    TaggedExpectedScalar,
    VerificationCaseEvidence,
    VerificationDeadlines,
    VerificationLayerEvidence,
    VerificationPlan,
    VerificationPolicy,
    VerificationSuiteEvidence,
    validate_stricter_policy,
)

_FINGERPRINT = "sha256:" + "1" * 64


def _smoke_case(case_id: str) -> SmokeQueryCase:
    return SmokeQueryCase(
        case_id=case_id,
        query=golden_ir(),
        fixture_profile_id="sqlite-v1",
        assertions=(OutcomeAssertion(assertion_id="outcome", expected="success"),),
    )


def _semantic_case(case_id: str) -> SemanticContractCase:
    return SemanticContractCase(
        case_id=case_id,
        query=golden_ir(),
        fixture_profile_id="sqlite-v1",
        contracts=(RowCountEqualityContract(assertion_id="rows", expected=1),),
    )


@pytest.mark.parametrize(
    ("kind", "value"),
    [("null", None), ("bool", True), ("int", 1), ("decimal", "1.25"), ("str", "north")],
)
def test_tagged_expected_scalars_preserve_wire_types(kind: str, value: object) -> None:
    assert TaggedExpectedScalar(kind=kind, value=value).canonical_payload()["value"] == value


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("int", True),
        ("bool", 1),
        ("decimal", 1),
        ("decimal", "1.0"),
        ("int", 1.0),
        ("str", object()),
        ("str", "password=secret"),
    ],
)
def test_tagged_expected_scalars_reject_native_or_unsafe_values(
    kind: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        TaggedExpectedScalar(kind=kind, value=value)


def test_deadline_and_capability_bounds_are_fail_closed() -> None:
    with pytest.raises(ValidationError):
        VerificationDeadlines(case_ms=10, layer_ms=5, suite_ms=20)
    with pytest.raises(ValidationError):
        VerificationDeadlines(case_ms=300_001)
    with pytest.raises(ValidationError):
        CapabilityRequirements(capabilities=("duplicate", "duplicate"))
    with pytest.raises(ValidationError):
        CapabilityRequirements(capabilities=("raw sql",))


def test_cases_reject_duplicate_assertions_and_unknown_operators() -> None:
    duplicate = OutcomeAssertion(assertion_id="same", expected="success")
    with pytest.raises(ValidationError):
        SmokeQueryCase(
            case_id="smoke",
            query=golden_ir(),
            fixture_profile_id="sqlite-v1",
            assertions=(duplicate, duplicate),
        )
    with pytest.raises(ValidationError):
        SmokeQueryCase.model_validate(
            {
                **_smoke_case("smoke").model_dump(),
                "assertions": ({"assertion_id": "a", "kind": "python", "code": "pass"},),
            }
        )


def test_plan_rejects_duplicate_ids_and_unsafe_descriptions() -> None:
    with pytest.raises(ValidationError):
        VerificationPlan(
            policy_profile="production-v1",
            smoke_cases=(_smoke_case("same"),),
            semantic_cases=(_semantic_case("same"),),
        )
    with pytest.raises(ValidationError):
        SmokeQueryCase(
            case_id="unsafe",
            description="SELECT password FROM credentials",
            query=golden_ir(),
            fixture_profile_id="sqlite-v1",
            assertions=(OutcomeAssertion(assertion_id="outcome", expected="success"),),
        )


def test_plan_canonical_identity_is_case_order_independent_and_json_safe() -> None:
    first = VerificationPlan(
        policy_profile="production-v1",
        smoke_cases=(_smoke_case("b"), _smoke_case("a")),
        semantic_cases=(_semantic_case("d"), _semantic_case("c")),
    )
    second = VerificationPlan(
        policy_profile="production-v1",
        smoke_cases=tuple(reversed(first.smoke_cases)),
        semantic_cases=tuple(reversed(first.semantic_cases)),
    )
    assert first.serialize_canonical() == second.serialize_canonical()
    assert first.fingerprint == second.fingerprint
    assert json.loads(first.serialize_canonical())["smoke_cases"][0]["case_id"] == "a"


def test_policy_profiles_are_immutable_bounded_and_cannot_be_weakened() -> None:
    assert COMPATIBILITY_POLICY.required_layers == {"layer_1"}
    assert COMPATIBILITY_POLICY.compatibility_label == "structural_only"
    assert PRODUCTION_POLICY.required_layers == {"layer_1", "layer_2", "layer_3"}
    assert PRODUCTION_POLICY.minimum_enabled_smoke_cases == 1
    assert PRODUCTION_POLICY.minimum_enabled_semantic_cases == 1
    with pytest.raises(ValidationError):
        VerificationPolicy(
            policy_id="production-v1",
            required_layers={"layer_1"},
        )
    weaker_host = VerificationPolicy(policy_id="host-v1", required_layers={"layer_1"})
    with pytest.raises(ValueError, match="required layers"):
        validate_stricter_policy(weaker_host)


def test_evidence_identity_excludes_durations_and_omits_absent_references() -> None:
    case = VerificationCaseEvidence(
        case_id="structural",
        layer="layer_1",
        status="passed",
        duration_ms=1,
    )
    slower_case = VerificationCaseEvidence.model_validate(
        {**case.model_dump(), "duration_ms": 200}
    )
    assert case.fingerprint == slower_case.fingerprint
    layer = VerificationLayerEvidence(
        layer="layer_1", status="passed", cases=(case,), duration_ms=3
    )
    suite = VerificationSuiteEvidence(
        status="passed",
        policy_profile="compatibility-v1",
        policy_version=1,
        policy_fingerprint=COMPATIBILITY_POLICY.fingerprint,
        runner_id="core-verifier",
        runner_version=1,
        draft_id="draft-1",
        draft_revision=1,
        bundle_fingerprint=_FINGERPRINT,
        manifest_fingerprint=_FINGERPRINT,
        tenant_scope_fingerprint=_FINGERPRINT,
        source_scope_fingerprint=_FINGERPRINT,
        layers=(layer,),
        duration_ms=4,
    )
    evidence_json = json.dumps(suite.evidence_payload(), sort_keys=True)
    assert "duration" not in evidence_json
    assert "plan_fingerprint" not in evidence_json
    assert "executor_id" not in evidence_json
    assert "query" not in evidence_json
    assert "value" not in evidence_json
    assert "fixture" not in evidence_json


def test_scalar_assertion_is_frozen() -> None:
    assertion = ScalarEqualsAssertion(
        assertion_id="scalar",
        selection_id="amount",
        expected=TaggedExpectedScalar(kind="int", value=1),
    )
    with pytest.raises(ValidationError):
        assertion.row_index = 1  # type: ignore[misc]


def test_publish_verification_summary_retains_bounded_legacy_decoding() -> None:
    legacy = PublishVerificationSummary.model_validate(
        {
            "structural_valid": True,
            "manifest_equivalent": True,
            "host_callback_count": 1,
        }
    )
    assert legacy.suite_version is None
    assert legacy.evidence_fingerprint is None
    with pytest.raises(ValidationError, match="identities must be complete"):
        PublishVerificationSummary(
            structural_valid=True,
            manifest_equivalent=True,
            suite_version=1,
        )