"""Versioned service command/result schema generation and validation.

The schema module provides a machine-readable catalog of all supported
command/result DTOs for a given contract version.  Hosts use this to bind
their transport (HTTP/CLI/UI) to the service without hard-coding DTO shapes.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from . import dtos


class ServiceSchema(BaseModel):
    """Catalog of command/result schemas for one contract version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: str
    commands: dict[str, type[BaseModel]]
    results: dict[str, type[BaseModel]]


_COMMAND_DTOS: tuple[type[BaseModel], ...] = (
    dtos.AuthoringDocumentCommand,
    dtos.ImportAuthoringCommand,
    dtos.AssertionDecisionCommand,
    dtos.DraftRevisionCommand,
    dtos.VerifyDraftCommand,
    dtos.PublishDraftCommand,
    dtos.ReviewCommand,
    dtos.BundleLifecycleCommand,
    dtos.PaginationParams,
)

_RESULT_DTOS: tuple[type[BaseModel], ...] = (
    dtos.AdminResult,
    dtos.SnapshotListItem,
    dtos.SnapshotDetail,
    dtos.ProposalListItem,
    dtos.ProposalSetDetail,
    dtos.ReviewResult,
    dtos.BundleListItem,
    dtos.BundleDetail,
    dtos.BundleLifecycleResult,
    dtos.BundleValidationResult,
    dtos.AuthoringDiagnosticDetail,
    dtos.AuthoringSemanticSummary,
    dtos.AuthoringValidationResult,
    dtos.AuthoringImportResult,
    dtos.DriftStatus,
    dtos.JobInfo,
    dtos.CapabilitiesResult,
    dtos.PaginatedResult,
    dtos.ErrorDetail,
    dtos.AssemblyAssertionSummary,
    dtos.DeploymentBindingSummary,
    dtos.AssemblyDraftSummary,
    dtos.AssemblyDraftDetail,
    dtos.DraftMutationResult,
    dtos.PublishAssemblyResult,
    dtos.PublishAuditSummary,
    dtos.PublishedVersionItem,
    dtos.VersionListResult,
    dtos.VerificationCaseSummary,
    dtos.VerificationLayerSummary,
    dtos.VerificationEvidenceReference,
    dtos.DraftVerificationResult,
)


def build_schema(contract_version: str) -> ServiceSchema:
    """Build the service schema for the given contract version."""
    return ServiceSchema(
        contract_version=contract_version,
        commands={cls.__name__: cls for cls in _COMMAND_DTOS},
        results={cls.__name__: cls for cls in _RESULT_DTOS},
    )


def validate_schema(schema: ServiceSchema) -> list[str]:
    """Validate that every schema model is documented and bounded.

    Returns a list of issues; an empty list means the schema is valid.
    """
    issues: list[str] = []
    all_models = {**schema.commands, **schema.results}
    if not all_models:
        issues.append("schema contains no command or result models")
    for name, model in all_models.items():
        if not issubclass(model, BaseModel):
            issues.append(f"{name} is not a Pydantic BaseModel")
    return issues
