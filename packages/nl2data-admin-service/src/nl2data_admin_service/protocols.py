"""Framework-neutral admin service protocols and injected dependencies."""

from __future__ import annotations

from typing import Any, Protocol

from nl2data_core.assembly import (
    AssemblyDraftStore,
    LifecycleAuthorizer,
    SeparationOfDutiesMode,
)
from nl2data_core.assembly.publishing import (
    AssemblyPublicationCatalog,
    ManifestBundleVerifier,
    SemanticBundleEmitter,
)
from nl2data_core.metadata.catalog import SemanticSnapshotCatalog
from nl2data_core.metadata.models import MetadataSnapshot
from nl2data_core.verification.execution import VerificationExecutionContext
from nl2data_core.verification.policy import VerificationPolicy
from nl2data_core.verification.smoke import RunnableVerificationExecutor

from .auth import AuthContext
from .dtos import (
    AuthoringDocumentCommand,
    AuthoringImportResult,
    AuthoringValidationResult,
    ImportAuthoringCommand,
)


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


class JobRecord(Protocol):
    """Opaque job record returned by a job runner."""

    @property
    def job_id(self) -> str: ...

    @property
    def status(self) -> str: ...

    @property
    def command(self) -> str: ...


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


class AdminServiceDependencies(Protocol):
    """Collection of dependencies injected into the admin service.

    Every dependency is optional at construction time, but the service will
    fail closed when a required dependency is missing for a given operation.
    """

    catalog: SemanticSnapshotCatalog | None
    discoverer: MetadataDiscoverer | None
    job_runner: JobRunner | None
    draft_store: AssemblyDraftStore | None
    lifecycle_catalog: AssemblyPublicationCatalog | None
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


class VerificationExecutionContextFactory(Protocol):
    """Host factory for trusted candidate-bound verification execution context."""

    def create(
        self,
        *,
        draft: Any,
        candidate: Any,
        manifest: Any,
        policy: VerificationPolicy,
        auth_context: AuthContext,
    ) -> VerificationExecutionContext: ...
