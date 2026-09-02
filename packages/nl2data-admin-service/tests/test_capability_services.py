"""Focused tests for the Admin capability services behind the facade.

Each capability service is constructed directly from ``(dependencies,
config)`` and exercised without the ``AdminService`` facade, proving that a
local capability change does not require edits to unrelated services.
"""

from __future__ import annotations

import pytest
from nl2data_core.assembly import (
    ASSEMBLY_API_VERSION,
    AssemblyDraft,
    AssertionProvenance,
    AssertionType,
    LifecycleRole,
    SemanticAssertion,
)

from helpers import (
    _FakeDependencies,
    _make_auth,
    _make_snapshot,
)
from nl2data_admin_service.assembly_admin import AssemblyLifecycleAdminCapability
from nl2data_admin_service.auth import Permission
from nl2data_admin_service.authoring_admin import AuthoringAdminCapability
from nl2data_admin_service.bundle_admin import PublishedBundleAdminCapability
from nl2data_admin_service.config import AdminServiceConfig
from nl2data_admin_service.errors import DiscoveryError, NotFoundError
from nl2data_admin_service.metadata_admin import MetadataAdminCapability
from nl2data_admin_service.protocols import AuthoringAdminService
from nl2data_admin_service.service import AdminService
from nl2data_admin_service.verification_admin import VerificationPublicationAdminCapability

_CAPABILITIES = (
    MetadataAdminCapability,
    AssemblyLifecycleAdminCapability,
    VerificationPublicationAdminCapability,
    PublishedBundleAdminCapability,
)


def draft() -> AssemblyDraft:
    assertion = SemanticAssertion.create(
        type=AssertionType.ENTITY,
        payload={
            "descriptor_id": "desc-1",
            "entity_id": "orders",
            "label": "Orders",
        },
        provenance=AssertionProvenance(kind="manual"),
    )
    return AssemblyDraft(
        apiVersion=ASSEMBLY_API_VERSION,
        draft_id="draft-1",
        bundle_id="bundle-1",
        source_id="source-1",
        model_version="v1",
        assertions=(assertion,),
        author_reference="audit-1",
    )


def author_auth() -> object:
    return _make_auth(
        [Permission.ASSEMBLY_WRITE, Permission.ASSEMBLY_READ],
        lifecycle_roles=frozenset({LifecycleRole.AUTHOR}),
    )


def test_each_capability_constructs_independently() -> None:
    deps = _FakeDependencies()
    config = AdminServiceConfig()
    for capability_cls in _CAPABILITIES:
        capability = capability_cls(deps, config)
        assert capability is not None


def test_authoring_capability_satisfies_authoring_protocol() -> None:
    deps = _FakeDependencies()
    config = AdminServiceConfig()
    assembly = AssemblyLifecycleAdminCapability(deps, config)
    capability = AuthoringAdminCapability(deps, config, assembly)
    assert isinstance(capability, AuthoringAdminService)


def test_metadata_capability_returns_same_output_as_facade() -> None:
    deps = _FakeDependencies()
    config = AdminServiceConfig()
    snapshot = _make_snapshot()
    deps.catalog.register_snapshot(snapshot, tenant_scope_fingerprint=snapshot.fingerprint)
    auth = _make_auth([Permission.SNAPSHOT_READ])
    direct = MetadataAdminCapability(deps, config).get_snapshot(
        snapshot.fingerprint,
        auth_context=auth,
    )
    via_facade = AdminService(deps, config).get_snapshot(
        snapshot.fingerprint,
        auth_context=auth,
    )
    assert direct.model_dump() == via_facade.model_dump()


def test_assembly_capability_returns_same_output_as_facade() -> None:
    config = AdminServiceConfig()
    auth = author_auth()
    direct = AssemblyLifecycleAdminCapability(_FakeDependencies(), config).create_draft(
        draft(),
        auth_context=auth,
    )
    via_facade = AdminService(_FakeDependencies(), config).create_draft(
        draft(),
        auth_context=auth,
    )
    assert direct.model_dump() == via_facade.model_dump()


def test_metadata_capability_does_not_need_draft_store() -> None:
    deps = _FakeDependencies()
    deps.draft_store = None
    config = AdminServiceConfig()
    snapshot = _make_snapshot()
    deps.catalog.register_snapshot(snapshot, tenant_scope_fingerprint=snapshot.fingerprint)
    result = MetadataAdminCapability(deps, config).get_snapshot(
        snapshot.fingerprint,
        auth_context=_make_auth([Permission.SNAPSHOT_READ]),
    )
    assert result.snapshot_id == snapshot.snapshot_id


def test_assembly_capability_does_not_need_metadata_catalog() -> None:
    deps = _FakeDependencies()
    deps.catalog = None
    result = AssemblyLifecycleAdminCapability(deps, AdminServiceConfig()).create_draft(
        draft(),
        auth_context=author_auth(),
    )
    assert result.draft.draft_id == "draft-1"


def test_metadata_capability_fails_closed_without_discoverer() -> None:
    deps = _FakeDependencies()
    deps.discoverer = None
    with pytest.raises(DiscoveryError):
        MetadataAdminCapability(deps, AdminServiceConfig()).submit_discovery(
            "source-1",
            auth_context=_make_auth([Permission.DISCOVERY_RUN]),
            idempotency_key="k-1",
        )


def test_assembly_capability_fails_closed_without_draft_store() -> None:
    deps = _FakeDependencies()
    deps.draft_store = None
    with pytest.raises(NotFoundError):
        AssemblyLifecycleAdminCapability(deps, AdminServiceConfig()).get_draft(
            "draft-1",
            auth_context=_make_auth([Permission.ASSEMBLY_READ]),
        )


def test_bundle_capability_lists_bundles_without_draft_store() -> None:
    deps = _FakeDependencies()
    deps.draft_store = None
    bundle_capability = PublishedBundleAdminCapability(deps, AdminServiceConfig())
    result = bundle_capability.list_bundles(
        "bundle-1",
        auth_context=_make_auth([Permission.BUNDLE_READ]),
    )
    assert result.total == 0
