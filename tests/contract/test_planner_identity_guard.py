"""Contract tests for the planner-identity drift guard (design D5).

The pre-execution boundary rejects any planner-identity mismatch and
any one-sided identity - context without evidence or evidence without
context - because a one-sided identity cannot be drift-checked.  The
identity-versioning strictness clause (missing evidence identity
rejected outright) stays behind the ``identity_versioning`` switch
(default: ``PLANNER_IDENTITY_VERSIONING``) so both-unset legacy paths
keep verifying unchanged.
"""

from __future__ import annotations

from tests.contract.test_compiler_governance_boundaries import (
    issue_authorization,
    sql_chain,
)

from nl2data_core.compilation.contract import (
    PLANNER_IDENTITY_VERSIONING,
    verify_pre_execution_guard,
)

PLANNER = "deterministic-join-planner"
OTHER_PLANNER = "legacy-planner"


def verify(context, evidence, guard, *, identity_versioning=None):
    return verify_pre_execution_guard(
        context=context,
        evidence=evidence,
        guard=guard,
        authorization=issue_authorization(context, evidence, guard),
        identity_versioning=identity_versioning,
    )


class TestPlannerIdentityDrift:
    def test_identity_mismatch_is_rejected(self) -> None:
        context, evidence, guard, _ = sql_chain()
        context = context.model_copy(update={"planner_identity": PLANNER})
        evidence = evidence.model_copy(update={"planner_identity": OTHER_PLANNER})
        reasons = verify(context, evidence, guard)
        assert any(
            "planner identity does not match" in reason for reason in reasons
        )

    def test_context_without_evidence_identity_is_rejected(self) -> None:
        context, evidence, guard, _ = sql_chain()
        context = context.model_copy(update={"planner_identity": PLANNER})
        reasons = verify(context, evidence, guard)
        assert any(
            "evidence lacks the planner identity" in reason for reason in reasons
        )

    def test_evidence_without_context_identity_is_rejected(self) -> None:
        context, evidence, guard, _ = sql_chain()
        evidence = evidence.model_copy(update={"planner_identity": PLANNER})
        reasons = verify(context, evidence, guard)
        assert any(
            "planner identity the compilation context does not declare" in reason
            for reason in reasons
        )

    def test_matching_identities_verify(self) -> None:
        context, evidence, guard, _ = sql_chain()
        context = context.model_copy(update={"planner_identity": PLANNER})
        evidence = evidence.model_copy(update={"planner_identity": PLANNER})
        assert verify(context, evidence, guard) == ()

    def test_drift_reasons_are_human_safe(self) -> None:
        context, evidence, guard, _ = sql_chain()
        context = context.model_copy(update={"planner_identity": PLANNER})
        evidence = evidence.model_copy(update={"planner_identity": OTHER_PLANNER})
        for reason in verify(context, evidence, guard):
            assert isinstance(reason, str)
            assert "sha256:" not in reason
            assert "\n" not in reason


class TestLegacyBothUnset:
    def test_versioning_flag_defaults_to_inactive(self) -> None:
        assert PLANNER_IDENTITY_VERSIONING is False

    def test_both_unset_verifies_unchanged(self) -> None:
        context, evidence, guard, _ = sql_chain()
        assert context.planner_identity is None
        assert evidence.planner_identity is None
        assert verify(context, evidence, guard) == ()

    def test_both_unset_with_versioning_inactive_verifies(self) -> None:
        context, evidence, guard, _ = sql_chain()
        assert verify(context, evidence, guard, identity_versioning=False) == ()


class TestIdentityVersioningStrictness:
    def test_active_versioning_rejects_missing_evidence_identity(self) -> None:
        context, evidence, guard, _ = sql_chain()
        context = context.model_copy(update={"planner_identity": PLANNER})
        reasons = verify(context, evidence, guard, identity_versioning=True)
        assert any(
            "planner identity versioning is active" in reason
            for reason in reasons
        )

    def test_active_versioning_accepts_a_matching_identity(self) -> None:
        context, evidence, guard, _ = sql_chain()
        context = context.model_copy(update={"planner_identity": PLANNER})
        evidence = evidence.model_copy(update={"planner_identity": PLANNER})
        assert verify(context, evidence, guard, identity_versioning=True) == ()

    def test_inactive_versioning_keeps_the_symmetric_guard_only(self) -> None:
        context, evidence, guard, _ = sql_chain()
        context = context.model_copy(update={"planner_identity": PLANNER})
        reasons = verify(context, evidence, guard, identity_versioning=False)
        assert any(
            "evidence lacks the planner identity" in reason for reason in reasons
        )
        assert not any(
            "versioning is active" in reason for reason in reasons
        )
