"""Host-integration tests without requiring a particular transport framework."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from helpers import _FakeDependencies, _make_auth, _make_proposal_set, _make_snapshot
from nl2data_admin_service.auth import AuthContext, Permission
from nl2data_admin_service.config import AdminServiceConfig
from nl2data_admin_service.dtos import ReviewAction, ReviewCommand
from nl2data_admin_service.errors import AuthorizationDeniedError, ConflictError
from nl2data_admin_service.service import AdminService


def test_source_scope_denies_unapproved_source() -> None:
    deps = _FakeDependencies()
    service = AdminService(deps, AdminServiceConfig())
    snapshot = _make_snapshot()
    deps.catalog.register_snapshot(snapshot, tenant_scope_fingerprint="sha256:" + "0" * 64)
    restricted = AuthContext(
        operator_id="op-2",
        tenant_scope_fingerprint="sha256:" + "0" * 64,
        source_ids=frozenset(["source-2"]),
        permissions=frozenset([Permission.SNAPSHOT_READ]),
        audit_reference="audit-2",
    )
    with pytest.raises(AuthorizationDeniedError):
        service.get_snapshot(snapshot.fingerprint, auth_context=restricted)


def test_source_scope_denies_unapproved_discovery() -> None:
    deps = _FakeDependencies()
    service = AdminService(deps, AdminServiceConfig())
    restricted = AuthContext(
        operator_id="op-2",
        tenant_scope_fingerprint="sha256:" + "0" * 64,
        source_ids=frozenset(["source-2"]),
        permissions=frozenset([Permission.DISCOVERY_RUN]),
        audit_reference="audit-2",
    )
    with pytest.raises(AuthorizationDeniedError):
        service.submit_discovery(
            "source-1",
            auth_context=restricted,
            idempotency_key="k-1",
        )


def test_stale_fingerprint_review_is_rejected() -> None:
    deps = _FakeDependencies()
    service = AdminService(deps, AdminServiceConfig())
    snapshot = _make_snapshot()
    proposal_set = _make_proposal_set(snapshot)
    deps.catalog.register_snapshot(snapshot, tenant_scope_fingerprint="sha256:" + "0" * 64)
    deps.catalog.save_proposal_set(proposal_set, tenant_scope_fingerprint="sha256:" + "0" * 64)
    with pytest.raises(ConflictError):
        service.review_proposals(
            snapshot.fingerprint,
            ReviewCommand(
                action=ReviewAction.APPROVE,
                proposal_ids=("p-1",),
                expected_set_fingerprint="sha256:" + "0" * 64,
                idempotency_key="k-1",
            ),
            auth_context=_make_auth([Permission.PROPOSAL_REVIEW]),
        )


def test_idempotency_key_is_required_for_review() -> None:
    deps = _FakeDependencies()
    service = AdminService(deps, AdminServiceConfig())
    snapshot = _make_snapshot()
    proposal_set = _make_proposal_set(snapshot)
    deps.catalog.register_snapshot(snapshot, tenant_scope_fingerprint="sha256:" + "0" * 64)
    deps.catalog.save_proposal_set(proposal_set, tenant_scope_fingerprint="sha256:" + "0" * 64)
    with pytest.raises(ValidationError):
        service.review_proposals(
            snapshot.fingerprint,
            ReviewCommand(
                action=ReviewAction.APPROVE,
                proposal_ids=("p-1",),
                expected_set_fingerprint=proposal_set.evidence_fingerprint_of(),
                idempotency_key="",
            ),
            auth_context=_make_auth([Permission.PROPOSAL_REVIEW]),
        )
