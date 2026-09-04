"""Closed registry of governance policy templates for authoring documents.

Policy templates are authoring syntax sugar only (DDS-020 ADR-038): a
declaration expands into an ordinary pending policy assertion during
lowering, before review.  The ``template`` reference and the raw parameter
mapping never enter the canonical payload or the fingerprint domain; the
expanded payload carries resolved policy semantics (``policy_kind`` plus
typed parameters) under the existing type-specific policy identity rules.

The registry is closed code, matching the repository's fail-closed
whitelist posture: documents, configuration, and host input cannot add
templates.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from nl2data_core.canonical import strict_blake2b_16_digest

if TYPE_CHECKING:  # pragma: no cover - import cycle guard for typing only
    from .models import SemanticAssemblyAuthoring

#: Bounded declaration and parameter limits for the authoring policies section.
MAX_POLICY_DECLARATIONS = 64
MAX_POLICY_PARAM_ENTRIES = 8
MAX_POLICY_LIST_ITEMS = 256
MAX_POLICY_SCALAR_CHARS = 1_024
MAX_POLICY_PARAM_KEY_CHARS = 64

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$")
_PARAM_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_IDENTIFIER_LENGTH = 128

#: Parameter keys that would smuggle lifecycle, credential, fingerprint, or
#: executable content into the semantic-only authoring surface.
_FORBIDDEN_POLICY_PARAM_KEYS = frozenset(
    {
        "fingerprint",
        "fingerprints",
        "sha256",
        "status",
        "state",
        "lifecycle",
        "review",
        "review_state",
        "reviewstate",
        "review_binding",
        "approval",
        "approved",
        "approver",
        "binding",
        "revision",
        "provenance",
        "audit",
        "evidence",
        "credential",
        "credentials",
        "secret",
        "password",
        "passwd",
        "token",
        "api_key",
        "apikey",
        "connection",
        "connection_reference",
        "dsn",
        "table",
        "column",
        "physical_name",
        "physicalname",
        "native_value",
        "nativevalue",
        "query",
        "sql",
        "script",
        "command",
        "expression",
    }
)


def _policy_scalar_error(value: Any) -> str | None:
    if isinstance(value, (bool, int)):
        return None
    if isinstance(value, float):
        return None if math.isfinite(value) else "policy parameter numbers must be finite"
    if isinstance(value, str):
        if len(value) > MAX_POLICY_SCALAR_CHARS:
            return "policy parameter strings are too long"
        if _FINGERPRINT_PATTERN.fullmatch(value) is not None:
            return "policy parameters cannot carry fingerprints"
        return None
    if value is None:
        return None
    return "policy parameters must be JSON-compatible scalars"


def normalize_policy_parameters(value: Mapping[str, Any]) -> dict[str, Any]:
    """Model-level generic normalization of a policy parameter mapping.

    Rejects reserved keys, non-scalar values, fingerprint-shaped strings,
    and oversized scalars or lists.  Registry-specific kinds and bounds are
    enforced later by :func:`expand_policy_templates`.
    """
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if (
            not isinstance(key, str)
            or _PARAM_KEY_PATTERN.fullmatch(key) is None
        ):
            raise ValueError("policy parameter names must be bounded snake_case identifiers")
        if key in _FORBIDDEN_POLICY_PARAM_KEYS:
            raise ValueError("policy parameters cannot carry lifecycle or credential content")
        if isinstance(item, dict):
            raise ValueError("policy parameters cannot contain raw policy payloads")
        if isinstance(item, (list, tuple)):
            if len(item) > MAX_POLICY_LIST_ITEMS:
                raise ValueError("policy parameter lists are too long")
            members: list[Any] = []
            for member in item:
                error = _policy_scalar_error(member)
                if error is not None:
                    raise ValueError(error)
                members.append(member)
            normalized[key] = members
            continue
        error = _policy_scalar_error(item)
        if error is not None:
            raise ValueError(error)
        normalized[key] = item
    return normalized


PolicyParameterKind = Literal[
    "identifier",
    "identifier_list",
    "scalar_list",
    "enum",
    "string",
    "entity_field_list",
]
PolicyReferenceKind = Literal["none", "entity", "field"]


@dataclass(frozen=True)
class PolicyParameterSpec:
    """One typed, bounded parameter slot of a policy template."""

    name: str
    kind: PolicyParameterKind
    reference: PolicyReferenceKind = "none"
    min_items: int = 1
    max_items: int = MAX_POLICY_LIST_ITEMS
    max_chars: int = MAX_POLICY_SCALAR_CHARS
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicyTemplateSpec:
    """One closed-registry policy template with its parameter schema."""

    name: str
    parameters: tuple[PolicyParameterSpec, ...]


_TENANT_ISOLATION = PolicyTemplateSpec(
    name="tenant-isolation",
    parameters=(
        PolicyParameterSpec(name="entity", kind="identifier", reference="entity"),
        PolicyParameterSpec(name="field", kind="identifier", reference="field"),
        PolicyParameterSpec(name="claim", kind="identifier"),
    ),
)
_ROW_RESTRICTION = PolicyTemplateSpec(
    name="row-restriction",
    parameters=(
        PolicyParameterSpec(name="entity", kind="identifier", reference="entity"),
        PolicyParameterSpec(name="field", kind="identifier", reference="field"),
        PolicyParameterSpec(
            name="allowed_values",
            kind="scalar_list",
            max_items=256,
        ),
    ),
)
_PURPOSE_GATING = PolicyTemplateSpec(
    name="purpose-gating",
    parameters=(
        PolicyParameterSpec(name="purposes", kind="identifier_list", max_items=16),
        PolicyParameterSpec(name="effect", kind="enum", choices=("allow", "deny")),
    ),
)
_FIELD_MASKING = PolicyTemplateSpec(
    name="field-masking",
    parameters=(
        PolicyParameterSpec(name="fields", kind="entity_field_list", max_items=64),
        PolicyParameterSpec(name="replacement", kind="string"),
    ),
)

#: Closed template registry, keyed by template name.
POLICY_TEMPLATE_SPECS: Mapping[str, PolicyTemplateSpec] = {
    _TENANT_ISOLATION.name: _TENANT_ISOLATION,
    _ROW_RESTRICTION.name: _ROW_RESTRICTION,
    _PURPOSE_GATING.name: _PURPOSE_GATING,
    _FIELD_MASKING.name: _FIELD_MASKING,
}

POLICY_TEMPLATE_NAMES = frozenset(POLICY_TEMPLATE_SPECS)


@dataclass(frozen=True)
class ExpandedPolicy:
    """One fully-resolved policy declaration, ordered by expanded identity."""

    declaration_index: int
    template: str
    policy_id: str
    payload: Mapping[str, Any]


PolicyIssueKind = Literal[
    "unknown_template",
    "unknown_parameter",
    "missing_parameter",
    "invalid_parameter",
    "parameter_bounds",
    "invalid_reference",
    "duplicate_identity",
]


@dataclass(frozen=True)
class PolicyTemplateIssue:
    """One fail-closed expansion failure, relative to one declaration."""

    kind: PolicyIssueKind
    declaration_index: int
    message: str
    path: tuple[str, ...] = ()
    duplicate_index: int | None = None


class PolicyTemplateError(ValueError):
    """Raised when policy template declarations fail closed expansion."""

    def __init__(self, issues: tuple[PolicyTemplateIssue, ...]) -> None:
        super().__init__("policy template declarations are not valid")
        self.issues = issues


def _entity_fields(model: SemanticAssemblyAuthoring) -> dict[str, frozenset[str]]:
    return {
        entity.entity_id: frozenset(field.field_id for field in entity.fields)
        for entity in model.spec.entities
    }


def _identifier_error(value: Any) -> str | None:
    if not isinstance(value, str) or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        return "policy parameter values must be bounded identifiers"
    return None


def _parameter_error(
    spec: PolicyParameterSpec,
    value: Any,
    entity_fields: Mapping[str, frozenset[str]],
) -> tuple[str | None, Any]:
    if spec.kind == "identifier":
        error = _identifier_error(value)
        if error is not None:
            return error, value
        if spec.reference == "entity" and value not in entity_fields:
            return "a policy parameter references an entity that does not exist", value
        if spec.reference == "field":
            entity = None
            for candidate in entity_fields:
                if value in entity_fields[candidate]:
                    entity = candidate
                    break
            if entity is None:
                return "a policy parameter references a field that does not exist", value
        return None, value
    if spec.kind == "enum":
        if value not in spec.choices:
            return "a policy parameter value is not one of the allowed choices", value
        return None, value
    if spec.kind == "string":
        if not isinstance(value, str) or not value or len(value) > spec.max_chars:
            return "a policy parameter must be a bounded non-empty string", value
        return None, value
    if not isinstance(value, (list, tuple)):
        return "a policy parameter must be a bounded list", value
    if not spec.min_items <= len(value) <= spec.max_items:
        return "a policy parameter list is outside the registry bounds", value
    members: list[Any] = []
    for member in value:
        if spec.kind == "scalar_list":
            error = _policy_scalar_error(member)
            if error is not None:
                return error, value
            members.append(member)
            continue
        if spec.kind == "identifier_list":
            error = _identifier_error(member)
            if error is not None:
                return error, value
            members.append(member)
            continue
        # entity_field_list: "entity.field" references must resolve.
        error = _identifier_error(member)
        if error is not None:
            return error, value
        entity, _, field = str(member).rpartition(".")
        if not entity or not field:
            return "a policy parameter entry must reference entity.field", value
        if entity not in entity_fields or field not in entity_fields[entity]:
            return "a policy parameter references an entity.field that does not exist", value
        members.append(member)
    return None, members


def _identity_components(template: str, parameters: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the canonical identifying-target components in stable order."""
    if template in (_TENANT_ISOLATION.name, _ROW_RESTRICTION.name):
        return (parameters["entity"], parameters["field"])
    if template == _PURPOSE_GATING.name:
        return tuple(sorted(set(parameters["purposes"])))
    if template == _FIELD_MASKING.name:
        return tuple(sorted(set(parameters["fields"])))
    raise ValueError(f"unknown policy template: {template}")


def expanded_policy_id(template: str, parameters: Mapping[str, Any]) -> str:
    """Derive the target-derived expanded identity for one declaration (D3).

    The rendered dotted form is used only when it is injective: identifier
    components may themselves contain dots, so a joined render of dot-bearing
    components (always the case for ``field-masking`` entity.field entries)
    could collide across distinct targets.  Ambiguous or over-long renders
    fall back to a bounded digest over a non-identifier separator, keeping
    the identity deterministic, injective, and within the identifier bound.
    """
    if template not in POLICY_TEMPLATE_SPECS:
        raise ValueError(f"unknown policy template: {template}")
    components = _identity_components(template, parameters)
    prefix = f"{template}."
    joined = ".".join(components)
    if (
        len(prefix) + len(joined) <= _MAX_IDENTIFIER_LENGTH
        and all("." not in component for component in components)
    ):
        return prefix + joined
    return f"{template}.{strict_blake2b_16_digest(chr(0).join(components))}"


def expand_policy_templates(
    model: SemanticAssemblyAuthoring,
) -> tuple[ExpandedPolicy, ...]:
    """Expand policy template declarations into resolved policy semantics.

    Pure and presentation-invariant: the result is ordered by expanded
    identity and independent of YAML mapping order, comments, whitespace,
    and anchors.  Raises :class:`PolicyTemplateError` with one issue per
    failure; no partial expansion is produced when any declaration fails.
    """
    declarations = model.spec.policies
    entity_fields = _entity_fields(model)
    issues: list[PolicyTemplateIssue] = []
    expanded: list[ExpandedPolicy] = []
    seen: dict[str, int] = {}
    for index, declaration in enumerate(declarations):
        spec = POLICY_TEMPLATE_SPECS.get(declaration.template)
        if spec is None:
            issues.append(
                PolicyTemplateIssue(
                    kind="unknown_template",
                    declaration_index=index,
                    message="The policy template name is not in the registry.",
                    path=("template",),
                )
            )
            continue
        resolved: dict[str, Any] = {}
        failed = False
        known_names = {parameter.name for parameter in spec.parameters}
        for parameter in spec.parameters:
            if parameter.name not in declaration.parameters:
                issues.append(
                    PolicyTemplateIssue(
                        kind="missing_parameter",
                        declaration_index=index,
                        message="The policy declaration is missing a required parameter.",
                        path=("parameters", parameter.name),
                    )
                )
                failed = True
                continue
            error, value = _parameter_error(
                parameter, declaration.parameters[parameter.name], entity_fields
            )
            if error is not None:
                issues.append(
                    PolicyTemplateIssue(
                        kind=_issue_kind(parameter, error),
                        declaration_index=index,
                        message=error,
                        path=("parameters", parameter.name),
                    )
                )
                failed = True
                continue
            resolved[parameter.name] = value
        for key in sorted(set(declaration.parameters) - known_names):
            issues.append(
                PolicyTemplateIssue(
                    kind="unknown_parameter",
                    declaration_index=index,
                    message="The policy declaration supplies an unknown parameter.",
                    path=("parameters", key),
                )
            )
        if failed:
            continue
        policy_id = expanded_policy_id(spec.name, resolved)
        if policy_id in seen:
            issues.append(
                PolicyTemplateIssue(
                    kind="duplicate_identity",
                    declaration_index=index,
                    message=(
                        "Two policy declarations expand to the same policy identity: "
                        f"{policy_id}."
                    ),
                    duplicate_index=seen[policy_id],
                )
            )
            continue
        seen[policy_id] = index
        payload: dict[str, Any] = {
            "policy_id": policy_id,
            "policy_kind": spec.name,
        }
        for parameter in spec.parameters:
            payload[parameter.name] = resolved[parameter.name]
        expanded.append(
            ExpandedPolicy(
                declaration_index=index,
                template=spec.name,
                policy_id=policy_id,
                payload=payload,
            )
        )
    if issues:
        raise PolicyTemplateError(tuple(issues))
    return tuple(sorted(expanded, key=lambda item: item.policy_id))


def _issue_kind(parameter: PolicyParameterSpec, error: str) -> PolicyIssueKind:
    if error.endswith("that does not exist") or error.endswith("entity.field"):
        return "invalid_reference"
    if error.endswith("outside the registry bounds") or error.endswith("too long"):
        return "parameter_bounds"
    return "invalid_parameter"
