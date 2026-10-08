"""Contract tests for the bounded typed IR expression algebra.

Covers the additive computed-expression contract: every node kind in the
closed operator set, canonical payload participation and fingerprint
identity, legacy payload/fingerprint preservation for IRs without the new
members, capability gating, and fail-closed rejection of malformed,
oversized, out-of-view, and semantically inconsistent expressions.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nl2data_core.planning.ir import (
    EXPRESSION_CAPABILITY,
    MAX_EXPRESSION_DEPTH,
    MAX_EXPRESSION_NODES,
    PRIOR_PERIOD_CAPABILITY,
    IRAggregateExpr,
    IRBinaryExpr,
    IRCaseExpr,
    IRCaseWhen,
    IRCoalesceExpr,
    IRDateTruncExpr,
    IRFieldExpr,
    IRGrouping,
    IRLiteralExpr,
    IRNullIfExpr,
    IROrdering,
    IRPredicateExpr,
    IRPriorPeriodExpr,
    IRProvenance,
    IRResultShape,
    IRRoundExpr,
    IRSelection,
    SemanticQueryIR,
    expression_depth,
    expression_field_ids,
    expression_has_aggregates,
    expression_node_count,
    expression_required_capabilities,
    expressions_equal,
)
from nl2data_core.planning.ir.fixtures import GOLDEN_CANONICAL_JSON, GOLDEN_FINGERPRINT, golden_ir
from nl2data_core.planning.ir.models import IRViewReference
from nl2data_core.planning.ir.validation import validate_ir
from nl2data_core.planning.validation import AuthorizedView

_VIEW_FIELDS = frozenset(
    {
        "orders.order_id",
        "orders.status",
        "orders.amount",
        "orders.created_at",
        "customers.channel",
        "products.category",
    }
)


def _view(**overrides) -> AuthorizedView:
    return AuthorizedView(
        source_id="sales",
        root_entity_ids=frozenset({"orders"}),
        field_ids=_VIEW_FIELDS,
        **overrides,
    )


def _ir(selections, groupings=(), orderings=(), capabilities=(), **overrides) -> SemanticQueryIR:
    parts: dict[str, object] = {
        "ir_id": "ir-expr-1",
        "source_id": "sales",
        "root_entity_id": "orders",
        "selections": selections,
        "groupings": groupings,
        "orderings": orderings,
        "limit": 100,
        "provenance": IRProvenance(source_id="sales", root_entity_id="orders"),
        "required_capabilities": capabilities,
    }
    parts.update(overrides)
    return SemanticQueryIR(**parts)


def _refund_rate() -> IRRoundExpr:
    """ROUND(100.0 * count_distinct(refunded) / count(*), 2) - the q2 shape."""
    refunded = IRAggregateExpr(
        func="count_distinct",
        operand=IRFieldExpr(field_id="orders.order_id"),
        filter=IRPredicateExpr(
            op="eq", operand=IRFieldExpr(field_id="orders.status"), value="refunded"
        ),
    )
    total = IRAggregateExpr(func="count", operand=IRFieldExpr(field_id="orders.order_id"))
    return IRRoundExpr(
        operand=IRBinaryExpr(
            op="mul",
            left=IRLiteralExpr(value=100.0),
            right=IRBinaryExpr(op="div", left=refunded, right=total, zero_division_policy="null"),
        ),
        scale=2,
    )


def _week_bucket() -> IRDateTruncExpr:
    return IRDateTruncExpr(bucket="week", operand=IRFieldExpr(field_id="orders.created_at"))


class TestExpressionNodes:
    def test_every_node_kind_is_constructible_and_frozen(self) -> None:
        nodes = [
            IRFieldExpr(field_id="orders.amount"),
            IRLiteralExpr(value=7),
            IRPredicateExpr(op="gte", operand=IRFieldExpr(field_id="orders.amount"), value=10),
            IRBinaryExpr(op="add", left=IRLiteralExpr(value=1), right=IRLiteralExpr(value=2)),
            IRRoundExpr(operand=IRLiteralExpr(value=1.5), scale=2),
            IRCoalesceExpr(
                operands=(IRFieldExpr(field_id="orders.amount"), IRLiteralExpr(value=0))
            ),
            IRNullIfExpr(left=IRLiteralExpr(value=0), right=IRFieldExpr(field_id="orders.amount")),
            IRCaseExpr(
                whens=(
                    IRCaseWhen(
                        condition=IRPredicateExpr(
                            op="eq", operand=IRFieldExpr(field_id="orders.status"), value="paid"
                        ),
                        result=IRLiteralExpr(value=1),
                    ),
                ),
                default=IRLiteralExpr(value=0),
            ),
            _week_bucket(),
            IRAggregateExpr(func="sum", operand=IRFieldExpr(field_id="orders.amount")),
            IRPriorPeriodExpr(
                aggregate="count", bucket="week", operand=IRFieldExpr(field_id="orders.order_id")
            ),
        ]
        for node in nodes:
            with pytest.raises((TypeError, ValidationError)):
                node.kind = "field"  # type: ignore[misc]

    def test_canonical_payloads_are_json_safe_and_stable(self) -> None:
        left = IRBinaryExpr(op="div", left=IRLiteralExpr(value=1), right=IRLiteralExpr(value=2))
        rebuilt = IRBinaryExpr.model_validate(left.canonical_payload())
        assert expressions_equal(left, rebuilt)

    def test_in_predicate_normalizes_to_tuple(self) -> None:
        predicate = IRPredicateExpr(
            op="in", operand=IRFieldExpr(field_id="orders.status"), value=["paid", "shipped"]
        )
        assert predicate.value == ("paid", "shipped")

    def test_metrics_follow_the_tree_shape(self) -> None:
        rate = _refund_rate()
        assert expression_node_count(rate) == 9
        assert expression_depth(rate) == 5
        assert expression_field_ids(rate) == frozenset(
            {"orders.order_id", "orders.status"}
        )
        assert expression_has_aggregates(rate) is True
        assert expression_has_aggregates(_week_bucket()) is False
        assert expression_required_capabilities(rate) == {EXPRESSION_CAPABILITY}

    def test_prior_period_requires_its_capability(self) -> None:
        prior = IRPriorPeriodExpr(
            aggregate="sum", bucket="month", operand=IRFieldExpr(field_id="orders.amount")
        )
        assert expression_required_capabilities(prior) == {
            EXPRESSION_CAPABILITY,
            PRIOR_PERIOD_CAPABILITY,
        }

    def test_fingerprint_determinism_is_mapping_order_independent(self) -> None:
        direct = _refund_rate()
        payload = direct.canonical_payload()
        from nl2data_core.canonical import strict_canonical_json

        assert strict_canonical_json(payload) == strict_canonical_json(
            IRRoundExpr.model_validate(payload).canonical_payload()
        )


class TestMalformedExpressionsFailClosed:
    def test_unknown_kind_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            IRLiteralExpr.model_validate({"kind": "window", "value": 1})

    def test_literal_rejects_non_scalars_and_unbounded_strings(self) -> None:
        with pytest.raises(ValidationError):
            IRLiteralExpr(value={"nested": 1})
        with pytest.raises(ValidationError):
            IRLiteralExpr(value=float("nan"))
        with pytest.raises(ValidationError):
            IRLiteralExpr(value="x" * 257)

    def test_predicate_value_shapes_are_enforced(self) -> None:
        field = IRFieldExpr(field_id="orders.status")
        with pytest.raises(ValidationError):
            IRPredicateExpr(op="in", operand=field, value=())
        with pytest.raises(ValidationError):
            IRPredicateExpr(op="in", operand=field, value=tuple(f"v{i}" for i in range(17)))
        with pytest.raises(ValidationError):
            IRPredicateExpr(op="is_null", operand=field, value="x")
        assert IRPredicateExpr(op="is_null", operand=field).value is None

    def test_zero_division_policy_is_restricted_to_division(self) -> None:
        with pytest.raises(ValidationError):
            IRBinaryExpr(
                op="add",
                left=IRLiteralExpr(value=1),
                right=IRLiteralExpr(value=2),
                zero_division_policy="null",
            )

    def test_round_scale_is_bounded(self) -> None:
        with pytest.raises(ValidationError):
            IRRoundExpr(operand=IRLiteralExpr(value=1.5), scale=7)

    def test_case_and_coalesce_arities_are_bounded(self) -> None:
        condition = IRPredicateExpr(
            op="eq", operand=IRFieldExpr(field_id="orders.status"), value="paid"
        )
        whens = tuple(
            IRCaseWhen(condition=condition, result=IRLiteralExpr(value=1)) for _ in range(9)
        )
        with pytest.raises(ValidationError):
            IRCaseExpr(whens=whens)
        with pytest.raises(ValidationError):
            IRCoalesceExpr(
                operands=tuple(IRLiteralExpr(value=i) for i in range(5))
            )

    def test_depth_exceeding_bound_is_rejected(self) -> None:
        expr: object = IRLiteralExpr(value=1)
        for _ in range(MAX_EXPRESSION_DEPTH - 1):
            expr = IRBinaryExpr(op="add", left=expr, right=IRLiteralExpr(value=1))  # type: ignore[arg-type]
        assert expression_depth(expr) == MAX_EXPRESSION_DEPTH  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            IRBinaryExpr(op="add", left=expr, right=IRLiteralExpr(value=1))  # type: ignore[arg-type]
        assert MAX_EXPRESSION_DEPTH == 6

    def test_node_count_exceeding_bound_is_rejected(self) -> None:
        # One case expression with many bounded branches blows the global budget.
        condition = IRPredicateExpr(
            op="eq", operand=IRFieldExpr(field_id="orders.status"), value="paid"
        )
        whens = tuple(
            IRCaseWhen(condition=condition, result=IRBinaryExpr(
                op="mul",
                left=IRLiteralExpr(value=i),
                right=IRFieldExpr(field_id="orders.amount"),
            ))
            for i in range(8)
        )
        with pytest.raises(ValidationError):
            IRCaseExpr(whens=whens)
        assert MAX_EXPRESSION_NODES == 24


class TestIRIntegration:
    def test_computed_selection_participates_in_fingerprint(self) -> None:
        base = _ir(
            selections=(IRSelection(selection_id="s1", field_id="orders.amount"),),
            capabilities=("ordering",),
        )
        computed = _ir(
            selections=(
                IRSelection(
                    selection_id="s1",
                    field_id="refund_rate_pct",
                    expression=_refund_rate(),
                ),
            ),
            capabilities=("ordering", EXPRESSION_CAPABILITY),
        )
        assert "expression" not in base.serialize_canonical()
        assert "expression" in computed.serialize_canonical()
        assert computed.fingerprint != base.fingerprint

    def test_computed_selection_requires_its_capability(self) -> None:
        with pytest.raises(ValidationError):
            _ir(
                selections=(
                    IRSelection(
                        selection_id="s1", field_id="refund_rate_pct", expression=_refund_rate()
                    ),
                ),
            )

    def test_computed_selection_rejects_base_aggregation(self) -> None:
        with pytest.raises(ValidationError):
            IRSelection(
                selection_id="s1",
                field_id="refund_rate_pct",
                aggregation="count",
                expression=_refund_rate(),
            )

    def test_grouping_expression_must_mirror_a_computed_selection(self) -> None:
        bucket = _week_bucket()
        with pytest.raises(ValidationError):
            _ir(
                selections=(
                    IRSelection(selection_id="s1", field_id="week_start", expression=bucket),
                ),
                groupings=(IRGrouping(grouping_id="g1", field_id="week_start"),),
                capabilities=(EXPRESSION_CAPABILITY,),
            )
        with pytest.raises(ValidationError):
            _ir(
                selections=(
                    IRSelection(selection_id="s1", field_id="week_start", expression=bucket),
                ),
                groupings=(
                    IRGrouping(
                        grouping_id="g1",
                        field_id="week_start",
                        expression=IRDateTruncExpr(
                            bucket="day", operand=IRFieldExpr(field_id="orders.created_at")
                        ),
                    ),
                ),
                capabilities=(EXPRESSION_CAPABILITY,),
            )
        matched = _ir(
            selections=(IRSelection(selection_id="s1", field_id="week_start", expression=bucket),),
            groupings=(IRGrouping(grouping_id="g1", field_id="week_start", expression=bucket),),
            capabilities=(EXPRESSION_CAPABILITY,),
        )
        assert matched.fingerprint

    def test_prior_period_requires_its_capability_at_ir_level(self) -> None:
        prior = IRPriorPeriodExpr(
            aggregate="count", bucket="week", operand=IRFieldExpr(field_id="orders.order_id")
        )
        with pytest.raises(ValidationError):
            _ir(
                selections=(
                    IRSelection(selection_id="s1", field_id="prev_orders", expression=prior),
                ),
                capabilities=(EXPRESSION_CAPABILITY,),
            )


class TestLegacyIdentityPreserved:
    def test_golden_payload_and_fingerprint_unchanged(self) -> None:
        ir = golden_ir()
        assert ir.serialize_canonical() == GOLDEN_CANONICAL_JSON
        assert ir.fingerprint == GOLDEN_FINGERPRINT
        assert '"expression"' not in GOLDEN_CANONICAL_JSON


class TestExpressionViewScoping:
    def _computed_ir(self, **overrides) -> SemanticQueryIR:
        bucket = _week_bucket()
        prior = IRPriorPeriodExpr(
            aggregate="count", bucket="week", operand=IRFieldExpr(field_id="orders.order_id")
        )
        parts: dict[str, object] = {
            "selections": (
                IRSelection(selection_id="s1", field_id="week_start", expression=bucket),
                IRSelection(selection_id="s2", field_id="order_count", expression=prior),
            ),
            "groupings": (
                IRGrouping(grouping_id="g1", field_id="week_start", expression=bucket),
            ),
            "orderings": (
                IROrdering(ordering_id="o1", field_id="week_start", direction="desc"),
            ),
            "capabilities": (
                "grouping",
                "ordering",
                EXPRESSION_CAPABILITY,
                PRIOR_PERIOD_CAPABILITY,
            ),
            "result_shape": IRResultShape(kind="grouped_rows"),
        }
        parts.update(overrides)
        return _ir(**parts)

    def test_computed_query_validates_against_the_view(self) -> None:
        result = validate_ir(self._computed_ir(), view=_view())
        assert result.valid, result.issues

    def test_out_of_view_expression_field_is_rejected(self) -> None:
        prior = IRPriorPeriodExpr(
            aggregate="count", bucket="week", operand=IRFieldExpr(field_id="orders.ghost")
        )
        ir = self._computed_ir(
            selections=(
                IRSelection(
                    selection_id="s1",
                    field_id="week_start",
                    expression=_week_bucket(),
                ),
                IRSelection(selection_id="s2", field_id="order_count", expression=prior),
            ),
        )
        result = validate_ir(ir, view=_view())
        assert not result.valid
        assert "field_out_of_scope" in result.issue_codes()

    def test_prior_period_requires_a_matching_bucket_grouping(self) -> None:
        prior = IRPriorPeriodExpr(
            aggregate="count", bucket="week", operand=IRFieldExpr(field_id="orders.order_id")
        )
        ir = _ir(
            selections=(
                IRSelection(selection_id="s1", field_id="customers.channel"),
                IRSelection(selection_id="s2", field_id="order_count", expression=prior),
            ),
            groupings=(IRGrouping(grouping_id="g1", field_id="customers.channel"),),
            capabilities=("grouping", EXPRESSION_CAPABILITY, PRIOR_PERIOD_CAPABILITY),
            result_shape=IRResultShape(kind="grouped_rows"),
        )
        result = validate_ir(ir, view=_view())
        assert not result.valid
        assert "prior_period_bucket_ungrouped" in result.issue_codes()

    def test_prior_period_bucket_ambiguous_across_time_fields_is_rejected(self) -> None:
        """Same bucket kind over two time fields makes the LAG order ambiguous."""
        created_bucket = _week_bucket()
        shipped_bucket = IRDateTruncExpr(
            bucket="week", operand=IRFieldExpr(field_id="orders.shipped_at")
        )
        prior = IRPriorPeriodExpr(
            aggregate="count", bucket="week", operand=IRFieldExpr(field_id="orders.order_id")
        )
        ir = _ir(
            selections=(
                IRSelection(
                    selection_id="s1", field_id="order_week", expression=created_bucket
                ),
                IRSelection(
                    selection_id="s2", field_id="ship_week", expression=shipped_bucket
                ),
                IRSelection(selection_id="s3", field_id="prev_orders", expression=prior),
            ),
            groupings=(
                IRGrouping(
                    grouping_id="g1", field_id="order_week", expression=created_bucket
                ),
                IRGrouping(
                    grouping_id="g2", field_id="ship_week", expression=shipped_bucket
                ),
            ),
            capabilities=("grouping", EXPRESSION_CAPABILITY, PRIOR_PERIOD_CAPABILITY),
            result_shape=IRResultShape(kind="grouped_rows"),
        )
        view = AuthorizedView(
            source_id="sales",
            root_entity_ids=frozenset({"orders"}),
            field_ids=_VIEW_FIELDS | {"orders.shipped_at"},
        )
        result = validate_ir(ir, view=view)
        assert not result.valid
        assert "prior_period_bucket_ambiguous" in result.issue_codes()

    def test_ordering_by_unknown_output_is_rejected(self) -> None:
        ir = self._computed_ir(
            orderings=(IROrdering(ordering_id="o1", field_id="ghost_output", direction="desc"),),
        )
        result = validate_ir(ir, view=_view())
        assert not result.valid
        assert "field_out_of_scope" in result.issue_codes()

    def test_view_aggregation_restrictions_apply_to_expression_aggregates(self) -> None:
        fingerprint = "sha256:" + "a" * 64
        restricted = _view(
            view_id="demo",
            view_version=1,
            view_fingerprint=fingerprint,
            field_aggregation_restrictions={"orders.order_id": frozenset({"count"})},
        )
        ir = _ir(
            selections=(
                IRSelection(
                    selection_id="s1",
                    field_id="refund_rate_pct",
                    expression=_refund_rate(),
                ),
            ),
            capabilities=(EXPRESSION_CAPABILITY,),
            provenance=IRProvenance(
                source_id="sales",
                root_entity_id="orders",
                view_reference=IRViewReference(
                    view_id="demo", view_version=1, view_fingerprint=fingerprint
                ),
            ),
        )
        result = validate_ir(ir, view=restricted)
        assert not result.valid
        assert "aggregation_out_of_scope" in result.issue_codes()
