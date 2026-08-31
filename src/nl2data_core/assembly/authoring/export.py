"""Deterministic semantic-only YAML export for authoring models and drafts."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import yaml
from pydantic import ValidationError

from nl2data_core.assembly.models import (
    AssemblyDraft,
    AssemblyState,
    AssertionProvenanceKind,
    AssertionType,
    ReviewState,
)

from .diagnostics import AuthoringDiagnostic, AuthoringExportResult
from .lowering import lower_authoring
from .models import AUTHORING_API_VERSION, AUTHORING_KIND, SemanticAssemblyAuthoring


class _AuthoringDumper(yaml.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:
        return True


def _quoted_string(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style='"')


_AuthoringDumper.add_representer(str, _quoted_string)


def _sorted_payload(model: SemanticAssemblyAuthoring) -> dict[str, Any]:
    payload = model.authoring_payload()
    spec = payload["spec"]
    spec["entities"] = sorted(spec["entities"], key=lambda item: item["entityId"])
    for entity in spec["entities"]:
        entity["fields"] = sorted(entity.get("fields", []), key=lambda item: item["fieldId"])
        entity["relationships"] = sorted(
            entity.get("relationships", []), key=lambda item: item["relationshipId"]
        )
        entity["calculatedFields"] = sorted(
            entity.get("calculatedFields", []), key=lambda item: item["name"]
        )
        for field in entity["fields"]:
            if "allowedAggregations" in field:
                field["allowedAggregations"] = sorted(field["allowedAggregations"])
    spec["measures"] = sorted(spec.get("measures", []), key=lambda item: item["measureId"])
    spec["grains"] = sorted(spec.get("grains", []), key=lambda item: item["grainId"])
    for grain in spec["grains"]:
        grain["attributes"] = sorted(grain.get("attributes", []))
    spec["sourceReferences"] = sorted(
        spec.get("sourceReferences", []), key=lambda item: item["referenceId"]
    )
    spec["deploymentBindings"] = sorted(
        spec.get("deploymentBindings", []), key=lambda item: item["bindingId"]
    )
    compatibility = spec.get("compatibility", {})
    if "compatible_catalog_fingerprints" in compatibility:
        compatibility["compatible_catalog_fingerprints"] = sorted(
            compatibility["compatible_catalog_fingerprints"]
        )
    return payload


def export_authoring(model: SemanticAssemblyAuthoring) -> AuthoringExportResult:
    """Emit repeatable block YAML with no aliases or implicit string coercion."""
    document = yaml.dump(
        _sorted_payload(model),
        Dumper=_AuthoringDumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=100,
    )
    return AuthoringExportResult(document=document)


def _unsupported() -> AuthoringExportResult:
    diagnostic = AuthoringDiagnostic(
        code="unsupported_export",
        message="The draft cannot be represented losslessly as semantic authoring YAML.",
    )
    return AuthoringExportResult(diagnostics=(diagnostic,), issue_count=1)


def _draft_payload(draft: AssemblyDraft) -> dict[str, Any] | None:
    if (
        draft.state is not AssemblyState.DRAFT
        or draft.draft_revision != 0
        or draft.authoring_source_references is None
        or draft.authoring_compatibility is None
        or any(
            assertion.provenance.kind is not AssertionProvenanceKind.MANUAL
            or assertion.review_state is not ReviewState.PENDING
            or assertion.review_binding is not None
            for assertion in draft.assertions
        )
    ):
        return None

    entities: dict[str, dict[str, Any]] = {}
    fields: dict[str, list[dict[str, Any]]] = defaultdict(list)
    relationships: dict[str, list[dict[str, Any]]] = defaultdict(list)
    calculated_fields: dict[str, list[dict[str, Any]]] = defaultdict(list)
    mappings: dict[tuple[str, str], dict[str, Any]] = {}
    measures: list[dict[str, Any]] = []
    grains: list[dict[str, Any]] = []

    expected_keys = {
        AssertionType.ENTITY: {"descriptor_id", "entity_id", "label", "description"},
        AssertionType.FIELD: {
            "descriptor_id",
            "entity_id",
            "field_id",
            "label",
            "description",
            "data_type",
            "allowed_aggregations",
            "value_semantics",
        },
        AssertionType.MAPPING: {
            "descriptor_id",
            "entity_id",
            "field_id",
            "value_mapping",
            "display_order",
            "sample_values",
            "pii",
            "unknown_value_policy",
        },
        AssertionType.RELATIONSHIP: {
            "descriptor_id",
            "relationship_id",
            "source_entity_id",
            "target_entity_id",
            "label",
            "source_fields",
            "target_fields",
        },
        AssertionType.CALCULATED_FIELD: {
            "descriptor_id",
            "entity_id",
            "name",
            "label",
            "description",
            "expression",
            "output_type",
            "requires",
            "zero_division_policy",
        },
        AssertionType.MEASURE: {
            "descriptor_id",
            "measure_id",
            "field_id",
            "aggregation",
            "label",
            "description",
        },
        AssertionType.GRAIN: {
            "descriptor_id",
            "grain_id",
            "entity_id",
            "attributes",
            "description",
        },
    }
    for assertion in draft.assertions:
        if (
            assertion.type not in expected_keys
            or not set(assertion.payload) <= expected_keys[assertion.type]
        ):
            return None
        payload = dict(assertion.payload)
        if payload.pop("descriptor_id", None) != draft.bundle_id:
            return None
        if assertion.type is AssertionType.ENTITY:
            entity_id = str(payload.pop("entity_id"))
            entities[entity_id] = {"entityId": entity_id, **payload}
        elif assertion.type is AssertionType.FIELD:
            entity_id = str(payload.pop("entity_id"))
            field_id = str(payload.pop("field_id"))
            fields[entity_id].append({"fieldId": field_id, **payload})
        elif assertion.type is AssertionType.MAPPING:
            entity_id = str(payload.pop("entity_id"))
            field_id = str(payload.pop("field_id"))
            mappings[(entity_id, field_id)] = payload
        elif assertion.type is AssertionType.RELATIONSHIP:
            source_id = str(payload.pop("source_entity_id"))
            relationship_id = str(payload.pop("relationship_id"))
            relationships[source_id].append({"relationshipId": relationship_id, **payload})
        elif assertion.type is AssertionType.CALCULATED_FIELD:
            entity_id = str(payload.pop("entity_id"))
            calculated_fields[entity_id].append(payload)
        elif assertion.type is AssertionType.MEASURE:
            measure_id = str(payload.pop("measure_id"))
            measures.append({"measureId": measure_id, **payload})
        elif assertion.type is AssertionType.GRAIN:
            grain_id = str(payload.pop("grain_id"))
            grains.append({"grainId": grain_id, **payload})

    for entity_id, entity in entities.items():
        entity["fields"] = fields.pop(entity_id, [])
        for field in entity["fields"]:
            mapping = mappings.pop((entity_id, field["fieldId"]), None)
            nested = field.get("value_semantics")
            if mapping is not None:
                if nested is not None and nested != mapping:
                    return None
                field["valueSemantics"] = mapping
                field.pop("value_semantics", None)
        entity["relationships"] = relationships.pop(entity_id, [])
        entity["calculatedFields"] = calculated_fields.pop(entity_id, [])
    if fields or relationships or calculated_fields or mappings:
        return None

    return {
        "apiVersion": AUTHORING_API_VERSION,
        "kind": AUTHORING_KIND,
        "metadata": {
            "bundleId": draft.bundle_id,
            "modelVersion": draft.model_version,
            "description": draft.authoring_description or "",
        },
        "spec": {
            "source": {
                "sourceId": draft.source_id,
                **(
                    {"catalogFingerprint": draft.source_snapshot_fingerprint}
                    if draft.source_snapshot_fingerprint is not None
                    else {}
                ),
            },
            "entities": list(entities.values()),
            "measures": measures,
            "grains": grains,
            "sourceReferences": [
                source.model_dump(mode="json", exclude_none=True)
                for source in draft.authoring_source_references
            ],
            "compatibility": draft.authoring_compatibility.model_dump(mode="json"),
            "deploymentBindings": [
                {
                    "bindingId": binding.binding_id,
                    "environment": binding.environment,
                    "sourceId": binding.source_id,
                    "connectionReference": binding.connection_reference,
                }
                for binding in draft.deployment_bindings
            ],
        },
    }


def export_authoring_draft(draft: AssemblyDraft) -> AuthoringExportResult:
    """Export only clean authoring-derived drafts that reconstruct losslessly."""
    payload = _draft_payload(draft)
    if payload is None:
        return _unsupported()
    try:
        model = SemanticAssemblyAuthoring.model_validate(payload)
    except ValidationError:
        return _unsupported()
    lowered = lower_authoring(
        model,
        draft_id=draft.draft_id,
        author_reference=draft.author_reference,
    )
    if lowered.draft is None or [
        (item.id, item.payload_hash()) for item in lowered.draft.assertions
    ] != [(item.id, item.payload_hash()) for item in draft.assertions]:
        return _unsupported()
    return export_authoring(model)
