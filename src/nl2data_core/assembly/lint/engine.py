"""Deterministic lint engine for semantic assemblies.

The engine accepts only already parsed and validated semantic objects: a
validated authoring model or a lifecycle ``AssemblyDraft``.  It never
parses unsafe YAML, lowers invalid models, mutates drafts, creates review
bindings, verification evidence, or audit records, and it never touches
semantic Bundle fingerprints.
"""

from __future__ import annotations

from nl2data_core.assembly.authoring.models import SemanticAssemblyAuthoring
from nl2data_core.assembly.models import AssemblyDraft

from .messages import bounded_message
from .models import (
    MAX_LINT_DIAGNOSTICS,
    LintDiagnostic,
    LintProfileId,
    LintReference,
    LintResult,
    LintResultSummary,
    LintSeverity,
    LintSourceLocation,
    LintTargetPath,
)
from .profiles import LINT_PROFILES
from .rules import LintFinding, run_builtin_rules
from .snapshot import SourceMarks, snapshot_from_authoring, snapshot_from_draft

_SEVERITY_ORDER = {
    LintSeverity.ERROR: 0,
    LintSeverity.WARNING: 1,
    LintSeverity.INFO: 2,
}


def _ordering_key(diagnostic: LintDiagnostic) -> tuple[object, ...]:
    location = diagnostic.source_location
    return (
        _SEVERITY_ORDER[diagnostic.severity],
        diagnostic.code,
        diagnostic.target_path.render(),
        (0, location.line, location.column) if location is not None else (1, 0, 0),
        tuple(reference.value for reference in diagnostic.references),
    )


def _build_result(
    findings: list[LintFinding],
    *,
    profile_id: LintProfileId,
) -> LintResult:
    profile = LINT_PROFILES[profile_id]
    diagnostics: list[LintDiagnostic] = []
    seen: set[tuple[object, ...]] = set()
    for finding in findings:
        severity = profile.severity_for(finding.code)
        if severity is None:
            continue
        diagnostic = LintDiagnostic(
            code=finding.code,
            severity=severity,
            profile=profile.profile,
            target_path=LintTargetPath(parts=finding.path),
            source_location=(
                LintSourceLocation(line=finding.mark.line, column=finding.mark.column)
                if finding.mark is not None
                else None
            ),
            message=bounded_message(finding.message),
            references=tuple(
                LintReference(kind=kind, value=value) for kind, value in finding.references
            ),
        )
        identity = (
            diagnostic.code,
            diagnostic.target_path.render(),
            diagnostic.message,
            tuple(reference.value for reference in diagnostic.references),
        )
        if identity in seen:
            continue
        seen.add(identity)
        diagnostics.append(diagnostic)
    diagnostics.sort(key=_ordering_key)
    truncated = len(diagnostics) > MAX_LINT_DIAGNOSTICS
    diagnostics = diagnostics[:MAX_LINT_DIAGNOSTICS]
    error_count = sum(item.severity is LintSeverity.ERROR for item in diagnostics)
    warning_count = sum(item.severity is LintSeverity.WARNING for item in diagnostics)
    info_count = sum(item.severity is LintSeverity.INFO for item in diagnostics)
    summary = LintResultSummary(
        profile=profile.profile,
        profile_version=profile.version,
        diagnostic_count=len(diagnostics),
        error_count=error_count,
        warning_count=warning_count,
        info_count=info_count,
        has_errors=error_count > 0,
        blocking=error_count > 0,
        truncated=truncated,
    )
    return LintResult(summary=summary, diagnostics=tuple(diagnostics))


def lint_authoring(
    model: SemanticAssemblyAuthoring,
    *,
    profile: LintProfileId = LintProfileId.RECOMMENDED,
    source_marks: SourceMarks | None = None,
) -> LintResult:
    """Lint a validated authoring model without parsing or persistence.

    ``source_marks`` optionally carries authoring source marks keyed by
    authoring path tuples; diagnostics on source-backed targets then
    include line/column locations.
    """
    snapshot = snapshot_from_authoring(model, source_marks=source_marks)
    findings = run_builtin_rules(snapshot)
    return _build_result(findings, profile_id=profile)


def lint_draft(
    draft: AssemblyDraft,
    *,
    profile: LintProfileId = LintProfileId.RECOMMENDED,
) -> LintResult:
    """Lint a lifecycle draft without mutation or lifecycle authority."""
    snapshot = snapshot_from_draft(draft)
    findings = run_builtin_rules(snapshot)
    return _build_result(findings, profile_id=profile)
