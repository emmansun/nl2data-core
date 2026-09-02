"""Built-in semantic assembly lint rules.

Every rule is a pure function over a :class:`LintSnapshot` and emits
findings with a stable ``SAL###`` code, a semantic target path, a bounded
safe message, optional safe references, and an optional authoring source
mark.  Severity per profile is resolved by the engine from the profile
catalog, never by the rule.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from nl2data_core.assembly.authoring.diagnostics import AuthoringSourceMark

from .messages import (
    bounded_message,
    is_missing_description,
    is_placeholder_description,
    is_weak_description,
    safe_scalar_summary,
)
from .snapshot import LintSnapshot, LintVerificationView

MAX_RULE_REFERENCES = 8


@dataclass(frozen=True)
class LintFinding:
    """One raw rule finding before profile severity resolution."""

    code: str
    path: tuple[str | int, ...]
    message: str
    references: tuple[tuple[str, str], ...] = ()
    mark: AuthoringSourceMark | None = None


@dataclass(frozen=True)
class _LabeledMember:
    label: str
    path: tuple[str | int, ...]
    mark: AuthoringSourceMark | None = None


def _render_path(path: tuple[str | int, ...]) -> str:
    rendered = "$"
    for part in path:
        rendered += f"[{part}]" if isinstance(part, int) else f".{part}"
    return rendered


def _sort_key(path: tuple[str | int, ...]) -> tuple[str, ...]:
    return tuple(str(part) for part in path)


def _labeled_members(snapshot: LintSnapshot) -> list[_LabeledMember]:
    members: list[_LabeledMember] = []
    for entity in snapshot.entities:
        members.append(_LabeledMember(entity.label, entity.location.path, entity.location.mark))
    for field_member in snapshot.fields:
        members.append(
            _LabeledMember(
                field_member.label,
                field_member.location.path,
                field_member.location.mark,
            )
        )
        for term in field_member.mapping_terms:
            members.append(
                _LabeledMember(term, field_member.location.path, field_member.location.mark)
            )
    for measure_member in snapshot.measures:
        members.append(
            _LabeledMember(
                measure_member.label, measure_member.location.path, measure_member.location.mark
            )
        )
    for calculated_member in snapshot.calculated_fields:
        members.append(
            _LabeledMember(
                calculated_member.label,
                calculated_member.location.path,
                calculated_member.location.mark,
            )
        )
    members.sort(key=lambda member: _sort_key(member.path))
    return members


def _member_references(members: list[_LabeledMember]) -> tuple[tuple[str, str], ...]:
    """Render deduplicated member references in deterministic path order."""
    seen: set[str] = set()
    references: list[tuple[str, str]] = []
    for member in members[:MAX_RULE_REFERENCES]:
        rendered = _render_path(member.path)
        if rendered not in seen:
            seen.add(rendered)
            references.append(("member", rendered))
    return tuple(references)


def rule_duplicate_business_labels(snapshot: LintSnapshot) -> list[LintFinding]:
    """SAL001: duplicate or confusable business labels in one assembly."""
    groups: dict[str, list[_LabeledMember]] = defaultdict(list)
    for member in _labeled_members(snapshot):
        groups[" ".join(member.label.split()).casefold()].append(member)
    findings: list[LintFinding] = []
    for key in sorted(groups):
        members = groups[key]
        if len(members) < 2:
            continue
        findings.append(
            LintFinding(
                code="SAL001",
                path=members[0].path,
                mark=members[0].mark,
                message=(
                    f"Duplicate business label {safe_scalar_summary(members[0].label)!r} "
                    f"is shared by {len(members)} semantic members."
                ),
                references=_member_references(members),
            )
        )
    return findings


_DescribedMember = tuple[
    str,
    str,
    tuple[str | int, ...],
    AuthoringSourceMark | None,
]


def _described_members(snapshot: LintSnapshot) -> list[_DescribedMember]:
    """Collect (kind, description, path, mark) for every described member."""
    entries: list[_DescribedMember] = []
    for entity in snapshot.entities:
        entries.append(("entity", entity.description, entity.location.path, entity.location.mark))
    for field_member in snapshot.fields:
        entries.append(
            (
                "field",
                field_member.description,
                field_member.location.path,
                field_member.location.mark,
            )
        )
    for measure_member in snapshot.measures:
        entries.append(
            (
                "measure",
                measure_member.description,
                measure_member.location.path,
                measure_member.location.mark,
            )
        )
    for calculated_member in snapshot.calculated_fields:
        entries.append(
            (
                "calculated field",
                calculated_member.description,
                calculated_member.location.path,
                calculated_member.location.mark,
            )
        )
    for grain in snapshot.grains:
        entries.append(("grain", grain.description, grain.location.path, grain.location.mark))
    entries.sort(key=lambda entry: _sort_key(entry[2]))
    return entries


def rule_missing_descriptions(snapshot: LintSnapshot) -> list[LintFinding]:
    """SAL002: missing or too-short descriptions where clarity is required."""
    return [
        LintFinding(
            code="SAL002",
            path=path,
            mark=mark,
            message=f"The {kind} lacks a clear business description.",
            references=(("member", _render_path(path)),),
        )
        for kind, description, path, mark in _described_members(snapshot)
        if is_weak_description(description)
    ]


def rule_placeholder_descriptions(snapshot: LintSnapshot) -> list[LintFinding]:
    """SAL003: placeholder descriptions where clarity is required."""
    return [
        LintFinding(
            code="SAL003",
            path=path,
            mark=mark,
            message=f"The {kind} uses a placeholder business description.",
            references=(("member", _render_path(path)),),
        )
        for kind, description, path, mark in _described_members(snapshot)
        if is_placeholder_description(description)
    ]


def rule_sensitive_masking_gaps(snapshot: LintSnapshot) -> list[LintFinding]:
    """SAL004: PII-classified fields without handling or masking metadata."""
    return [
        LintFinding(
            code="SAL004",
            path=member.location.path,
            mark=member.location.mark,
            message=(
                f"PII-classified field {safe_scalar_summary(member.field_id)!r} "
                "has no handling or masking description."
            ),
            references=(("member", _render_path(member.location.path)),),
        )
        for member in snapshot.fields
        if member.pii and is_missing_description(member.description)
    ]


def rule_sensitive_sample_values(snapshot: LintSnapshot) -> list[LintFinding]:
    """SAL005: PII-classified fields that expose sample values."""
    return [
        LintFinding(
            code="SAL005",
            path=member.location.path,
            mark=member.location.mark,
            message=(
                f"PII-classified field {safe_scalar_summary(member.field_id)!r} "
                "exposes sample values."
            ),
            references=(("member", _render_path(member.location.path)),),
        )
        for member in snapshot.fields
        if member.pii and member.has_sample_values
    ]


def rule_missing_source_policy_hints(snapshot: LintSnapshot) -> list[LintFinding]:
    """SAL006: source bindings missing catalog fingerprint policy hints."""
    return [
        LintFinding(
            code="SAL006",
            path=location.path,
            mark=location.mark,
            message="A source binding is missing its catalog fingerprint policy hint.",
            references=(("member", _render_path(location.path)),),
        )
        for location in snapshot.missing_source_hints
    ]


def rule_conflicting_term_mappings(snapshot: LintSnapshot) -> list[LintFinding]:
    """SAL007: business terms mapped to multiple stored values."""
    values: dict[str, set[str]] = defaultdict(set)
    locations: dict[str, list[tuple[str | int, ...]]] = defaultdict(list)
    marks: dict[str, AuthoringSourceMark | None] = {}
    for mapping in snapshot.mappings:
        for term, value in mapping.values_by_term:
            values[term].add(value)
            rendered = _render_path(mapping.location.path)
            if rendered not in (_render_path(path) for path in locations[term]):
                locations[term].append(mapping.location.path)
                marks[term] = mapping.location.mark
    findings: list[LintFinding] = []
    for term in sorted(values):
        if len(values[term]) < 2:
            continue
        paths = sorted(locations[term], key=_sort_key)
        findings.append(
            LintFinding(
                code="SAL007",
                path=paths[0],
                mark=marks[term],
                message=(
                    f"Business term {safe_scalar_summary(term)!r} maps to "
                    f"{len(values[term])} distinct stored values."
                ),
                references=tuple(
                    ("member", _render_path(path)) for path in paths[:MAX_RULE_REFERENCES]
                ),
            )
        )
    return findings


def rule_orphan_like_references(snapshot: LintSnapshot) -> list[LintFinding]:
    """SAL008: grains never referenced by any measure attribute."""
    measure_fields = {measure.field_id for measure in snapshot.measures}
    return [
        LintFinding(
            code="SAL008",
            path=grain.location.path,
            mark=grain.location.mark,
            message=(
                f"Grain {safe_scalar_summary(grain.grain_id)!r} is not referenced "
                "by any measure attribute."
            ),
            references=(("member", _render_path(grain.location.path)),),
        )
        for grain in snapshot.grains
        if not grain.attributes.intersection(measure_fields)
    ]


def rule_calculated_field_metadata(snapshot: LintSnapshot) -> list[LintFinding]:
    """SAL009: strict zero-division calculated fields without explanation."""
    return [
        LintFinding(
            code="SAL009",
            path=member.location.path,
            mark=member.location.mark,
            message=(
                f"Calculated field {safe_scalar_summary(member.name)!r} uses a "
                "strict zero-division policy without explaining it in its "
                "description."
            ),
            references=(("member", _render_path(member.location.path)),),
        )
        for member in snapshot.calculated_fields
        if member.has_division
        and member.zero_division_policy == "error"
        and is_missing_description(member.description)
    ]


def _verification_findings(
    verification: LintVerificationView | None,
) -> list[LintFinding]:
    if verification is None:
        return [
            LintFinding(
                code="SAL010",
                path=(),
                message="The assembly does not declare a verification plan.",
            )
        ]
    findings: list[LintFinding] = []
    if verification.enabled_smoke_cases == 0 or verification.enabled_semantic_cases == 0:
        findings.append(
            LintFinding(
                code="SAL010",
                path=verification.location.path,
                mark=verification.location.mark,
                message=(
                    "The verification plan lacks enabled smoke or semantic "
                    "contract cases."
                ),
                references=(("member", _render_path(verification.location.path)),),
            )
        )
    if verification.enabled_cases_without_capabilities:
        findings.append(
            LintFinding(
                code="SAL011",
                path=verification.location.path,
                mark=verification.location.mark,
                message=(
                    f"{verification.enabled_cases_without_capabilities} enabled "
                    "verification cases declare no executor capability "
                    "requirements."
                ),
                references=(("member", _render_path(verification.location.path)),),
            )
        )
    return findings


def rule_verification_readiness(snapshot: LintSnapshot) -> list[LintFinding]:
    """SAL010/SAL011: verification-plan readiness gaps."""
    return _verification_findings(snapshot.verification)


#: Deterministic built-in rule pipeline.
BUILTIN_RULES = (
    rule_duplicate_business_labels,
    rule_missing_descriptions,
    rule_placeholder_descriptions,
    rule_sensitive_masking_gaps,
    rule_sensitive_sample_values,
    rule_missing_source_policy_hints,
    rule_conflicting_term_mappings,
    rule_orphan_like_references,
    rule_calculated_field_metadata,
    rule_verification_readiness,
)


def run_builtin_rules(snapshot: LintSnapshot) -> list[LintFinding]:
    """Run every built-in rule over one snapshot."""
    findings: list[LintFinding] = []
    for rule in BUILTIN_RULES:
        findings.extend(rule(snapshot))
    return [finding for finding in findings if bounded_message(finding.message)]
