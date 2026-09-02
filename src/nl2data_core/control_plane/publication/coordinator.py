"""Fixed-order publication coordinator."""

from __future__ import annotations

from nl2data_core.assembly.authorization import LifecycleAuthorizer
from nl2data_core.control_plane.publication.contracts import (
    AssemblyPublishOutcome,
    PublicationContext,
    PublicationRequest,
)
from nl2data_core.control_plane.publication.gates_freeze import (
    freeze_stage,
    materialize_stage,
)
from nl2data_core.control_plane.publication.gates_verify import (
    aggregate_stage,
    persist_stage,
    verify_stage,
)
from nl2data_core.control_plane.publication.ports import (
    AssemblyPublicationCatalog,
    ManifestBundleVerifier,
    SemanticBundleEmitter,
    SynchronousVerificationProvider,
)
from nl2data_core.verification.smoke import RunnableVerificationExecutor
from nl2data_core.verification.structural import CoreStructuralVerificationRunner


def coordinate_publication(
    request: PublicationRequest,
    context: PublicationContext,
    *,
    authorizer: LifecycleAuthorizer,
    emitter: SemanticBundleEmitter,
    verifier: ManifestBundleVerifier,
    catalog: AssemblyPublicationCatalog,
    structural_runner: CoreStructuralVerificationRunner | None = None,
    verification_executor: RunnableVerificationExecutor | None = None,
    verification_provider: SynchronousVerificationProvider | None = None,
) -> AssemblyPublishOutcome:
    """Run the fixed fail-closed publication stages in security order."""
    frozen = freeze_stage(
        request,
        context,
        authorizer=authorizer,
        catalog=catalog,
    )
    if isinstance(frozen, AssemblyPublishOutcome):
        return frozen
    materialized = materialize_stage(
        request,
        context,
        emitter=emitter,
        structural_runner=structural_runner,
    )
    if isinstance(materialized, AssemblyPublishOutcome):
        return materialized
    verified = verify_stage(
        request,
        context,
        materialized,
        frozen,
        verifier=verifier,
        verification_executor=verification_executor,
        verification_provider=verification_provider,
    )
    if isinstance(verified, AssemblyPublishOutcome):
        return verified
    aggregate = aggregate_stage(request, context, materialized, verified)
    if isinstance(aggregate, AssemblyPublishOutcome):
        return aggregate
    return persist_stage(
        request,
        context,
        frozen,
        aggregate,
        catalog=catalog,
    )
