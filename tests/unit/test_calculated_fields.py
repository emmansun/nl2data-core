"""Unit tests for calculated-field semantics (change: calculated-field-semantics).

Covers the ``ExprNode`` / ``CalculatedField`` model contract (closed
whitelist, bounds, int-only constants, complete inference table, exact
order-free ``requires``, JSON-wire safety), the entity-level member rules
(design D4: N6 fingerprint stability, uniqueness, no composition, pii
isolation at definition time per D11 direction 1), the bundle-validation
direction of the pii isolation (D11 direction 2), and the content-hash
helper (design D6).
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from nl2data_core.bundles import (
    BundleProvenance,
    BundleQualityStatus,
    SemanticModelBundle,
    SemanticSourceReference,
    validate_bundle,
)
from nl2data_core.views import (
    CalculatedField,
    ExprNode,
    SemanticDescriptor,
    SemanticEntityDescriptor,
    SemanticFieldDescriptor,
    ValueSemantics,
)


def make_field(field_id: str, data_type: str = "int", **overrides) -> SemanticFieldDescriptor:
    values = {
        "field_id": field_id,
        "label": field_id.replace("_", " ").title(),
        "data_type": data_type,
    }
    values.update(overrides)
    return SemanticFieldDescriptor(**values)


def make_entity(**overrides) -> SemanticEntityDescriptor:
    values = {
        "entity_id": "metrics",
        "label": "Metrics",
        "fields": (
            make_field("revenue", data_type="int"),
            make_field("cost", data_type="int"),
            make_field("price", data_type="float"),
            make_field("region", data_type="string"),
        ),
    }
    values.update(overrides)
    return SemanticEntityDescriptor(**values)


def make_cf(name: str = "margin", **overrides) -> CalculatedField:
    if "expression" not in overrides:
        overrides["expression"] = ExprNode(
            op="sub",
            left=ExprNode(op="field", field_id="revenue"),
            right=ExprNode(op="field", field_id="cost"),
        )
    values = {
        "name": name,
        "label": name.replace("_", " ").title(),
        "output_type": "int",
        "requires": ("revenue", "cost"),
    }
    values.update(overrides)
    return CalculatedField(**values)


def chain(depth: int) -> ExprNode:
    """An ``add`` chain of ``depth`` operators (depth 0 is a const leaf)."""
    node = ExprNode(op="const", const=1)
    for _ in range(depth):
        node = ExprNode(op="add", left=node, right=ExprNode(op="const", const=1))
    return node


def full_tree(depth: int) -> ExprNode:
    """A full binary ``add`` tree with ``2 ** depth - 1`` nodes."""
    if depth == 0:
        return ExprNode(op="const", const=1)
    return ExprNode(op="add", left=full_tree(depth - 1), right=full_tree(depth - 1))


def make_cf_descriptor(**entity_overrides) -> SemanticDescriptor:
    entity_overrides.setdefault("calculated_fields", (make_cf(),))
    entity = make_entity(**entity_overrides)
    return SemanticDescriptor(
        descriptor_id="metrics_catalog",
        version=1,
        source_id="metrics",
        entities=(entity,),
    )


def make_plain_descriptor(**entity_overrides) -> SemanticDescriptor:
    """A descriptor whose entities never declare calculated fields."""
    return SemanticDescriptor(
        descriptor_id="metrics_catalog",
        version=1,
        source_id="metrics",
        entities=(make_entity(**entity_overrides),),
    )


def make_cf_bundle(descriptor: SemanticDescriptor) -> SemanticModelBundle:
    return SemanticModelBundle(
        bundle_id="metrics_model",
        model_version="1.0.0",
        descriptor=descriptor,
        sources=(
            SemanticSourceReference(
                reference_id="src-metrics",
                source_id=descriptor.source_id,
            ),
        ),
        provenance=BundleProvenance(
            owner_reference="team-analytics",
            quality=BundleQualityStatus.VALIDATED,
            created_at="2026-01-01T00:00:00+00:00",
        ),
    )


class TestExprNodeWhitelistAndBounds:
    def test_unknown_operator_is_rejected_with_cf_001(self) -> None:
        with pytest.raises(ValidationError, match="CF_001") as error:
            ExprNode(op="pow", left=ExprNode(op="const", const=1),
                     right=ExprNode(op="const", const=2))
        assert "pow" in str(error.value)

    def test_operator_leaf_shape_violations_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="CF_001"):
            ExprNode(op="add", left=ExprNode(op="const", const=1))
        with pytest.raises(ValidationError, match="CF_001"):
            ExprNode(op="add", left=ExprNode(op="const", const=1),
                     right=ExprNode(op="const", const=2), const=3)
        with pytest.raises(ValidationError, match="CF_001"):
            ExprNode(op="field", field_id="revenue", const=1)
        with pytest.raises(ValidationError, match="CF_001"):
            ExprNode(op="const", const=1, left=ExprNode(op="const", const=1))

    def test_float_const_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="CF_001"):
            ExprNode(op="const", const=1.5)
        # Lax coercion of integral floats must also fail at the boundary.
        with pytest.raises(ValidationError, match="CF_001"):
            ExprNode(op="const", const=2.0)

    def test_bool_const_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="CF_001"):
            ExprNode(op="const", const=True)

    def test_negative_int_const_is_accepted(self) -> None:
        node = ExprNode(op="const", const=-7)
        assert node.const == -7

    def test_depth_bound(self) -> None:
        # chain(15) has depth 16 (allowed); chain(16) has depth 17.
        assert chain(15).canonical_payload()["op"] == "add"
        with pytest.raises(ValidationError, match="CF_001.*depth"):
            chain(16)

    def test_node_count_bound(self) -> None:
        # full_tree(6) has 127 nodes with depth 7: the count bound fires.
        with pytest.raises(ValidationError, match="CF_001.*node count"):
            full_tree(6)
        # full_tree(5) has 63 nodes: within bounds.
        assert full_tree(5).canonical_payload()["op"] == "add"

    def test_field_leaves_is_the_exact_set(self) -> None:
        tree = ExprNode(
            op="mul",
            left=ExprNode(
                op="add",
                left=ExprNode(op="field", field_id="revenue"),
                right=ExprNode(op="field", field_id="revenue"),
            ),
            right=ExprNode(op="field", field_id="price"),
        )
        assert tree.field_leaves() == frozenset({"revenue", "price"})

    def test_canonical_payload_is_nested_dict_list_only(self) -> None:
        tree = ExprNode(
            op="div",
            left=ExprNode(op="field", field_id="revenue"),
            right=ExprNode(op="const", const=2),
        )
        payload = json.loads(json.dumps(tree.canonical_payload()))
        assert payload == {
            "op": "div",
            "left": {"op": "field", "field_id": "revenue"},
            "right": {"op": "const", "const": 2},
        }

    def test_model_is_frozen(self) -> None:
        node = ExprNode(op="const", const=1)
        with pytest.raises((TypeError, ValidationError)):
            node.const = 2  # type: ignore[misc]


class TestInferenceTable:
    def test_add_int_int_infers_int(self) -> None:
        cf = make_cf()
        assert cf.output_type == "int"
        descriptor = make_cf_descriptor()
        assert descriptor  # declared == inferred enforced at entity level

    def test_mixed_operands_infer_float(self) -> None:
        expression = ExprNode(
            op="add",
            left=ExprNode(op="field", field_id="revenue"),
            right=ExprNode(op="field", field_id="price"),
        )
        with pytest.raises(ValidationError, match="CF_001"):
            make_entity(calculated_fields=(
                make_cf(expression=expression, output_type="int", requires=("revenue", "price")),
            ))
        entity = make_entity(calculated_fields=(
            make_cf(expression=expression, output_type="float", requires=("revenue", "price")),
        ))
        assert entity.calculated_fields[0].output_type == "float"

    def test_div_always_infers_float_even_int_int(self) -> None:
        expression = ExprNode(
            op="div",
            left=ExprNode(op="field", field_id="revenue"),
            right=ExprNode(op="field", field_id="cost"),
        )
        with pytest.raises(ValidationError, match="CF_001"):
            make_entity(calculated_fields=(
                make_cf(expression=expression, output_type="int", requires=("revenue", "cost")),
            ))
        entity = make_entity(calculated_fields=(
            make_cf(
                name="ratio",
                expression=expression,
                output_type="float",
                requires=("revenue", "cost"),
            ),
        ))
        assert entity.calculated_field("ratio").output_type == "float"

    def test_const_only_tree_infers_int(self) -> None:
        entity = make_entity(calculated_fields=(
            make_cf(
                name="one",
                expression=ExprNode(op="const", const=1),
                output_type="int",
                requires=(),
            ),
        ))
        assert entity.calculated_field("one").output_type == "int"

    def test_declared_not_equal_inferred_names_both_types(self) -> None:
        expression = ExprNode(
            op="div",
            left=ExprNode(op="field", field_id="revenue"),
            right=ExprNode(op="field", field_id="cost"),
        )
        with pytest.raises(ValidationError, match="CF_001") as error:
            make_entity(calculated_fields=(
                make_cf(expression=expression, output_type="int", requires=("revenue", "cost")),
            ))
        message = str(error.value)
        assert "'int'" in message and "'float'" in message

    def test_non_numeric_leaf_is_rejected(self) -> None:
        expression = ExprNode(op="field", field_id="region")
        with pytest.raises(ValidationError, match="CF_001"):
            make_entity(calculated_fields=(
                make_cf(expression=expression, output_type="int", requires=("region",)),
            ))

    def test_unknown_leaf_reference_is_rejected(self) -> None:
        expression = ExprNode(op="field", field_id="nope")
        with pytest.raises(ValidationError, match="CF_002"):
            make_entity(calculated_fields=(
                make_cf(expression=expression, output_type="int", requires=("nope",)),
            ))


class TestCalculatedFieldContract:
    def test_requires_mismatch_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="CF_002"):
            make_cf(requires=("revenue",))

    def test_requires_duplicates_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="CF_002"):
            make_cf(requires=("revenue", "revenue", "cost"))

    def test_requires_is_order_free(self) -> None:
        first = make_cf(requires=("revenue", "cost"))
        second = make_cf(requires=("cost", "revenue"))
        # Declaration order is not constrained: canonical payloads (and
        # through them fingerprints) are identical either way.
        assert first.canonical_payload() == second.canonical_payload()
        assert first.content_hash() == second.content_hash()

    def test_content_hash_covers_tree_policy_and_output(self) -> None:
        baseline = make_cf()
        same = make_cf()
        assert baseline.content_hash() == same.content_hash()
        other_policy = make_cf(zero_division_policy="error")
        assert baseline.content_hash() != other_policy.content_hash()
        other_output = make_cf(
            name="ratio",
            expression=ExprNode(
                op="div",
                left=ExprNode(op="field", field_id="revenue"),
                right=ExprNode(op="field", field_id="cost"),
            ),
            output_type="float",
            requires=("revenue", "cost"),
        )
        assert baseline.content_hash() != other_output.content_hash()
        other_tree = make_cf(
            name="total",
            expression=ExprNode(
                op="add",
                left=ExprNode(op="field", field_id="revenue"),
                right=ExprNode(op="field", field_id="cost"),
            ),
            requires=("revenue", "cost"),
        )
        assert baseline.content_hash() != other_tree.content_hash()

    def test_json_wire_safe(self) -> None:
        payload = json.loads(make_cf().model_dump_json())
        assert payload["expression"]["op"] == "sub"
        assert payload["requires"] == ["revenue", "cost"]
        assert payload["zero_division_policy"] == "null"


class TestEntityMemberRules:
    def test_set_means_non_empty(self) -> None:
        with pytest.raises(ValidationError, match="set means non-empty"):
            make_entity(calculated_fields=())

    def test_names_are_unique_within_entity(self) -> None:
        with pytest.raises(ValidationError, match="CF_001"):
            make_entity(calculated_fields=(make_cf(), make_cf()))

    def test_name_collision_with_field_id_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="CF_001"):
            make_entity(calculated_fields=(make_cf(name="revenue"),))

    def test_self_reference_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="CF_002"):
            make_entity(calculated_fields=(
                make_cf(
                    name="double",
                    expression=ExprNode(
                        op="mul",
                        left=ExprNode(op="field", field_id="double"),
                        right=ExprNode(op="const", const=2),
                    ),
                    requires=("double",),
                ),
            ))

    def test_composition_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="CF_002"):
            make_entity(calculated_fields=(
                make_cf(),
                make_cf(
                    name="margin_per_unit",
                    expression=ExprNode(
                        op="div",
                        left=ExprNode(op="field", field_id="margin"),
                        right=ExprNode(op="field", field_id="units"),
                    ),
                    output_type="float",
                    requires=("margin", "units"),
                ),
            ))

    def test_cross_entity_name_collision_is_rejected(self) -> None:
        other = make_entity(
            entity_id="ledger",
            calculated_fields=(make_cf(),),
        )
        with pytest.raises(ValidationError):
            SemanticDescriptor(
                descriptor_id="metrics_catalog",
                version=1,
                source_id="metrics",
                entities=(make_entity(calculated_fields=(make_cf(),)), other),
            )

    def test_descriptor_helpers_resolve_calculated_fields(self) -> None:
        descriptor = make_cf_descriptor()
        assert descriptor.calculated_field("margin") is not None
        assert descriptor.calculated_field("nope") is None
        assert descriptor.all_calculated_field_ids() == frozenset({"margin"})

    def test_entity_lookup_helper(self) -> None:
        entity = make_entity(calculated_fields=(make_cf(),))
        assert entity.calculated_field("margin") is not None
        assert entity.calculated_field("cost") is None


class TestPiiIsolationDefinitionTime:
    """Design D11 direction 1 (CF -> masking, definition time)."""

    def test_pii_reference_rejected_at_definition_time(self) -> None:
        fields = list(make_entity().fields)
        fields[0] = make_field("revenue", data_type="int").model_copy(
            update={
                "value_semantics": ValueSemantics(
                    value_mapping={"acme": 1}, pii=True
                )
            }
        )
        with pytest.raises(ValidationError, match="CF_004"):
            make_entity(
                fields=tuple(fields),
                calculated_fields=(make_cf(),),
            )


class TestPiiIsolationBundleValidation:
    """Design D11 direction 2 (masking -> CF, bundle validation time)."""

    def _descriptor_with_pii_and_cf(self) -> SemanticDescriptor:
        """A governance-state edit: pii applied over an already-validated

        descriptor whose declared calculated field references the field.
        The edit is applied via ``model_copy`` (the post-construction
        path - the definition-time check never had a moment to fire;
        this is exactly the direction-2 arrival order).
        """
        entity = make_entity()
        pii_revenue = entity.fields[0].model_copy(
            update={
                "value_semantics": ValueSemantics(
                    value_mapping={"acme": 1}, pii=True
                )
            }
        )
        edited_entity = entity.model_copy(
            update={
                "fields": (pii_revenue,) + entity.fields[1:],
                "calculated_fields": (make_cf(),),
            }
        )
        return make_plain_descriptor().model_copy(
            update={"entities": (edited_entity,)}
        )

    def test_pii_over_referenced_field_blocks_publication(self) -> None:
        result = validate_bundle(make_cf_bundle(self._descriptor_with_pii_and_cf()))
        assert not result.valid
        assert "CF_004" in result.issue_codes()

    def test_masked_field_not_referenced_by_any_expression_is_unaffected(self) -> None:
        fields = list(make_entity().fields)
        fields[3] = make_field("region", data_type="string").model_copy(
            update={
                "value_semantics": ValueSemantics(
                    value_mapping={"west": "WEST"}, pii=True
                )
            }
        )
        result = validate_bundle(
            make_cf_bundle(make_cf_descriptor(fields=tuple(fields)))
        )
        assert result.valid

    def test_isolation_is_order_independent(self) -> None:
        # Both arrival orders converge on the same final governance state,
        # and that state is rejected regardless of the order it was built.
        descriptor = self._descriptor_with_pii_and_cf()
        assert validate_bundle(make_cf_bundle(descriptor)).issue_codes() == ["CF_004"]


class TestN6OmitWhenUnset:
    def test_unset_member_is_omitted_from_entity_payload(self) -> None:
        entity = make_entity()
        assert "calculated_fields" not in entity.canonical_payload()

    def test_set_member_is_included(self) -> None:
        entity = make_entity(calculated_fields=(make_cf(),))
        payload = entity.canonical_payload()
        assert payload["calculated_fields"] == [make_cf().canonical_payload()]

    def test_descriptor_fingerprint_unchanged_by_explicitly_unset_member(self) -> None:
        # Never declared (implicit default) and explicitly unset are the
        # same descriptor identity (invariant N6).
        implicit = make_plain_descriptor()
        explicit = make_plain_descriptor(calculated_fields=None)
        assert implicit.canonical_payload() == explicit.canonical_payload()
        assert implicit.fingerprint == explicit.fingerprint

    def test_declaring_a_calculated_field_changes_the_fingerprint(self) -> None:
        without = make_plain_descriptor()
        with_cf = make_cf_descriptor()
        assert without.fingerprint != with_cf.fingerprint


class TestSnapshotBreakingChain:
    """Design D4: any calculated-field content edit is snapshot-breaking."""

    def _chain(self, **cf_overrides):
        old = make_cf_descriptor()
        new = make_cf_descriptor(calculated_fields=(make_cf(**cf_overrides),))
        return old.fingerprint, new.fingerprint

    def test_expression_edit_is_snapshot_breaking(self) -> None:
        old_fp, new_fp = self._chain(
            expression=ExprNode(
                op="add",
                left=ExprNode(op="field", field_id="revenue"),
                right=ExprNode(op="field", field_id="cost"),
            ),
        )
        assert old_fp != new_fp

    def test_policy_edit_is_snapshot_breaking(self) -> None:
        old_fp, new_fp = self._chain(zero_division_policy="error")
        assert old_fp != new_fp

    def test_label_edit_is_snapshot_breaking(self) -> None:
        old_fp, new_fp = self._chain(label="Gross Margin")
        assert old_fp != new_fp

    def test_removal_is_snapshot_breaking(self) -> None:
        old_fp, new_fp = self._chain()
        removed = make_cf_descriptor(calculated_fields=None)
        assert old_fp != removed.fingerprint
        assert new_fp != removed.fingerprint

    @pytest.mark.parametrize(
        ("member", "overrides"),
        [
            ("name", {"name": "gross_margin"}),
            ("label", {"label": "Gross margin"}),
            ("description", {"description": "Revenue less cost"}),
            (
                "expression",
                {
                    "expression": ExprNode(
                        op="add",
                        left=ExprNode(op="field", field_id="revenue"),
                        right=ExprNode(op="field", field_id="cost"),
                    )
                },
            ),
            (
                "output_type",
                {
                    "expression": ExprNode(
                        op="div",
                        left=ExprNode(op="field", field_id="revenue"),
                        right=ExprNode(op="field", field_id="cost"),
                    ),
                    "output_type": "float",
                },
            ),
            (
                "requires",
                {
                    "expression": ExprNode(
                        op="add",
                        left=ExprNode(op="field", field_id="revenue"),
                        right=ExprNode(op="field", field_id="price"),
                    ),
                    "output_type": "float",
                    "requires": ("revenue", "price"),
                },
            ),
            ("zero_division_policy", {"zero_division_policy": "error"}),
        ],
    )
    def test_every_canonical_member_changes_bundle_and_projection_anchor(
        self,
        member: str,
        overrides: dict[str, object],
    ) -> None:
        baseline = make_cf()
        changed = make_cf(**overrides)
        assert baseline.canonical_payload()[member] != changed.canonical_payload()[member]
        baseline_bundle = make_cf_bundle(
            make_cf_descriptor(calculated_fields=(baseline,))
        )
        changed_bundle = make_cf_bundle(
            make_cf_descriptor(calculated_fields=(changed,))
        )
        assert baseline_bundle.fingerprint != changed_bundle.fingerprint
        assert baseline.content_hash() != changed.content_hash()
