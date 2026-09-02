"""Framework-neutral admin service protocols and injected dependencies."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from nl2data_core.assembly import (
    MAX_TRAIL_ENTRIES,
    AssemblyDraft,
    AssemblyDraftStore,
    AuditTrail,
    LifecycleAuthorizer,
    SeparationOfDutiesMode,
)
from nl2data_core.assembly.manifest import AcceptedAssertionManifest
from nl2data_core.assembly.publishing import (
    ManifestBundleVerifier,
    SemanticBundleEmitter,
)
from nl2data_core.bundles.catalog import BundleCatalogOutcome, BundlePublication
from nl2data_core.bundles.models import SemanticModelBundle
from nl2data_core.bundles.publication import PublishAuditRecord
from nl2data_core.control_plane.publication.contracts import (
    PublicationAggregate,
    PublicationDraftBinding,
)
from nl2data_core.metadata.models import MetadataSnapshot
from nl2data_core.metadata.policy import ProductionActivationContext
from nl2data_core.metadata.production import SnapshotLifecycleRecord
from nl2data_core.metadata.proposals import SemanticProposalSet
from nl2data_core.verification.execution import VerificationExecutionContext
from nl2data_core.verification.models import VerificationSuiteEvidence
from nl2data_core.verification.policy import VerificationPolicy
from nl2data_core.verification.smoke import RunnableVerificationExecutor

from .auth import AuthContext
from .dtos import (
    AuthoringDocumentCommand,
    AuthoringImportResult,
    AuthoringValidationResult,
    ImportAuthoringCommand,
)


@runtime_checkable
class AuthoringAdminService(Protocol):
    """Transport-facing bounded semantic authoring operations."""

    def validate_authoring(
        self,
        command: AuthoringDocumentCommand,
        *,
        auth_context: AuthContext,
    ) -> AuthoringValidationResult: ...

    def import_authoring(
        self,
        command: ImportAuthoringCommand,
        *,
        auth_context: AuthContext,
    ) -> AuthoringImportResult: ...


@runtime_checkable
class MetadataDiscoverer(Protocol):
    """Host-provided metadata discovery port.

    A discoverer returns bounded canonical snapshots and never exposes raw
    values, credentials, or native driver objects.
    """

    def discover(
        self,
        source_id: str,
        *,
        auth_context: AuthContext,
        bounds: dict[str, Any] | None = None,
    ) -> MetadataSnapshot | None:
        """Return a discovered snapshot, or ``None`` when denied/unavailable."""
        ...

    def supports_source(self, source_id: str) -> bool:
        """Whether this discoverer can inspect the given source."""
        ...


@runtime_checkable
class MetadataCatalogPort(Protocol):
    """Metadata snapshot and proposal-set operations used by Admin."""

    def register_snapshot(
        self,
        snapshot: MetadataSnapshot,
        *,
        tenant_scope_fingerprint: str,
        retained_for_seconds: float | None = None,
    ) -> SnapshotLifecycleRecord: ...

    def snapshot(
        self,
        snapshot_fingerprint: str,
        *,
        tenant_scope_fingerprint: str,
    ) -> MetadataSnapshot | None: ...

    def active_snapshot(
        self,
        source_id: str,
        tenant_scope_fingerprint: str,
    ) -> MetadataSnapshot | None: ...

    def save_proposal_set(
        self,
        proposal_set: SemanticProposalSet,
        *,
        tenant_scope_fingerprint: str,
    ) -> None: ...

    def proposal_set(
        self,
        snapshot_fingerprint: str,
        *,
        tenant_scope_fingerprint: str,
    ) -> SemanticProposalSet | None: ...

    def get(
        self,
        bundle_id: str,
        version: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> SemanticModelBundle | None: ...

    def versions(
        self,
        bundle_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> tuple[SemanticModelBundle, ...]: ...

    def active(
        self,
        bundle_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> SemanticModelBundle | None: ...


@runtime_checkable
class PublicationStoragePort(Protocol):
    """Immutable publication write capability used by Admin publishing."""

    def authoritative_release_binding_matches(
        self,
        binding: PublicationDraftBinding,
    ) -> bool | None: ...

    def publish(
        self,
        bundle: SemanticModelBundle,
        *,
        publication_aggregate: PublicationAggregate,
        production: ProductionActivationContext | None = None,
        publication_binding: PublicationDraftBinding | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome: ...


@runtime_checkable
class PublishedLookupPort(Protocol):
    """Published Bundle lookup, audit, evidence, and version operations."""

    def get(
        self,
        bundle_id: str,
        version: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> SemanticModelBundle | None: ...

    def get_by_fingerprint(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> SemanticModelBundle | None: ...

    def versions(
        self,
        bundle_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> tuple[SemanticModelBundle, ...]: ...

    def accepted_assertion_manifest(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> AcceptedAssertionManifest | None: ...

    def publish_audit(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> PublishAuditRecord | None: ...

    def verification_evidence(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> VerificationSuiteEvidence | None: ...

    def publication_records(
        self,
        bundle_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> tuple[BundlePublication, ...]: ...

    def audit_entries(
        self,
        *,
        tenant_scope_fingerprint: str | None = None,
        draft_id: str | None = None,
        draft_revision_min: int | None = None,
        draft_revision_max: int | None = None,
        assertion_id: str | None = None,
        bundle_fingerprint: str | None = None,
        lifecycle_reference: str | None = None,
        predecessor_event_id: str | None = None,
        limit: int = MAX_TRAIL_ENTRIES,
        cursor: str | None = None,
    ) -> AuditTrail: ...


@runtime_checkable
class ActivationLifecyclePort(Protocol):
    """Published Bundle activation, rollback, and startup state operations."""

    def active(
        self,
        bundle_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> SemanticModelBundle | None: ...

    def activate_fingerprint(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        production: ProductionActivationContext | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome: ...

    def rollback_to_fingerprint(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        production: ProductionActivationContext | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome: ...


class LifecycleCatalogPort(
    PublicationStoragePort,
    PublishedLookupPort,
    ActivationLifecyclePort,
    Protocol,
):
    """Composed published Bundle lifecycle capability used by Admin."""


@runtime_checkable
class JobRecord(Protocol):
    """Opaque job record returned by a job runner."""

    @property
    def job_id(self) -> str: ...

    @property
    def status(self) -> str: ...

    @property
    def command(self) -> str: ...


@runtime_checkable
class JobRunner(Protocol):
    """Host-provided job runner for long-running discovery/catalog operations.

    The runner is responsible for durable execution, polling, and
    cancellation.  It returns an opaque job handle immediately and updates
    status durably.
    """

    def submit(
        self,
        command: str,
        *,
        payload: dict[str, Any],
        auth_context: AuthContext,
        idempotency_key: str,
    ) -> JobRecord:
        """Submit a job and return its handle."""
        ...

    def status(self, job_id: str) -> JobRecord:
        """Return the current job status."""
        ...

    def cancel(self, job_id: str) -> JobRecord:
        """Request cancellation of a job."""
        ...


@runtime_checkable
class AdminServiceDependencies(Protocol):
    """Collection of dependencies injected into the admin service.

    Every dependency is optional at construction time, but the service will
    fail closed when a required dependency is missing for a given operation.
    """

    catalog: MetadataCatalogPort | None
    discoverer: MetadataDiscoverer | None
    job_runner: JobRunner | None
    draft_store: AssemblyDraftStore | None
    lifecycle_catalog: LifecycleCatalogPort | None
    lifecycle_authorizer: LifecycleAuthorizer | None
    bundle_emitter: SemanticBundleEmitter | None
    manifest_verifier: ManifestBundleVerifier | None
    verification_executor: RunnableVerificationExecutor | None
    verification_context_factory: VerificationExecutionContextFactory | None
    verification_policies: dict[str, VerificationPolicy]
    separation_mode: SeparationOfDutiesMode

    @property
    def audit_reference(self) -> str:
        """Host-provided audit reference for mutating operations."""
        ...


@runtime_checkable
class VerificationExecutionContextFactory(Protocol):
    """Host factory for trusted candidate-bound verification execution context."""

    def create(
        self,
        *,
        draft: AssemblyDraft,
        candidate: SemanticModelBundle,
        manifest: AcceptedAssertionManifest,
        policy: VerificationPolicy,
        auth_context: AuthContext,
    ) -> VerificationExecutionContext: ...
