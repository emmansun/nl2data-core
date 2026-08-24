"""Contract tests for the canonical Semantic Query IR (DDS-019).

Covers the IR schema invariants (immutable, versioned, bounded, scalar-only
values, JSON-compatible extensions), canonical JSON serialization with the
golden fixtures, unsupported-feature rejection, and physical-payload
rejection so SQL/MQL, credentials, bindings, and native objects can never
enter the canonical representation.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from nl2data_core.planning.ir.fixtures import (
    GOLDEN_CANONICAL_JSON,
    GOLDEN_FINGERPRINT,
    golden_ir,
)
from nl2data_core.planning.ir.models import (
    IR_VERSION,
    IRExtension,
    IRFilter,
    IRGrouping,
    IRProvenance,
    IRSelection,
    IRTimeContext,
    SemanticQueryIR,
)
from nl2data_core.planning.ir.validation import validate_ir, verify_ir_fingerprint


def _clean_ir(**overrides) -> SemanticQueryIR:
    """The golden IR without extensions, re-validated with the overrides.

    ``model_validate`` re-runs every validator and recomputes the
    fingerprint, unlike ``model_copy`` which bypasses validation.
    """
    payload = {**golden_ir().model_dump(), "extensions": (), **overrides}
    return SemanticQueryIR.model_validate(payload)


class TestIRSchema:
    def test_ir_is_frozen(self) -> None:
        ir = golden_ir()
        with pytest.raises((TypeError, ValidationError)):
            ir.ir_id = "mutated"  # type: ignore[misc]

    def test_ir_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            SemanticQueryIR.model_validate(
                {
                    **golden_ir().model_dump(),
                    "binding": {"object_id": "orders_table"},
                }
            )

    def test_ir_version_is_literal_1(self) -> None:
        assert golden_ir().ir_version == IR_VERSION == 1
        with pytest.raises(ValidationError):
            _clean_ir(ir_version=2)

    def test_collection_sizes_are_bounded(self) -> None:
        with pytest.raises(ValidationError):
            _clean_ir(
                selections=tuple(
                    IRSelection(selection_id=f"s{i}", field_id="region") for i in range(1_001)
                )
            )
        with pytest.raises(ValidationError):
            _clean_ir(
                filters=tuple(
                    IRFilter(filter_id=f"f{i}", field_id="region", operator="eq", value="x")
                    for i in range(1_001)
                )
            )
        with pytest.raises(ValidationError):
            _clean_ir(
                extensions=tuple(
                    IRExtension(extension_id=f"e{i}", kind="k", payload={})
                    for i in range(65)
                )
            )

    def test_limit_is_bounded(self) -> None:
        with pytest.raises(ValidationError):
            _clean_ir(limit=0)
        with pytest.raises(ValidationError):
            _clean_ir(limit=1_000_001)
        assert _clean_ir(limit=None).limit is None  # unbounded is expressible

    def test_filter_values_are_scalar_only(self) -> None:
        with pytest.raises(ValidationError):
            IRFilter(filter_id="f1", field_id="region", operator="eq", value={"nested": 1})
        with pytest.raises(ValidationError):
            IRFilter(filter_id="f1", field_id="created_at", operator="eq", value=datetime.now(UTC))
        with pytest.raises(ValidationError):
            IRFilter(
                filter_id="f1",
                field_id="region",
                operator="in",
                value=("north", {"native": True}),
            )
        #: A tuple/list of scalars is the only non-scalar accepted shape.
        assert IRFilter(
            filter_id="f1", field_id="region", operator="in", value=["north", "south"]
        ).value == ("north", "south")
        with pytest.raises(ValidationError):
            IRFilter(filter_id="f1", field_id="amount", operator="gt", value=math.nan)

    def test_extension_payloads_are_json_compatible(self) -> None:
        with pytest.raises(ValidationError):
            IRExtension(extension_id="e1", kind="k", payload={"native": object()})
        with pytest.raises(ValidationError):
            IRExtension(extension_id="e1", kind="k", payload={1: "non-string-key"})
        assert IRExtension(extension_id="e1", kind="k", payload={"mode": "strict"}).payload == {
            "mode": "strict"
        }

    def test_extension_payloads_reject_physical_content_and_are_immutable(self) -> None:
        with pytest.raises(ValidationError):
            IRExtension(extension_id="e1", kind="k", payload={"sql": "SELECT * FROM orders"})
        with pytest.raises(ValidationError):
            IRExtension(
                extension_id="e1", kind="k", payload={"note": "SELECT * FROM orders"}
            )
        extension = IRExtension(
            extension_id="e1", kind="k", payload={"mode": "strict", "options": ["a"]}
        )
        with pytest.raises(TypeError):
            extension.payload["mode"] = "permissive"
        with pytest.raises(TypeError):
            extension.payload["options"][0] = "b"

    def test_time_context_reference_requires_matching_value_shape(self) -> None:
        with pytest.raises(ValidationError):
            IRTimeContext(context_id="t1", reference="range", value="2026-01-01")
        with pytest.raises(ValidationError):
            IRTimeContext(context_id="t1", reference="as_of", value=("start", "end"))

    def test_undeclared_extension_fails_closed(self) -> None:
        ir = golden_ir().model_copy(
            update={
                "extensions": (
                    IRExtension(extension_id="e9", kind="vector_search", payload={}),
                )
            }
        )
        result = validate_ir(ir)
        assert result.valid is False
        assert "unsupported_extension" in result.issue_codes()

    def test_declared_extension_is_accepted(self) -> None:
        ir = _clean_ir(
            required_capabilities=("aggregation", "grouping", "list_ops", "ordering", "risk"),
            extensions=(IRExtension(extension_id="e1", kind="risk", payload={"mode": "strict"}),),
        )
        assert validate_ir(ir).valid is True

    def test_duplicate_selection_ids_rejected_at_model_level(self) -> None:
        with pytest.raises(ValidationError):
            _clean_ir(
                selections=(
                    IRSelection(selection_id="s1", field_id="region"),
                    IRSelection(selection_id="s1", field_id="status"),
                )
            )

    def test_required_capabilities_are_bounded_identifiers(self) -> None:
        with pytest.raises(ValidationError):
            _clean_ir(required_capabilities=("risk feature",))

    def test_duplicate_filter_ids_rejected_by_validation(self) -> None:
        ir = _clean_ir(
            filters=(
                IRFilter(filter_id="f1", field_id="status", operator="eq", value="shipped"),
                IRFilter(filter_id="f1", field_id="region", operator="eq", value="north"),
            )
        )
        result = validate_ir(ir)
        assert result.valid is False
        assert "duplicate_filter" in result.issue_codes()

    def test_validation_result_carries_ir_identity(self) -> None:
        ir = _clean_ir()
        result = validate_ir(ir)
        assert result.valid is True
        assert result.ir_version == IR_VERSION
        assert result.ir_fingerprint == ir.fingerprint
        assert result.issues == ()


class TestCanonicalSerialization:
    def test_golden_canonical_json_matches(self) -> None:
        assert golden_ir().serialize_canonical() == GOLDEN_CANONICAL_JSON

    def test_golden_fingerprint_matches(self) -> None:
        assert golden_ir().fingerprint == GOLDEN_FINGERPRINT

    def test_round_trip_from_canonical_json(self) -> None:
        loaded = SemanticQueryIR.from_canonical_json(GOLDEN_CANONICAL_JSON)
        assert loaded.fingerprint == GOLDEN_FINGERPRINT
        assert loaded.serialize_canonical() == GOLDEN_CANONICAL_JSON

    def test_list_values_normalize_to_tuple(self) -> None:
        payload = json.loads(GOLDEN_CANONICAL_JSON)
        payload["filters"][1]["value"] = ["north", "south"]  # list instead of tuple
        ir = SemanticQueryIR.model_validate(payload)
        assert isinstance(ir.filters[1].value, tuple)
        assert ir.fingerprint == GOLDEN_FINGERPRINT

    def test_serialization_is_mapping_order_independent(self) -> None:
        payload = json.loads(GOLDEN_CANONICAL_JSON)
        reordered = dict(reversed(list(payload.items())))
        assert SemanticQueryIR.model_validate(reordered).serialize_canonical() == (
            GOLDEN_CANONICAL_JSON
        )

    def test_tampered_fingerprint_cannot_be_trusted(self) -> None:
        tampered = golden_ir().model_copy(update={"fingerprint": "sha256:" + "0" * 64})
        assert tampered.fingerprint != GOLDEN_FINGERPRINT
        assert verify_ir_fingerprint(tampered) is False
        assert verify_ir_fingerprint(golden_ir()) is True

    def test_from_canonical_json_recomputes_fingerprint(self) -> None:
        payload = json.loads(GOLDEN_CANONICAL_JSON)
        payload["fingerprint"] = "sha256:" + "0" * 64
        ir = SemanticQueryIR.from_canonical_json(json.dumps(payload))
        assert ir.fingerprint == GOLDEN_FINGERPRINT  # altered input never trusted

    def test_fingerprint_changes_with_semantic_change(self) -> None:
        assert _clean_ir(limit=101).fingerprint != GOLDEN_FINGERPRINT
        assert _clean_ir(limit=101).serialize_canonical() != GOLDEN_CANONICAL_JSON

    def test_provenance_is_part_of_the_payload(self) -> None:
        ir = _clean_ir(
            provenance=IRProvenance(
                source_id="acme_warehouse",
                root_entity_id="orders",
                catalog_fingerprint="sha256:" + "ff" * 32,
                policy_view_fingerprint="sha256:" + "ee" * 32,
            )
        )
        assert ir.fingerprint != GOLDEN_FINGERPRINT
        assert "ff" * 32 in ir.serialize_canonical()


class TestUnsupportedFeatures:
    def test_sql_syntax_rejected_in_identifiers(self) -> None:
        with pytest.raises(ValidationError):
            IRSelection(selection_id="s1", field_id="SELECT * FROM orders")
        with pytest.raises(ValidationError):
            IRFilter(filter_id="f1", field_id="region; DROP TABLE", operator="eq", value="x")
        with pytest.raises(ValidationError):
            IRGrouping(grouping_id="g1", field_id="total_amount UNION SELECT 1")

    def test_native_object_values_rejected(self) -> None:
        class Native:
            pass

        with pytest.raises(ValidationError):
            IRFilter(filter_id="f1", field_id="region", operator="eq", value=Native())

    def test_unbounded_limit_reported_when_required(self) -> None:
        result = validate_ir(_clean_ir(limit=None))
        assert result.valid is False
        assert "unbounded_limit" in result.issue_codes()
        #: The same IR passes when boundedness is not required.
        assert validate_ir(_clean_ir(limit=None), require_bounded=False).valid is True

    def test_physical_binding_rejected_as_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            SemanticQueryIR.model_validate(
                {
                    **golden_ir().model_dump(),
                    "binding": {
                        "object_id": "orders_table",
                        "dialect": "sqlite",
                        "column_bindings": [],
                    },
                }
            )

    def test_invalid_operator_rejected_by_validation(self) -> None:
        ir = _clean_ir(
            filters=(IRFilter(filter_id="f1", field_id="status", operator="eq", value=()),),
        )
        result = validate_ir(ir)
        assert result.valid is False
        assert "invalid_filter_value" in result.issue_codes()

    def test_result_shape_mismatch_reported(self) -> None:
        ir = _clean_ir(result_shape={"kind": "rows"})
        result = validate_ir(ir)
        assert result.valid is False
        assert "result_shape_mismatch" in result.issue_codes()


class TestPhysicalPayloadRejection:
    def test_golden_json_contains_no_physical_content(self) -> None:
        for forbidden in (
            "orders_table",
            "sqlite",
            "SELECT",
            "$match",
            "password",
            "mongodb",
            "db_password",
            "uri",
        ):
            assert forbidden not in GOLDEN_CANONICAL_JSON, forbidden
            assert forbidden not in golden_ir().serialize_canonical(), forbidden

    def test_ir_has_no_physical_fields(self) -> None:
        assert "binding" not in SemanticQueryIR.model_fields
        assert "sql" not in SemanticQueryIR.model_fields
        assert "mql" not in SemanticQueryIR.model_fields
        assert "credentials" not in SemanticQueryIR.model_fields
        assert "artifact" not in SemanticQueryIR.model_fields
