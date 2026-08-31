"""Shared test fixtures for the admin service."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from nl2data_core.assembly import (
    InMemoryAssemblyDraftStore,
    LifecycleAuthorizationDecision,
    LifecycleAuthorizationRequest,
    LifecycleRole,
    SeparationOfDutiesMode,
)
from nl2data_core.assembly.publishing import ManifestBundleVerification
from nl2data_core.bundles import InMemorySemanticBundleCatalog
from nl2data_core.bundles.catalog import BundleCatalogIssue, BundleCatalogOutcome
from nl2data_core.bundles.models import (
    BundleCompatibility,
    BundleProvenance,
    BundleQualityStatus,
    SemanticDescriptor,
    SemanticModelBundle,
    SemanticSourceReference,
)
from nl2data_core.views import SemanticEntityDescriptor
from nl2data_core.metadata.catalog import SemanticSnapshotCatalog
from nl2data_core.metadata.models import (
    MetadataConfidence,
    MetadataObject,
    MetadataObjectKind,
    MetadataProvenance,
    MetadataSnapshot,
    MetadataSourceReference,
)
from nl2data_core.metadata.proposals import SemanticProposal, SemanticProposalSet

from nl2data_admin_service.auth import AuthContext, Permission
from nl2data_admin_service.config import AdminServiceConfig
from nl2data_admin_service.service import AdminService

_FINGERPRINT = "sha256:" + "0" * 64
_SOURCE_FP = "sha256:" + "1" * 64
_SNAP_FP = "sha256:" + "2" * 64
_EVIDENCE_FP = "sha256:" + "3" * 64


class _FakeCatalog(SemanticSnapshotCatalog):
    """In-memory semantic catalog for testing."""

    def __init__(self) -> None:
        self._snapshots: dict[str, MetadataSnapshot] = {}
        self._proposal_sets: dict[str, SemanticProposalSet] = {}
        self._bundles: dict[str, list[SemanticModelBundle]] = {}
        self._active: dict[str, SemanticModelBundle] = {}
        self._active_snapshots: dict[str, MetadataSnapshot] = {}

    def register_snapshot(
        self,
        snapshot: MetadataSnapshot,
        *,
        tenant_scope_fingerprint: str,
        retained_for_seconds: float | None = None,
    ) -> Any:
        self._snapshots[snapshot.fingerprint] = snapshot
        return None

    def snapshot(
        self, snapshot_fingerprint: str, *, tenant_scope_fingerprint: str
    ) -> MetadataSnapshot | None:
        return self._snapshots.get(snapshot_fingerprint)

    def activate_snapshot(
        self,
        snapshot_fingerprint: str,
        *,
        tenant_scope_fingerprint: str,
        policy: Any | None = None,
        drift_decision: Any | None = None,
        overrides: tuple[Any, ...] = (),
        now: datetime | None = None,
    ) -> Any:
        raise NotImplementedError

    def active_snapshot(
        self, source_id: str, tenant_scope_fingerprint: str
    ) -> MetadataSnapshot | None:
        return self._active_snapshots.get(source_id)

    def save_proposal_set(
        self,
        proposal_set: SemanticProposalSet,
        *,
        tenant_scope_fingerprint: str,
    ) -> None:
        self._proposal_sets[proposal_set.snapshot_fingerprint] = proposal_set

    def proposal_set(
        self, snapshot_fingerprint: str, *, tenant_scope_fingerprint: str
    ) -> SemanticProposalSet | None:
        return self._proposal_sets.get(snapshot_fingerprint)

    def publish(
        self,
        bundle: SemanticModelBundle,
        *,
        production: Any | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome:
        self._bundles.setdefault(bundle.bundle_id, []).append(bundle)
        return BundleCatalogOutcome(kind="published", bundle=bundle)

    def get(
        self, bundle_id: str, version: str, *, tenant_scope_fingerprint: str | None = None
    ) -> SemanticModelBundle | None:
        for bundle in self._bundles.get(bundle_id, []):
            if bundle.model_version == version:
                return bundle
        return None

    def versions(
        self, bundle_id: str, *, tenant_scope_fingerprint: str | None = None
    ) -> tuple[SemanticModelBundle, ...]:
        return tuple(self._bundles.get(bundle_id, []))

    def active(
        self, bundle_id: str, *, tenant_scope_fingerprint: str | None = None
    ) -> SemanticModelBundle | None:
        return self._active.get(bundle_id)

    def activate(
        self,
        bundle_id: str,
        version: str,
        *,
        production: Any | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome:
        bundle = self.get(bundle_id, version)
        if bundle is None:
            return BundleCatalogOutcome(
                kind="not_found",
                issues=(BundleCatalogIssue(code="not_found", message="bundle not found"),),
            )
        self._active[bundle_id] = bundle
        return BundleCatalogOutcome(kind="activated", bundle=bundle)

    def rollback(
        self,
        bundle_id: str,
        *,
        production: Any | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome:
        bundle = self._active.get(bundle_id)
        if bundle is None:
            return BundleCatalogOutcome(
                kind="no_history",
                issues=(BundleCatalogIssue(code="no_history", message="no rollback history"),),
            )
        return BundleCatalogOutcome(kind="rolled_back", bundle=bundle)

    def cleanup(self, *, now: datetime | None = None) -> int:
        return 0

    def reload_active(self, *, now: datetime | None = None) -> Any:
        raise NotImplementedError


class _FakeDependencies:
    def __init__(self) -> None:
        self.catalog: _FakeCatalog = _FakeCatalog()
        self.discoverer: Any = None
        self.job_runner: Any = None
        self.draft_store = InMemoryAssemblyDraftStore()
        self.lifecycle_authorizer = _AllowLifecycleAuthorizer()
        self.lifecycle_catalog = InMemorySemanticBundleCatalog(
            draft_store=self.draft_store
        )
        self.bundle_emitter = _FakeBundleEmitter()
        self.manifest_verifier = _AllowManifestVerifier()
        self.separation_mode = SeparationOfDutiesMode.SOLO_WITH_WAIVER

    @property
    def audit_reference(self) -> str:
        return "test-audit"


class _FakeDiscoverer:
    """In-memory discoverer returning one canned snapshot."""

    def __init__(self, snapshot: MetadataSnapshot | None = None) -> None:
        self._snapshot = snapshot
        self.sources: list[str] = []

    def discover(
        self,
        source_id: str,
        *,
        auth_context: AuthContext,
        bounds: dict[str, Any] | None = None,
    ) -> MetadataSnapshot | None:
        self.sources.append(source_id)
        return self._snapshot

    def supports_source(self, source_id: str) -> bool:
        return source_id in {"source-1", "source-2"}


class _AllowLifecycleAuthorizer:
    def __init__(self) -> None:
        self.requests: list[LifecycleAuthorizationRequest] = []

    def authorize(
        self,
        request: LifecycleAuthorizationRequest,
    ) -> LifecycleAuthorizationDecision:
        self.requests.append(request)
        return LifecycleAuthorizationDecision(allowed=True)


class _FakeBundleEmitter:
    def emit(self, draft: Any) -> SemanticModelBundle:
        return _make_bundle().model_copy(
            update={
                "bundle_id": draft.bundle_id,
                "model_version": draft.model_version,
            }
        )


class _AllowManifestVerifier:
    def verify(self, draft: Any, manifest: Any, bundle: Any) -> ManifestBundleVerification:
        return ManifestBundleVerification(valid=True)


def _make_auth(
    permissions: list[Permission] | None = None,
    *,
    lifecycle_roles: frozenset[LifecycleRole] = frozenset(),
) -> AuthContext:
    return AuthContext(
        operator_id="op-1",
        tenant_scope_fingerprint=_FINGERPRINT,
        source_ids=frozenset(["source-1"]),
        permissions=frozenset(permissions or []),
        lifecycle_roles=lifecycle_roles,
        audit_reference="audit-1",
    )


def _make_snapshot() -> MetadataSnapshot:
    return MetadataSnapshot(
        snapshot_id="snap-1",
        source=MetadataSourceReference(
            source_id="source-1",
            catalog_fingerprint=_SOURCE_FP,
        ),
        objects=(
            MetadataObject(
                object_id="obj-1",
                kind=MetadataObjectKind.TABLE,
                name="customers",
            ),
        ),
        provenance=MetadataProvenance(
            discovered_by_fingerprint="sha256:" + "4" * 64,
            method="test",
        ),
    )


def _make_proposal_set(snapshot: MetadataSnapshot) -> SemanticProposalSet:
    proposal = SemanticProposal(
        proposal_id="p-1",
        kind="entity",
        target_id="obj-1",
        method="test",
        confidence=MetadataConfidence(value=0.9, method="test"),
        evidence_fingerprint=_EVIDENCE_FP,
        snapshot_fingerprint=snapshot.fingerprint,
    )
    return SemanticProposalSet(
        snapshot_fingerprint=snapshot.fingerprint,
        proposals=(proposal,),
    )


def _make_bundle() -> SemanticModelBundle:
    return SemanticModelBundle(
        bundle_id="bundle-1",
        model_version="v1",
        descriptor=SemanticDescriptor(
            descriptor_id="desc-1",
            version=1,
            source_id="source-1",
            entities=(SemanticEntityDescriptor(entity_id="orders", label="Orders"),),
        ),
        sources=(
            SemanticSourceReference(
                reference_id="src-1",
                source_id="source-1",
                catalog_fingerprint=_SOURCE_FP,
            ),
        ),
        provenance=BundleProvenance(
            owner_reference="owner-1",
            quality=BundleQualityStatus.VALIDATED,
        ),
        compatibility=BundleCompatibility(),
        fingerprint="sha256:" + "5" * 64,
    )


@pytest.fixture
def dependencies() -> _FakeDependencies:
    return _FakeDependencies()


@pytest.fixture
def service(dependencies: _FakeDependencies) -> AdminService:
    return AdminService(dependencies, AdminServiceConfig())


@pytest.fixture
def auth_read() -> AuthContext:
    return _make_auth([Permission.SNAPSHOT_READ])
