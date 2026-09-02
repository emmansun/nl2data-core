"""Admin contract and security tests for semantic assembly lint operations."""

from __future__ import annotations

import pytest
from nl2data_core.assembly import LifecycleRole
from pydantic import ValidationError as PydanticValidationError

from helpers import _FakeDependencies, _make_auth
from nl2data_admin_service.auth import Permission
from nl2data_admin_service.config import AdminServiceConfig
from nl2data_admin_service.dtos import (
    AdminLintDiagnostic,
    ImportAuthoringCommand,
    LintAuthoringCommand,
    LintDraftCommand,
    LintResultDetail,
)
from nl2data_admin_service.errors import (
    AuthorizationDeniedError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from nl2data_admin_service.schema import build_schema
from nl2data_admin_service.service import AdminService

_TENANT = "sha256:" + "0" * 64


def document(*, source_id: str = "source-1", extra: str = "") -> str:
    return f"""apiVersion: nl2data.io/semantic-assembly-authoring/v1alpha1
kind: SemanticAssembly
metadata:
  bundleId: sales
  modelVersion: v1
spec:
  source:
    sourceId: {source_id}
  entities:
    - entityId: orders
      label: Orders
    - entityId: customers
      label: Orders
{extra}"""


def pii_document() -> str:
    return document(
        extra=(
            "    - entityId: contacts\n"
            "      label: Contacts\n"
            "      fields:\n"
            "        - fieldId: email\n"
            "          label: Email\n"
            "          description: Customer contact email address.\n"
            "          dataType: string\n"
            "          allowedAggregations: []\n"
            "          valueSemantics:\n"
            "            value_mapping:\n"
            "              jane@example.com: customer email\n"
            "            pii: true\n"
            "            sample_values: [\"jane@example.com\"]\n"
        )
    )


def validation_auth():
    return _make_auth([Permission.BUNDLE_VALIDATE])


def import_auth():
    return _make_auth(
        [Permission.ASSEMBLY_READ, Permission.ASSEMBLY_WRITE],
        lifecycle_roles=frozenset({LifecycleRole.AUTHOR}),
    )


def _imported_draft(service: AdminService) -> None:
    result = service.import_authoring(
        ImportAuthoringCommand(document=document(), draft_id="draft-1"),
        auth_context=import_auth(),
    )
    assert result.imported


class TestAuthoringLintContract:
    def test_production_lint_blocks_duplicate_labels(self) -> None:
        service = AdminService(_FakeDependencies(), AdminServiceConfig())
        result = service.lint_authoring(
            LintAuthoringCommand(document=document(), profile="production"),
            auth_context=validation_auth(),
        )
        assert result.profile == "production"
        assert result.blocking is True and result.has_errors is True
        codes = {diagnostic.code for diagnostic in result.diagnostics}
        assert "SAL001" in codes
        assert result.diagnostic_count == len(result.diagnostics)
        assert (
            result.diagnostic_count
            == result.error_count + result.warning_count + result.info_count
        )
        for diagnostic in result.diagnostics:
            assert diagnostic.severity in {"error", "warning", "info"}
            assert len(diagnostic.message) <= 256
            assert diagnostic.path == "$" or diagnostic.path.startswith("$.")

    def test_recommended_lint_is_advisory(self) -> None:
        service = AdminService(_FakeDependencies(), AdminServiceConfig())
        result = service.lint_authoring(
            LintAuthoringCommand(document=document()),
            auth_context=validation_auth(),
        )
        assert result.profile == "recommended"
        assert result.blocking is False and result.has_errors is False
        assert "SAL001" in {diagnostic.code for diagnostic in result.diagnostics}

    def test_authoring_lint_has_no_persistence_side_effects(self) -> None:
        dependencies = _FakeDependencies()
        service = AdminService(dependencies, AdminServiceConfig())
        result = service.lint_authoring(
            LintAuthoringCommand(document=document(), profile="production"),
            auth_context=validation_auth(),
        )
        assert result.diagnostic_count > 0
        assert (
            dependencies.draft_store.get(
                "draft-1", tenant_scope_fingerprint=_TENANT
            )
            is None
        )

    def test_pii_sample_values_never_leak_into_responses(self) -> None:
        service = AdminService(_FakeDependencies(), AdminServiceConfig())
        result = service.lint_authoring(
            LintAuthoringCommand(document=pii_document(), profile="production"),
            auth_context=validation_auth(),
        )
        assert "SAL005" in {diagnostic.code for diagnostic in result.diagnostics}
        assert "jane@example.com" not in result.model_dump_json()

    def test_invalid_or_oversized_input_is_rejected_safely(self) -> None:
        service = AdminService(_FakeDependencies(), AdminServiceConfig())
        with pytest.raises(ValidationError) as excinfo:
            service.lint_authoring(
                LintAuthoringCommand(document="::: not yaml ["),
                auth_context=validation_auth(),
            )
        assert "jane" not in str(excinfo.value)
        with pytest.raises((ValidationError, PydanticValidationError)):
            service.lint_authoring(
                LintAuthoringCommand(document="x" * (2_000_000)),
                auth_context=validation_auth(),
            )


class TestAuthoringLintSecurity:
    def test_missing_permission_is_denied(self) -> None:
        service = AdminService(_FakeDependencies(), AdminServiceConfig())
        with pytest.raises(AuthorizationDeniedError):
            service.lint_authoring(
                LintAuthoringCommand(document=document()),
                auth_context=_make_auth([]),
            )

    def test_cross_source_scope_is_denied(self) -> None:
        service = AdminService(_FakeDependencies(), AdminServiceConfig())
        with pytest.raises(AuthorizationDeniedError):
            service.lint_authoring(
                LintAuthoringCommand(document=document(source_id="source-2")),
                auth_context=validation_auth(),
            )


class TestDraftLintContract:
    def test_draft_lint_is_revision_guarded_and_side_effect_free(self) -> None:
        dependencies = _FakeDependencies()
        service = AdminService(dependencies, AdminServiceConfig())
        _imported_draft(service)
        result = service.lint_draft(
            "draft-1",
            LintDraftCommand(expected_revision=0),
            auth_context=import_auth(),
        )
        assert result.profile == "recommended" and result.blocking is False
        assert "SAL001" in {diagnostic.code for diagnostic in result.diagnostics}
        stored = dependencies.draft_store.get(
            "draft-1", tenant_scope_fingerprint=_TENANT
        )
        assert stored is not None
        assert stored.draft_revision == 0
        assert all(
            assertion.review_state.value == "pending"
            and assertion.review_binding is None
            for assertion in stored.assertions
        )

    def test_stale_draft_lint_returns_safe_conflict(self) -> None:
        service = AdminService(_FakeDependencies(), AdminServiceConfig())
        _imported_draft(service)
        with pytest.raises(ConflictError):
            service.lint_draft(
                "draft-1",
                LintDraftCommand(expected_revision=1),
                auth_context=import_auth(),
            )

    def test_draft_lint_requires_read_permission(self) -> None:
        service = AdminService(_FakeDependencies(), AdminServiceConfig())
        _imported_draft(service)
        with pytest.raises(AuthorizationDeniedError):
            service.lint_draft(
                "draft-1",
                LintDraftCommand(expected_revision=0),
                auth_context=_make_auth([]),
            )

    def test_draft_lint_is_tenant_isolated(self) -> None:
        service = AdminService(_FakeDependencies(), AdminServiceConfig())
        _imported_draft(service)
        other_tenant = import_auth().model_copy(
            update={"tenant_scope_fingerprint": "sha256:" + "9" * 64}
        )
        with pytest.raises(NotFoundError):
            service.lint_draft(
                "draft-1",
                LintDraftCommand(expected_revision=0),
                auth_context=other_tenant,
            )


class TestLintSchemaAndCapabilities:
    def test_schema_advertises_lint_commands_and_results(self) -> None:
        schema = build_schema(AdminServiceConfig().contract_version)
        assert schema.commands["LintAuthoringCommand"] is LintAuthoringCommand
        assert schema.commands["LintDraftCommand"] is LintDraftCommand
        assert schema.results["AdminLintDiagnostic"] is AdminLintDiagnostic
        assert schema.results["LintResultDetail"] is LintResultDetail

    def test_capabilities_advertise_lint_operations(self) -> None:
        service = AdminService(_FakeDependencies(), AdminServiceConfig())
        capabilities = {
            capability.name: capability
            for capability in service.capabilities().capabilities
        }
        assert capabilities["authoring lint"].permission is Permission.BUNDLE_VALIDATE
        assert capabilities["authoring lint"].maximum_input_size is not None
        assert capabilities["assembly lint"].permission is Permission.ASSEMBLY_READ


class TestLintDtoBounds:
    def test_non_catalog_codes_are_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            AdminLintDiagnostic(code="XXX001", severity="error", path="$", message="m")

    def test_unbounded_messages_are_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            AdminLintDiagnostic(
                code="SAL001",
                severity="error",
                path="$",
                message="x" * 257,
            )

    def test_result_round_trips_through_json(self) -> None:
        result = LintResultDetail(
            profile="production",
            profile_version=1,
            diagnostics=(
                AdminLintDiagnostic(
                    code="SAL001",
                    severity="error",
                    path="$.spec.entities.orders",
                    line=3,
                    column=5,
                    message="Duplicate business label 'Orders' is shared by 2 members.",
                ),
            ),
            diagnostic_count=1,
            error_count=1,
            has_errors=True,
            blocking=True,
        )
        assert LintResultDetail.model_validate(result.model_dump(mode="json")) == result
