"""Semantic assembly lint Admin capability service."""

from __future__ import annotations

from nl2data_core.assembly.authoring import (
    MAX_AUTHORING_BYTES,
    SemanticAssemblyAuthoringLoader,
)
from nl2data_core.assembly.authoring import (
    validate_authoring as core_validate_authoring,
)
from nl2data_core.assembly.lint import (
    LintProfileId,
    LintResult,
)
from nl2data_core.assembly.lint import (
    lint_authoring as core_lint_authoring,
)
from nl2data_core.assembly.lint import (
    lint_draft as core_lint_draft,
)

from .assembly_admin import AssemblyLifecycleAdminCapability
from .auth import AuthContext, Permission
from .authoring_admin import _authoring_input_failure
from .common import (
    load_draft,
    require_permission,
    require_source,
)
from .common import (
    normalize_errors as _normalize_errors,
)
from .config import AdminServiceConfig
from .dtos import (
    AdminLintDiagnostic,
    LintAuthoringCommand,
    LintDraftCommand,
    LintResultDetail,
)
from .errors import ValidationError
from .protocols import AdminServiceDependencies


def _lint_detail(result: LintResult) -> LintResultDetail:
    summary = result.summary
    return LintResultDetail(
        profile=summary.profile.value,
        profile_version=summary.profile_version,
        diagnostics=tuple(
            AdminLintDiagnostic(
                code=diagnostic.code,
                severity=diagnostic.severity.value,
                path=diagnostic.target_path.render(),
                line=(
                    diagnostic.source_location.line
                    if diagnostic.source_location is not None
                    else None
                ),
                column=(
                    diagnostic.source_location.column
                    if diagnostic.source_location is not None
                    else None
                ),
                message=diagnostic.message,
            )
            for diagnostic in result.diagnostics
        ),
        diagnostic_count=summary.diagnostic_count,
        error_count=summary.error_count,
        warning_count=summary.warning_count,
        info_count=summary.info_count,
        has_errors=summary.has_errors,
        blocking=summary.blocking,
        truncated=summary.truncated,
    )


class LintAdminCapability:
    """Side-effect-free semantic assembly lint over authoring content and drafts."""

    def __init__(
        self,
        dependencies: AdminServiceDependencies,
        config: AdminServiceConfig,
        assembly: AssemblyLifecycleAdminCapability,
    ) -> None:
        self._deps = dependencies
        self._config = config
        self._assembly = assembly
        self._access = assembly._access

    @_normalize_errors
    def lint_authoring(
        self,
        command: LintAuthoringCommand,
        *,
        auth_context: AuthContext,
    ) -> LintResultDetail:
        """Lint an authoring document after safe parse and validation; persist nothing."""
        require_permission(auth_context, Permission.BUNDLE_VALIDATE)
        input_failure = _authoring_input_failure(
            command.document,
            maximum_bytes=min(self._config.max_body_size_bytes, MAX_AUTHORING_BYTES),
        )
        if input_failure is not None:
            raise ValidationError(input_failure.message)
        parsed = SemanticAssemblyAuthoringLoader().load(command.document)
        if parsed.model is None:
            raise ValidationError(
                "The authoring document is not valid; run authoring validation first."
            )
        require_source(auth_context, parsed.model.spec.source.source_id)
        validated = core_validate_authoring(parsed.model)
        if not validated.valid or validated.model is None:
            raise ValidationError(
                "The authoring document is not valid; run authoring validation first."
            )
        result = core_lint_authoring(
            validated.model,
            profile=LintProfileId(command.profile.value),
            source_marks={entry.path.parts: entry.mark for entry in parsed.source_marks},
        )
        return _lint_detail(result)

    @_normalize_errors
    def lint_draft(
        self,
        draft_id: str,
        command: LintDraftCommand,
        *,
        auth_context: AuthContext,
    ) -> LintResultDetail:
        """Lint a stored draft at its expected revision without mutation."""
        require_permission(auth_context, Permission.ASSEMBLY_READ)
        draft = load_draft(self._access, draft_id, auth_context)
        draft.require_revision(command.expected_revision)
        result = core_lint_draft(draft, profile=LintProfileId(command.profile.value))
        return _lint_detail(result)
