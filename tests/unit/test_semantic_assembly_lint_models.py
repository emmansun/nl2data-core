"""Unit tests for semantic assembly lint diagnostic models and helpers."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nl2data_core.assembly.lint import (
    LINT_PROFILES,
    LintDiagnostic,
    LintProfileId,
    LintReference,
    LintResult,
    LintResultSummary,
    LintSeverity,
    LintSourceLocation,
    LintTargetPath,
    bounded_message,
    lint_rule_codes,
    safe_scalar_summary,
)


class TestDiagnosticContracts:
    def test_diagnostic_round_trips_through_json(self) -> None:
        diagnostic = LintDiagnostic(
            code="SAL001",
            severity=LintSeverity.WARNING,
            profile=LintProfileId.RECOMMENDED,
            target_path=LintTargetPath(parts=("spec", "entities", 0)),
            source_location=LintSourceLocation(line=3, column=5),
            message="Duplicate business label 'Orders' is shared by 2 semantic members.",
            references=(LintReference(kind="member", value="$.spec.entities[1]"),),
        )
        payload = diagnostic.model_dump(mode="json")
        assert LintDiagnostic.model_validate(payload) == diagnostic

    def test_target_path_renders_stable_notation(self) -> None:
        path = LintTargetPath(parts=("spec", "entities", 2, "fields", 7))
        assert path.render() == "$.spec.entities[2].fields[7]"
        assert LintTargetPath().render() == "$"

    @pytest.mark.parametrize("code", ["SAL1", "SAL0001", "sal001", "XXX001", ""])
    def test_non_catalog_codes_are_rejected(self, code: str) -> None:
        with pytest.raises(ValidationError):
            LintDiagnostic(
                code=code,
                severity=LintSeverity.INFO,
                profile=LintProfileId.COMPATIBILITY,
                message="message",
            )

    def test_unbounded_message_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LintDiagnostic(
                code="SAL001",
                severity=LintSeverity.INFO,
                profile=LintProfileId.COMPATIBILITY,
                message="x" * 257,
            )

    def test_reference_bounds_are_enforced(self) -> None:
        with pytest.raises(ValidationError):
            LintReference(kind="member", value="")

    def test_frozen_models_reject_mutation(self) -> None:
        diagnostic = LintDiagnostic(
            code="SAL001",
            severity=LintSeverity.INFO,
            profile=LintProfileId.COMPATIBILITY,
            message="message",
        )
        with pytest.raises((TypeError, ValidationError)):
            diagnostic.message = "changed"  # type: ignore[misc]


class TestResultSummary:
    def test_summary_defaults_are_safe(self) -> None:
        summary = LintResultSummary(profile=LintProfileId.RECOMMENDED, profile_version=1)
        assert summary.diagnostic_count == 0
        assert summary.has_errors is False
        assert summary.blocking is False
        assert summary.truncated is False

    def test_result_round_trips_through_json(self) -> None:
        result = LintResult(
            summary=LintResultSummary(
                profile=LintProfileId.PRODUCTION,
                profile_version=1,
                diagnostic_count=1,
                error_count=1,
                has_errors=True,
                blocking=True,
            ),
            diagnostics=(
                LintDiagnostic(
                    code="SAL004",
                    severity=LintSeverity.ERROR,
                    profile=LintProfileId.PRODUCTION,
                    message="PII-classified field 'status' has no handling description.",
                ),
            ),
        )
        assert LintResult.model_validate(result.model_dump(mode="json")) == result


class TestProfiles:
    def test_builtin_catalog_is_stable(self) -> None:
        assert lint_rule_codes() == (
            "SAL001",
            "SAL002",
            "SAL003",
            "SAL004",
            "SAL005",
            "SAL006",
            "SAL007",
            "SAL008",
            "SAL009",
            "SAL010",
            "SAL011",
        )

    def test_every_profile_defines_every_rule(self) -> None:
        for code in lint_rule_codes():
            for profile_id in LintProfileId:
                severity = LINT_PROFILES[profile_id].severity_for(code)
                assert severity is None or isinstance(severity, LintSeverity)

    def test_only_production_profile_blocks_governance_rules(self) -> None:
        for code in ("SAL004", "SAL005", "SAL006", "SAL007", "SAL010"):
            assert LINT_PROFILES[LintProfileId.RECOMMENDED].severity_for(code) != LintSeverity.ERROR
            assert LINT_PROFILES[LintProfileId.PRODUCTION].severity_for(code) == LintSeverity.ERROR

    def test_recommended_profile_is_never_blocking(self) -> None:
        for code in lint_rule_codes():
            severity = LINT_PROFILES[LintProfileId.RECOMMENDED].severity_for(code)
            assert severity is None or severity is not LintSeverity.ERROR

    def test_profiles_are_versioned(self) -> None:
        for profile in LINT_PROFILES.values():
            assert profile.version == 1


class TestSafeMessages:
    def test_long_scalars_are_truncated(self) -> None:
        summary = safe_scalar_summary("x" * 500)
        assert len(summary) <= 48
        assert summary.endswith("…")

    def test_internal_whitespace_is_collapsed(self) -> None:
        assert safe_scalar_summary("a\n  b\tc") == "a b c"

    @pytest.mark.parametrize(
        "value",
        [
            "password=hunter2",
            "my-api-key: abc",
            "bearer token=xyz",
            "AWS_SECRET_ACCESS_KEY",
        ],
    )
    def test_secret_like_scalars_are_redacted(self, value: str) -> None:
        assert safe_scalar_summary(value) == "[redacted]"

    def test_message_bound_is_enforced(self) -> None:
        assert len(bounded_message("x" * 5_000)) <= 256
        assert bounded_message("short") == "short"
