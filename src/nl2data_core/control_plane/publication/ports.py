"""Typed ports consumed by semantic publication orchestration."""

from __future__ import annotations

from typing import Protocol

from nl2data_core.assembly.manifest import AcceptedAssertionManifest
from nl2data_core.assembly.models import AssemblyDraft
from nl2data_core.bundles.catalog import BundleCatalogOutcome
from nl2data_core.bundles.models import SemanticModelBundle
from nl2data_core.control_plane.publication.contracts import (
    ManifestBundleVerification,
    PublicationAggregate,
    PublicationDraftBinding,
)
from nl2data_core.metadata.policy import ProductionActivationContext
from nl2data_core.verification.execution import VerificationExecutionContext
from nl2data_core.verification.models import (
    VerificationLayerEvidence,
    VerificationPlan,
    VerificationSuiteEvidence,
)
from nl2data_core.verification.policy import VerificationPolicy


class ManifestBundleVerifier(Protocol):
    """Required host verifier for assertion-manifest to Bundle equivalence."""

    def verify(
        self,
        draft: AssemblyDraft,
        manifest: AcceptedAssertionManifest,
        bundle: SemanticModelBundle,
    ) -> ManifestBundleVerification: ...


class SemanticBundleEmitter(Protocol):
    """Host emitter that constructs the runtime Bundle inside publish."""

    def emit(self, draft: AssemblyDraft) -> SemanticModelBundle: ...


class SynchronousVerificationProvider(Protocol):
    """Host-controlled bridge for running the async suite outside core publish."""

    def provide(
        self,
        *,
        plan: VerificationPlan,
        policy: VerificationPolicy,
        structural_evidence: VerificationLayerEvidence,
        context: VerificationExecutionContext,
        draft_id: str,
        draft_revision: int,
    ) -> VerificationSuiteEvidence: ...


class AssemblyPublicationCatalog(Protocol):
    """Tenant-bound catalog port used by the atomic assembly publish gate."""

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