"""Admin contract and security tests for semantic authoring operations."""

from __future__ import annotations

import pytest
from nl2data_core.assembly import LifecycleRole

from helpers import _FakeDependencies, _make_auth
from nl2data_admin_service.auth import Permission
from nl2data_admin_service.config import AdminServiceConfig
from nl2data_admin_service.dtos import AuthoringDocumentCommand, ImportAuthoringCommand
from nl2data_admin_service.errors import AuthorizationDeniedError, ConflictError
from nl2data_admin_service.service import AdminService


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
{extra}"""


def validation_auth():
    return _make_auth([Permission.BUNDLE_VALIDATE])


def import_auth():
    return _make_auth(
        [Permission.ASSEMBLY_WRITE],
        lifecycle_roles=frozenset({LifecycleRole.AUTHOR}),
    )


def test_validation_is_side_effect_free_and_returns_safe_summary() -> None:
    dependencies = _FakeDependencies()
    service = AdminService(dependencies, AdminServiceConfig())
    result = service.validate_authoring(
        AuthoringDocumentCommand(document=document()),
        auth_context=validation_auth(),
    )
    assert result.valid and result.summary is not None
    assert result.summary.bundle_id == "sales"
    assert dependencies.draft_store.get(
        "draft-1",
        tenant_scope_fingerprint="sha256:" + "0" * 64,
    ) is None


def test_import_creates_clean_draft_with_trusted_author() -> None:
    dependencies = _FakeDependencies()
    service = AdminService(dependencies, AdminServiceConfig())
    result = service.import_authoring(
        ImportAuthoringCommand(document=document(), draft_id="draft-1"),
        auth_context=import_auth(),
    )
    assert result.imported and result.draft is not None
    assert result.draft.draft_revision == 0
    assert result.draft.pending_count == result.draft.assertion_count
    stored = dependencies.draft_store.get(
        "draft-1",
        tenant_scope_fingerprint="sha256:" + "0" * 64,
    )
    assert stored is not None
    assert stored.author_reference == "audit-1"
    assert stored.review_submitted_by is None and stored.approved_by is None


def test_import_requires_permission_role_and_source_scope() -> None:
    service = AdminService(_FakeDependencies(), AdminServiceConfig())
    with pytest.raises(AuthorizationDeniedError):
        service.import_authoring(
            ImportAuthoringCommand(document=document(), draft_id="draft-1"),
            auth_context=_make_auth([]),
        )
    with pytest.raises(AuthorizationDeniedError):
        service.import_authoring(
            ImportAuthoringCommand(document=document(), draft_id="draft-1"),
            auth_context=_make_auth([Permission.ASSEMBLY_WRITE]),
        )
    with pytest.raises(AuthorizationDeniedError):
        service.import_authoring(
            ImportAuthoringCommand(document=document(source_id="source-2"), draft_id="draft-1"),
            auth_context=import_auth(),
        )


def test_import_is_tenant_isolated_and_duplicate_is_conflict() -> None:
    service = AdminService(_FakeDependencies(), AdminServiceConfig())
    first_auth = import_auth()
    second_auth = first_auth.model_copy(
        update={"tenant_scope_fingerprint": "sha256:" + "9" * 64}
    )
    command = ImportAuthoringCommand(document=document(), draft_id="shared")
    assert service.import_authoring(command, auth_context=first_auth).imported
    assert service.import_authoring(command, auth_context=second_auth).imported
    with pytest.raises(ConflictError):
        service.import_authoring(command, auth_context=first_auth)


def test_invalid_diagnostics_redact_values_and_lifecycle_smuggling() -> None:
    service = AdminService(_FakeDependencies(), AdminServiceConfig())
    secret = "postgres://user:super-secret@host/db"
    unsafe = document(
        extra=(
            "  deploymentBindings:\n"
            "    - bindingId: prod\n"
            "      environment: production\n"
            "      sourceId: source-1\n"
            f"      connectionReference: {secret}\n"
        )
    )
    result = service.validate_authoring(
        AuthoringDocumentCommand(document=unsafe),
        auth_context=validation_auth(),
    )
    assert not result.valid
    assert secret not in result.model_dump_json()

    smuggled = document(extra="draftRevision: 7\napprovedBy: attacker\n")
    imported = service.import_authoring(
        ImportAuthoringCommand(document=smuggled, draft_id="draft-1"),
        auth_context=import_auth(),
    )
    assert not imported.imported and imported.draft is None
    assert imported.diagnostics
    assert imported.issue_count == len(imported.diagnostics)


@pytest.mark.parametrize("operation", ["validate", "import"])
def test_authoring_operations_reject_malformed_unicode(operation: str) -> None:
    service = AdminService(_FakeDependencies(), AdminServiceConfig())
    malformed = document() + "\ud800"
    if operation == "validate":
        result = service.validate_authoring(
            AuthoringDocumentCommand.model_construct(document=malformed),
            auth_context=validation_auth(),
        )
    else:
        result = service.import_authoring(
            ImportAuthoringCommand.model_construct(document=malformed, draft_id="draft-1"),
            auth_context=import_auth(),
        )
    assert result.diagnostics[0].code == "invalid_encoding"


def test_import_enforces_configured_body_size_before_persistence() -> None:
    dependencies = _FakeDependencies()
    service = AdminService(dependencies, AdminServiceConfig(max_body_size_bytes=1_024))
    oversized = document() + "#" + "x" * 1_024
    result = service.import_authoring(
        ImportAuthoringCommand(document=oversized, draft_id="draft-1"),
        auth_context=import_auth(),
    )
    assert not result.imported
    assert result.diagnostics[0].code == "input_too_large"
    assert result.issue_count == 1
    assert dependencies.draft_store.get(
        "draft-1",
        tenant_scope_fingerprint="sha256:" + "0" * 64,
    ) is None


def test_capabilities_advertise_only_validate_and_import_prerequisites() -> None:
    capabilities = AdminService(_FakeDependencies(), AdminServiceConfig()).capabilities()
    by_name = {capability.name: capability for capability in capabilities.capabilities}
    assert by_name["authoring validate"].permission is Permission.BUNDLE_VALIDATE
    assert by_name["authoring import"].permission is Permission.ASSEMBLY_WRITE
    assert by_name["authoring import"].lifecycle_role == LifecycleRole.AUTHOR.value
    assert by_name["authoring import"].supported_api_versions
    assert by_name["authoring import"].maximum_input_size == 1_048_576
    assert not any(
        name in by_name for name in ("authoring publish", "authoring approve", "authoring review")
    )