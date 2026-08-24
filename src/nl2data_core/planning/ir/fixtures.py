"""Golden fixtures for the canonical Semantic Query IR (DDS-019).

The golden canonical JSON and fingerprint are frozen values computed from
:func:`golden_ir` with the repository's canonical serializer.  They guard
against accidental changes to field order, defaults, or canonicalization
rules: any change that alters the serialized form or fingerprint of the
golden IR fails the contract tests.

Future SQL, MongoDB, and other compilers share these fixtures so logical
identity across backends stays provable.
"""

from __future__ import annotations

from nl2data_core.planning.models import ColumnBinding, PhysicalBinding

from .models import (
    IRExtension,
    IRFilter,
    IRGrouping,
    IROrdering,
    IRProvenance,
    IRResultShape,
    IRSelection,
    IRTimeContext,
    SemanticQueryIR,
)

GOLDEN_IR_ID = "ir-golden-001"
GOLDEN_SOURCE_ID = "acme_warehouse"
GOLDEN_ROOT_ENTITY_ID = "orders"
GOLDEN_CATALOG_FINGERPRINT = "sha256:" + "ab" * 32
GOLDEN_POLICY_VIEW_FINGERPRINT = "sha256:" + "cd" * 32

#: Frozen canonical JSON of :func:`golden_ir` (must never change silently).
GOLDEN_CANONICAL_JSON = (
    '{"extensions":[{"extension_id":"e1","kind":"customer_risk_flag",'
    '"payload":{"mode":"strict"}}],"filters":[{"field_id":"status",'
    '"filter_id":"f1","operator":"eq","value":"shipped"},{"field_id":"region",'
    '"filter_id":"f2","operator":"in","value":["north","south"]}],'
    '"groupings":[{"field_id":"region","grouping_id":"g1"}],'
    '"ir_id":"ir-golden-001","ir_version":1,"limit":100,'
    '"orderings":[{"direction":"desc","field_id":"total_amount",'
    '"ordering_id":"o1"}],"provenance":{"catalog_fingerprint":'
    '"sha256:abababababababababababababababababababababababababababababababab",'
    '"policy_view_fingerprint":'
    '"sha256:cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd",'
    '"root_entity_id":"orders","source_id":"acme_warehouse"},'
    '"required_capabilities":["aggregation","customer_risk_flag","grouping","list_ops","ordering"],'
    '"result_shape":{"kind":"grouped_rows"},"root_entity_id":"orders",'
    '"selections":[{"aggregation":"none","alias":null,"field_id":"region",'
    '"selection_id":"s1"},{"aggregation":"sum","alias":"order_value",'
    '"field_id":"total_amount","selection_id":"s2"}],'
    '"source_id":"acme_warehouse","time_context":{"context_id":"t1",'
    '"reference":"as_of","value":"2026-01-01T00:00:00Z"}}'
)

#: Frozen SHA-256 fingerprint of the golden IR canonical payload.
GOLDEN_FINGERPRINT = (
    "sha256:1d89bc67bf3f871045bff0e5949c4f4380893f2418a23d7c35d56c8f95bd768c"
)


def golden_ir() -> SemanticQueryIR:
    """The frozen golden IR shared by contract, compiler, and workflow tests."""
    return SemanticQueryIR(
        ir_id=GOLDEN_IR_ID,
        source_id=GOLDEN_SOURCE_ID,
        root_entity_id=GOLDEN_ROOT_ENTITY_ID,
        selections=(
            IRSelection(selection_id="s1", field_id="region", alias=None, aggregation="none"),
            IRSelection(
                selection_id="s2",
                field_id="total_amount",
                alias="order_value",
                aggregation="sum",
            ),
        ),
        filters=(
            IRFilter(filter_id="f1", field_id="status", operator="eq", value="shipped"),
            IRFilter(
                filter_id="f2",
                field_id="region",
                operator="in",
                value=("north", "south"),
            ),
        ),
        groupings=(IRGrouping(grouping_id="g1", field_id="region"),),
        orderings=(
            IROrdering(ordering_id="o1", field_id="total_amount", direction="desc"),
        ),
        limit=100,
        time_context=IRTimeContext(
            context_id="t1",
            reference="as_of",
            value="2026-01-01T00:00:00Z",
        ),
        result_shape=IRResultShape(kind="grouped_rows"),
        provenance=IRProvenance(
            source_id=GOLDEN_SOURCE_ID,
            root_entity_id=GOLDEN_ROOT_ENTITY_ID,
            catalog_fingerprint=GOLDEN_CATALOG_FINGERPRINT,
            policy_view_fingerprint=GOLDEN_POLICY_VIEW_FINGERPRINT,
        ),
        required_capabilities=(
            "aggregation",
            "customer_risk_flag",
            "grouping",
            "list_ops",
            "ordering",
        ),
        extensions=(
            IRExtension(
                extension_id="e1",
                kind="customer_risk_flag",
                payload={"mode": "strict"},
            ),
        ),
    )


def golden_binding() -> PhysicalBinding:
    """The frozen physical binding used to compile the golden IR.

    Maps the golden IR's semantic fields onto the ``orders_table`` SQLite
    object; shared by SQL and MongoDB compiler tests.
    """
    return PhysicalBinding(
        object_id="orders_table",
        dialect="sqlite",
        column_bindings=(
            ColumnBinding(field_id="region", physical_name="region"),
            ColumnBinding(field_id="total_amount", physical_name="total_amount"),
            ColumnBinding(field_id="status", physical_name="status"),
        ),
    )
