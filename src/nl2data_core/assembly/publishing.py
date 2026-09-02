"""Fail-closed publication gate from approved assemblies to bundle catalogs."""

from __future__ import annotations

from nl2data_core.control_plane.publication.contracts import (
    AssemblyPublishIssue,
    AssemblyPublishOutcome,
    ManifestBundleVerification,
    PublicationContext,
    PublicationRequest,
)
from nl2data_core.control_plane.publication.coordinator import coordinate_publication
from nl2data_core.control_plane.publication.ports import (
    AssemblyPublicationCatalog,
    ManifestBundleVerifier,
    SemanticBundleEmitter,
    SynchronousVerificationProvider,
)
from nl2data_core.metadata.policy import ProductionActivationContext
from nl2data_core.verification.execution import VerificationExecutionContext
from nl2data_core.verification.models import VerificationSuiteEvidence
from nl2data_core.verification.policy import VerificationPolicy
from nl2data_core.verification.smoke import RunnableVerificationExecutor
from nl2data_core.verification.structural import CoreStructuralVerificationRunner

from .authorization import (
    LifecycleAuthorizationContext,
    LifecycleAuthorizer,
)
from .models import AssemblyDraft
from .separation import SeparationOfDutiesDecision

__all__ = [
    "AssemblyPublicationCatalog",
    "AssemblyPublishIssue",
    "AssemblyPublishOutcome",
    "ManifestBundleVerification",
    "ManifestBundleVerifier",
    "SemanticBundleEmitter",
    "SynchronousVerificationProvider",
    "publish_assembly",
]


def publish_assembly(
    draft: AssemblyDraft,
    *,
    expected_revision: int,
    authorization: LifecycleAuthorizationContext,
    authorizer: LifecycleAuthorizer,
    separation: SeparationOfDutiesDecision,
    emitter: SemanticBundleEmitter,
    verifier: ManifestBundleVerifier,
    catalog: AssemblyPublicationCatalog,
    structural_runner: CoreStructuralVerificationRunner | None = None,
    verification_policy: VerificationPolicy | None = None,
    verification_context: VerificationExecutionContext | None = None,
    verification_evidence: VerificationSuiteEvidence | None = None,
    verification_executor: RunnableVerificationExecutor | None = None,
    verification_provider: SynchronousVerificationProvider | None = None,
    approval_chain: tuple[str, ...] = (),
    production: ProductionActivationContext | None = None,
) -> AssemblyPublishOutcome:
    """Validate all publication gates before one atomic catalog write."""
    request = PublicationRequest(
        draft=draft,
        expected_revision=expected_revision,
        approval_chain=approval_chain,
        production=production,
    )
    context = PublicationContext(
        authorization=authorization,
        separation=separation,
        verification_policy=verification_policy,
        verification_context=verification_context,
        verification_evidence=verification_evidence,
    )
    return coordinate_publication(
        request,
        context,
        authorizer=authorizer,
        emitter=emitter,
        verifier=verifier,
        catalog=catalog,
        structural_runner=structural_runner,
        verification_executor=verification_executor,
        verification_provider=verification_provider,
    )