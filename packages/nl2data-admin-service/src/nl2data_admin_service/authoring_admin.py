"""Semantic authoring Admin capability service."""

from __future__ import annotations

from typing import Any

from nl2data_core.assembly.authoring import (
    MAX_AUTHORING_BYTES,
    SemanticAssemblyAuthoringLoader,
    lower_authoring,
)
from nl2data_core.assembly.authoring import (
    validate_authoring as core_validate_authoring,
)

from .assembly_admin import AssemblyLifecycleAdminCapability
from .auth import AuthContext, Permission
from .common import (
    lifecycle_context,
    require_permission,
    require_source,
)
from .common import (
    normalize_errors as _normalize_errors,
)
from .config import AdminServiceConfig
from .dtos import (
    AuthoringDiagnosticDetail,
    AuthoringDocumentCommand,
    AuthoringImportResult,
    AuthoringSemanticSummary,
    AuthoringValidationResult,
    ImportAuthoringCommand,
)
from .protocols import AdminServiceDependencies


def _authoring_diagnostics(
    diagnostics: tuple[Any, ...],
) -> tuple[AuthoringDiagnosticDetail, ...]:
    return tuple(
        AuthoringDiagnosticDetail(
            code=diagnostic.code,
            path=diagnostic.path.render(),
            line=diagnostic.mark.line if diagnostic.mark is not None else None,
            column=diagnostic.mark.column if diagnostic.mark is not None else None,
            message=diagnostic.message,
        )
        for diagnostic in diagnostics
    )


def _authoring_input_failure(
    document: str,
    *,
    maximum_bytes: int,
) -> AuthoringDiagnosticDetail | None:
    try:
        byte_length = len(document.encode("utf-8"))
    except UnicodeError:
        return AuthoringDiagnosticDetail(
            code="invalid_encoding",
            path="$",
            message="The authoring document is not valid UTF-8 text.",
        )
    if byte_length > maximum_bytes:
        return AuthoringDiagnosticDetail(
            code="input_too_large",
            path="$",
            message="The authoring document exceeds the input limit.",
        )
    return None


class AuthoringAdminCapability:
    """Authoring document validation and import into assembly drafts."""

    def __init__(
        self,
        dependencies: AdminServiceDependencies,
        config: AdminServiceConfig,
        assembly: AssemblyLifecycleAdminCapability,
    ) -> None:
        self._deps = dependencies
        self._config = config
        self._assembly = assembly

    @_normalize_errors
    def validate_authoring(
        self,
        command: AuthoringDocumentCommand,
        *,
        auth_context: AuthContext,
    ) -> AuthoringValidationResult:
        """Validate authoring content without touching persistence."""
        require_permission(auth_context, Permission.BUNDLE_VALIDATE)
        input_failure = _authoring_input_failure(
            command.document,
            maximum_bytes=min(self._config.max_body_size_bytes, MAX_AUTHORING_BYTES),
        )
        if input_failure is not None:
            return AuthoringValidationResult(
                valid=False,
                diagnostics=(input_failure,),
                issue_count=1,
            )
        parsed = SemanticAssemblyAuthoringLoader().load(command.document)
        if parsed.model is None:
            return AuthoringValidationResult(
                valid=False,
                diagnostics=_authoring_diagnostics(parsed.diagnostics),
                issue_count=parsed.issue_count,
                truncated=parsed.truncated,
            )
        require_source(auth_context, parsed.model.spec.source.source_id)
        validated = core_validate_authoring(parsed.model)
        assert validated.summary is not None
        return AuthoringValidationResult(
            valid=True,
            summary=AuthoringSemanticSummary(**validated.summary.model_dump()),
        )

    @_normalize_errors
    def import_authoring(
        self,
        command: ImportAuthoringCommand,
        *,
        auth_context: AuthContext,
    ) -> AuthoringImportResult:
        """Lower valid authoring content and persist through create_draft."""
        require_permission(auth_context, Permission.ASSEMBLY_WRITE)
        input_failure = _authoring_input_failure(
            command.document,
            maximum_bytes=min(self._config.max_body_size_bytes, MAX_AUTHORING_BYTES),
        )
        if input_failure is not None:
            return AuthoringImportResult(
                imported=False,
                diagnostics=(input_failure,),
                issue_count=1,
            )
        parsed = SemanticAssemblyAuthoringLoader().load(command.document)
        if parsed.model is None:
            return AuthoringImportResult(
                imported=False,
                diagnostics=_authoring_diagnostics(parsed.diagnostics),
                issue_count=parsed.issue_count,
                truncated=parsed.truncated,
            )
        source_id = parsed.model.spec.source.source_id
        require_source(auth_context, source_id)
        authorization = lifecycle_context(self._deps, auth_context, source_id)
        lowered = lower_authoring(
            parsed.model,
            draft_id=command.draft_id,
            author_reference=authorization.operator_reference,
        )
        if lowered.draft is None:
            return AuthoringImportResult(
                imported=False,
                diagnostics=_authoring_diagnostics(lowered.diagnostics),
                issue_count=lowered.issue_count,
                truncated=lowered.truncated,
            )
        created = self._assembly.create_draft(lowered.draft, auth_context=auth_context)
        return AuthoringImportResult(imported=True, draft=created.draft)
