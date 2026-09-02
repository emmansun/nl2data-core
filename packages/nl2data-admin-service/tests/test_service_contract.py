"""Service-layer contract tests for the admin service."""

from __future__ import annotations

import pytest
from nl2data_core.assembly import LifecycleRole

from helpers import (
    _FakeDependencies,
    _FakeDiscoverer,
    _make_auth,
    _make_bundle,
    _make_proposal_set,
    _make_snapshot,
)
from nl2data_admin_service.auth import Permission
from nl2data_admin_service.config import AdminServiceConfig
from nl2data_admin_service.dtos import (
    BundleLifecycleCommand,
    JobStatus,
    LifecycleCommand,
    ReviewAction,
    ReviewCommand,
)
from nl2data_admin_service.errors import (
    AuthorizationDeniedError,
)
from nl2data_admin_service.errors import ValidationError as AdminValidationError
from nl2data_admin_service.protocols import (
    ActivationLifecyclePort,
    AdminServiceDependencies,
    MetadataCatalogPort,
    PublicationStoragePort,
    PublishedLookupPort,
)
from nl2data_admin_service.service import AdminService


def test_admin_dependency_fakes_satisfy_runtime_ports() -> None:
    deps = _FakeDependencies()
    assert isinstance(deps, AdminServiceDependencies)
    assert isinstance(deps.catalog, MetadataCatalogPort)
    assert isinstance(deps.lifecycle_catalog, PublicationStoragePort)
    assert isinstance(deps.lifecycle_catalog, PublishedLookupPort)
    assert isinstance(deps.lifecycle_catalog, ActivationLifecyclePort)


def test_unauthenticated_request_fails_closed() -> None:
    service = AdminService(_FakeDependencies(), AdminServiceConfig())
    with pytest.raises(AuthorizationDeniedError):
        service.get_snapshot("sha256:" + "0" * 64, auth_context=_make_auth([]))


def test_snapshot_read_denied_without_permission() -> None:
    service = AdminService(_FakeDependencies(), AdminServiceConfig())
    with pytest.raises(AuthorizationDeniedError):
        service.get_snapshot("sha256:" + "0" * 64, auth_context=_make_auth([]))


def test_snapshot_read_succeeds_with_permission() -> None:
    deps = _FakeDependencies()
    service = AdminService(deps, AdminServiceConfig())
    snapshot = _make_snapshot()
    deps.catalog.register_snapshot(
        snapshot,
        tenant_scope_fingerprint="sha256:" + "0" * 64,
    )
    result = service.get_snapshot(
        snapshot.fingerprint, auth_context=_make_auth([Permission.SNAPSHOT_READ])
    )
    assert result.snapshot_id == snapshot.snapshot_id
    assert result.source_id == "source-1"


def test_proposal_review_requires_review_permission() -> None:
    deps = _FakeDependencies()
    service = AdminService(deps, AdminServiceConfig())
    snapshot = _make_snapshot()
    proposal_set = _make_proposal_set(snapshot)
    deps.catalog.register_snapshot(snapshot, tenant_scope_fingerprint="sha256:" + "0" * 64)
    deps.catalog.save_proposal_set(proposal_set, tenant_scope_fingerprint="sha256:" + "0" * 64)
    with pytest.raises(AuthorizationDeniedError):
        service.review_proposals(
            snapshot.fingerprint,
            ReviewCommand(
                action=ReviewAction.APPROVE,
                proposal_ids=("p-1",),
                expected_set_fingerprint=proposal_set.evidence_fingerprint_of(),
                idempotency_key="k-1",
            ),
            auth_context=_make_auth([Permission.PROPOSAL_READ]),
        )


def test_proposal_review_approves_with_permission() -> None:
    deps = _FakeDependencies()
    service = AdminService(deps, AdminServiceConfig())
    snapshot = _make_snapshot()
    proposal_set = _make_proposal_set(snapshot)
    deps.catalog.register_snapshot(snapshot, tenant_scope_fingerprint="sha256:" + "0" * 64)
    deps.catalog.save_proposal_set(proposal_set, tenant_scope_fingerprint="sha256:" + "0" * 64)
    result = service.review_proposals(
        snapshot.fingerprint,
        ReviewCommand(
            action=ReviewAction.APPROVE,
            proposal_ids=("p-1",),
            expected_set_fingerprint=proposal_set.evidence_fingerprint_of(),
            idempotency_key="k-1",
        ),
        auth_context=_make_auth([Permission.PROPOSAL_REVIEW]),
    )
    assert result.action == ReviewAction.APPROVE
    assert result.audit_reference == "audit-1"


def test_publish_bundle_cannot_bypass_assembly_authority() -> None:
    deps = _FakeDependencies()
    service = AdminService(deps, AdminServiceConfig())
    bundle = _make_bundle()
    with pytest.raises(AdminValidationError, match="approved assembly draft"):
        service.publish_bundle(
            bundle,
            auth_context=_make_auth([Permission.BUNDLE_PUBLISH]),
            idempotency_key="k-1",
        )


def test_publish_bundle_denied_without_permission() -> None:
    deps = _FakeDependencies()
    service = AdminService(deps, AdminServiceConfig())
    with pytest.raises(AuthorizationDeniedError):
        service.publish_bundle(
            _make_bundle(),
            auth_context=_make_auth([]),
            idempotency_key="k-1",
        )


def test_validate_bundle_returns_validation_result() -> None:
    deps = _FakeDependencies()
    service = AdminService(deps, AdminServiceConfig())
    result = service.validate_bundle(
        _make_bundle(),
        auth_context=_make_auth([Permission.BUNDLE_VALIDATE]),
    )
    assert result.valid is True
    assert result.issues == ()


def test_activate_bundle_with_permission() -> None:
    deps = _FakeDependencies()
    service = AdminService(deps, AdminServiceConfig())
    bundle = _make_bundle()
    deps.lifecycle_catalog.publish(
        bundle,
        tenant_scope_fingerprint="sha256:" + "0" * 64,
    )
    result = service.lifecycle_command(
        BundleLifecycleCommand(
            command=LifecycleCommand.ACTIVATE,
            bundle_id="bundle-1",
            expected_fingerprint=bundle.fingerprint,
            idempotency_key="k-1",
        ),
        auth_context=_make_auth(
            [Permission.BUNDLE_ACTIVATE],
            lifecycle_roles=frozenset({LifecycleRole.PUBLISHER}),
        ),
    )
    assert result.success is True
    assert result.fingerprint == bundle.fingerprint


def test_sync_discovery_registers_snapshot() -> None:
    deps = _FakeDependencies()
    service = AdminService(deps, AdminServiceConfig())
    snapshot = _make_snapshot()
    deps.discoverer = _FakeDiscoverer(snapshot)
    job = service.submit_discovery(
        "source-1",
        auth_context=_make_auth([Permission.DISCOVERY_RUN]),
        idempotency_key="k-1",
    )
    assert job.status == JobStatus.COMPLETED
    assert job.result_fingerprint == snapshot.fingerprint
    detail = service.get_snapshot(
        snapshot.fingerprint, auth_context=_make_auth([Permission.SNAPSHOT_READ])
    )
    assert detail.snapshot_id == snapshot.snapshot_id
    assert detail.status == "retained"
