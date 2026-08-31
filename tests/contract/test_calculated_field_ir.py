"""Contract tests for calculated-field IR gating and the placeholder reservation (v4.2).

Covers D5 (``calculated-fields`` capability gating with fail-closed
``CF_003`` reference resolution), D8 (the reserved
``named_query_placeholder`` extension schema: capability-gated,
structurally validated, zero behavior), and the fingerprint-neutrality of
the reservation (IRs without the extension stay byte-identical).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nl2data_core.ai.models import (
    IntentSelection,
    StructuredIntent,
)
from nl2data_core.ai.plan_builder import build_ir_from_intent
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.planning.ir.fixtures import GOLDEN_FINGERPRINT, golden_ir
from nl2data_core.planning.ir.models import (
    IR_VERSION,
    NAMED_QUERY_PLACEHOLDER_CAPABILITY,
    NAMED_QUERY_PLACEHOLDER_KIND,
    IRExtension,
    IRFilter,
    IRGrouping,
    IROrdering,
    IRProvenance,
    IRSelection,
    NamedQueryPlaceholderExtension,
    SemanticQueryIR,
)
from nl2data_core.planning.ir.validation import validate_ir, verify_ir_fingerprint
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.views.models import ViewProvenance
from nl2data_core.views.projection import (
    ResolvedCalculatedField,
    ResolvedViewEntity,
    ResolvedViewField,
    ResolvedViewProjection,
)


def _ir(**overrides) -> SemanticQueryIR:
    """A minimal valid rows IR with the given overrides applied."""
    payload: dict = {
        "ir_id": "ir-test",
        "source_id": "src",
        "root_entity_id": "orders",
        "selections": (IRSelection(selection_id="s1", field_id="revenue"),),
        "limit": 100,
        "provenance": IRProvenance(source_id="src", root_entity_id="orders"),
        "required_capabilities": (),
        "extensions": (),
    }
    payload.update(overrides)
    return SemanticQueryIR.model_validate(payload)


def _view(**overrides) -> AuthorizedView:
    payload: dict = {
        "source_id": "src",
        "root_entity_ids": frozenset({"orders"}),
        "field_ids": frozenset({"revenue", "region"}),
    }
    payload.update(overrides)
    return AuthorizedView.model_validate(payload)


class TestCalculatedFieldReferenceGating:
    """D5: a CF-referencing selection carries the capability; CF_003 fail-closed."""

    def test_cf_selection_without_capability_is_rejected(self) -> None:
        ir = _ir(selections=(IRSelection(selection_id="s1", field_id="margin"),))
        result = validate_ir(ir, calculated_field_ids=frozenset({"margin"}))
        assert not result.valid
        assert result.issue_codes() == ["calculated_fields_capability_missing"]

    def test_cf_selection_with_capability_is_valid(self) -> None:
        ir = _ir(
            selections=(IRSelection(selection_id="s1", field_id="margin"),),
            required_capabilities=("calculated-fields",),
        )
        result = validate_ir(ir, calculated_field_ids=frozenset({"margin"}))
        assert result.valid, result.issue_codes()

    def test_view_declared_names_gate_the_capability(self) -> None:
        view = _view(calculated_field_ids=frozenset({"margin"}))
        ir = _ir(selections=(IRSelection(selection_id="s1", field_id="margin"),))
        result = validate_ir(ir, view=view)
        assert not result.valid
        assert "calculated_fields_capability_missing" in result.issue_codes()

    def test_builder_adds_calculated_fields_capability(self) -> None:
        intent = StructuredIntent(
            intent_id="i1",
            request_id="r1",
            source_id="src",
            root_entity_id="orders",
            selections=(
                IntentSelection(selection_id="s1", field_id="margin"),
                IntentSelection(selection_id="s2", field_id="region"),
            ),
            limit=100,
        )
        ir = build_ir_from_intent(intent, calculated_field_ids=frozenset({"margin"}))
        assert "calculated-fields" in ir.required_capabilities
        assert validate_ir(ir, calculated_field_ids=frozenset({"margin"})).valid

    def test_builder_without_declared_set_is_unchanged(self) -> None:
        intent = StructuredIntent(
            intent_id="i1",
            request_id="r1",
            source_id="src",
            root_entity_id="orders",
            selections=(IntentSelection(selection_id="s1", field_id="revenue"),),
            limit=100,
        )
        ir = build_ir_from_intent(intent)
        assert "calculated-fields" not in ir.required_capabilities
        assert validate_ir(ir).valid

    def test_undeclared_reference_fails_closed_with_cf_003(self) -> None:
        view = _view(calculated_field_ids=frozenset({"margin"}))
        ir = _ir(selections=(IRSelection(selection_id="s1", field_id="mystery"),))
        result = validate_ir(ir, view=view)
        assert not result.valid
        assert "CF_003" in result.issue_codes()

    def test_undeclared_reference_without_a_view_is_structurally_unknown(self) -> None:
        """Without a view the declared field universe is unknowable, so a
        selection that is not a declared calculated field stays valid; the
        governed path always supplies the view, which produces ``CF_003``."""
        ir = _ir(selections=(IRSelection(selection_id="s1", field_id="mystery"),))
        result = validate_ir(ir, calculated_field_ids=frozenset({"margin"}))
        assert result.valid
        assert result.issue_codes() == []

    def test_legacy_out_of_scope_code_without_declared_set(self) -> None:
        """No declared calculated names: legacy ``field_out_of_scope`` stands."""
        view = _view()
        ir = _ir(selections=(IRSelection(selection_id="s1", field_id="mystery"),))
        result = validate_ir(ir, view=view)
        assert not result.valid
        assert result.issue_codes() == ["field_out_of_scope"]

    def test_names_resolve_unambiguously_in_a_mixed_view(self) -> None:
        """D4 no-collision: field ids and calculated names are disjoint sets."""
        view = _view(calculated_field_ids=frozenset({"margin"}))
        assert not view.contains_field("margin")
        assert view.contains_calculated_field("margin")
        assert not view.contains_calculated_field("revenue")
        ir = _ir(
            selections=(
                IRSelection(selection_id="s1", field_id="margin"),
                IRSelection(selection_id="s2", field_id="revenue"),
            ),
            required_capabilities=("calculated-fields",),
        )
        assert validate_ir(ir, view=view).valid

    def test_projection_rejects_name_collision(self) -> None:
        with pytest.raises(ValidationError, match="calculated_field_ids"):
            _projection(calculated_field_ids=frozenset({"revenue"}))


class TestOnlySelectionsMayReferenceCalculatedFields:
    """Filters, groupings, and orderings referencing a CF fail closed (CF_003)."""

    def test_filter_reference_is_rejected(self) -> None:
        ir = _ir(
            filters=(
                IRFilter(filter_id="f1", field_id="margin", operator="eq", value="x"),
            ),
            required_capabilities=("calculated-fields",),
        )
        result = validate_ir(ir, calculated_field_ids=frozenset({"margin"}))
        assert not result.valid
        assert "CF_003" in result.issue_codes()

    def test_grouping_reference_is_rejected(self) -> None:
        ir = _ir(
            selections=(
                IRSelection(selection_id="s1", field_id="revenue", aggregation="sum"),
                IRSelection(selection_id="s2", field_id="margin"),
            ),
            groupings=(IRGrouping(grouping_id="g1", field_id="margin"),),
            required_capabilities=("aggregation", "calculated-fields"),
        )
        result = validate_ir(ir, calculated_field_ids=frozenset({"margin"}))
        assert not result.valid
        assert "CF_003" in result.issue_codes()

    def test_ordering_reference_is_rejected(self) -> None:
        ir = _ir(
            selections=(IRSelection(selection_id="s1", field_id="margin"),),
            orderings=(IROrdering(ordering_id="o1", field_id="margin"),),
            required_capabilities=("calculated-fields", "ordering"),
        )
        result = validate_ir(ir, calculated_field_ids=frozenset({"margin"}))
        assert not result.valid
        assert "CF_003" in result.issue_codes()


class TestPlaceholderReservation:
    """D8: the reserved placeholder kind is schema-validated and capability-gated."""

    def test_kind_and_capability_constants(self) -> None:
        assert NAMED_QUERY_PLACEHOLDER_KIND == "named_query_placeholder"
        assert NAMED_QUERY_PLACEHOLDER_CAPABILITY == "named-query-placeholders"

    def test_placeholder_without_capability_is_rejected(self) -> None:
        ir = _ir(
            extensions=(
                IRExtension(
                    extension_id="e1",
                    kind=NAMED_QUERY_PLACEHOLDER_KIND,
                    payload={"query_ref": "q1", "parameters": []},
                ),
            ),
        )
        result = validate_ir(ir)
        assert not result.valid
        assert result.issue_codes() == ["unsupported_extension"]

    def test_valid_placeholder_payload_constructs(self) -> None:
        ir = _ir(
            extensions=(
                IRExtension(
                    extension_id="e1",
                    kind=NAMED_QUERY_PLACEHOLDER_KIND,
                    payload={
                        "query_ref": "q1",
                        "parameters": [
                            {"name": "region", "scalar_type": "str", "required": True},
                            {"name": "limit", "scalar_type": "int"},
                        ],
                    },
                ),
            ),
        )
        assert verify_ir_fingerprint(ir)

    def test_invalid_placeholder_payload_fails_construction(self) -> None:
        with pytest.raises(ValidationError, match="named_query_placeholder"):
            _ir(
                extensions=(
                    IRExtension(
                        extension_id="e1",
                        kind=NAMED_QUERY_PLACEHOLDER_KIND,
                        payload={
                            "query_ref": "q1",
                            "parameters": [{"name": "x", "scalar_type": "bytes"}],
                        },
                    ),
                ),
            )
        with pytest.raises(ValidationError):
            _ir(
                extensions=(
                    IRExtension(
                        extension_id="e1",
                        kind=NAMED_QUERY_PLACEHOLDER_KIND,
                        payload={"query_ref": "not an id!", "parameters": []},
                    ),
                ),
            )
        with pytest.raises(ValidationError):
            _ir(
                extensions=(
                    IRExtension(
                        extension_id="e1",
                        kind=NAMED_QUERY_PLACEHOLDER_KIND,
                        payload={
                            "query_ref": "q1",
                            "parameters": [
                                {"name": "dup", "scalar_type": "str"},
                                {"name": "dup", "scalar_type": "int"},
                            ],
                        },
                    ),
                ),
            )

    def test_placeholder_schema_model_round_trips(self) -> None:
        extension = NamedQueryPlaceholderExtension.validate_payload(
            {"query_ref": "q1", "parameters": [{"name": "region", "scalar_type": "str"}]}
        )
        assert extension.query_ref == "q1"
        assert extension.parameters[0].required is True


class TestReservationIsFingerprintNeutral:
    """The reservation changes nothing for IRs that do not carry it."""

    def test_ir_version_is_unchanged(self) -> None:
        assert IR_VERSION == 1
        assert golden_ir().ir_version == 1

    def test_golden_fingerprint_is_byte_identical(self) -> None:
        ir = golden_ir()
        assert ir.fingerprint == GOLDEN_FINGERPRINT
        assert verify_ir_fingerprint(ir)

    def test_projection_without_calculated_fields_omits_the_member(self) -> None:
        """N6 on the projection: unset members never enter the fingerprint."""
        projection = _projection()
        assert "calculated_field_ids" not in projection.canonical_payload()
        assert projection.calculated_field_ids == frozenset()
        projection = _projection(calculated_field_ids=frozenset({"margin"}))
        assert projection.canonical_payload()["calculated_field_ids"] == ["margin"]

    def test_projection_anchors_calculated_field_content_hash(self) -> None:
        projection = _projection(
            calculated_field_ids=frozenset({"margin"}),
            calculated_fields=(
                ResolvedCalculatedField(
                    name="margin",
                    label="Margin",
                    output_type="float",
                    content_hash=_fingerprint(),
                ),
            ),
        )
        assert projection.canonical_payload()["calculated_fields"][0][
            "content_hash"
        ] == _fingerprint()


# -- helpers ----------------------------------------------------------------


def _fingerprint() -> str:
    return sha256_fingerprint({"test": True})


def _entity() -> ResolvedViewEntity:
    return ResolvedViewEntity(
        entity_id="orders",
        label="Orders",
        fields=(
            ResolvedViewField(field_id="revenue", label="Revenue", data_type="int"),
        ),
    )


def _projection(**overrides) -> ResolvedViewProjection:
    payload: dict = {
        "view_id": "views_orders",
        "view_version": 1,
        "descriptor_id": "orders_descriptor",
        "source_id": "src",
        "root_entity_ids": frozenset({"orders"}),
        "field_ids": frozenset({"revenue"}),
        "entities": (_entity(),),
        "provenance": ViewProvenance(
            descriptor_fingerprint=_fingerprint(),
            policy_decision_fingerprint=_fingerprint(),
            resolver_version=1,
        ),
    }
    payload.update(overrides)
    return ResolvedViewProjection.model_validate(payload)
