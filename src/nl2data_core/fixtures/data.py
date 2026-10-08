"""Shared deterministic fixture data for controlled SQL fixtures.

The logical schema, synthetic seed rows, expected counts, policy cases,
and result assertions are defined once here and reused by every profile
(SQLite default, optional PostgreSQL) so outcomes stay comparable.  All
values are fixed literals bound to the evaluation clock anchor; nothing
depends on wall-clock time or random state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from nl2data_core.fixtures.models import FixtureSpec, TableCount, fixture_setup_fingerprint
from nl2data_core.governance.models import (
    GovernanceDecision,
    GovernanceFacts,
    PolicyScope,
)

#: Logical schema shared by every fixture profile.  The DDL uses only
#: types that SQLite and PostgreSQL both accept, so dialect-specific
#: setup is limited to connection and placeholder handling.  Monetary
#: columns are declared DOUBLE PRECISION (not REAL): SQLite maps that
#: name to its REAL affinity, while PostgreSQL would store plain REAL
#: as float4 and round seed values like 29.99 to a different float64
#: than SQLite's - the profiles must return byte-equal protected rows.
#: The demo extensions are additive: ``customers.channel``,
#: ``products.stock_quantity`` and ``orders.business_status`` join the
#: legacy integer-coded columns, which keep their historical values
#: and semantics.
SCHEMA: dict[str, str] = {
    "customers": (
        "CREATE TABLE customers (customer_id INT, name TEXT, region TEXT, "
        "channel TEXT)"
    ),
    "orders": (
        "CREATE TABLE orders (order_id INT, customer_id INT, "
        "amount DOUBLE PRECISION, "
        "region TEXT, status INT, created_at TEXT, business_status TEXT)"
    ),
    "products": (
        "CREATE TABLE products (product_id INT, category TEXT, "
        "unit_price DOUBLE PRECISION, "
        "stock_quantity INT)"
    ),
    "order_items": (
        "CREATE TABLE order_items (item_id INT, order_id INT, product_id INT, "
        "quantity INT, unit_price DOUBLE PRECISION)"
    ),
}

#: Governed business terms for the legacy integer status codes.  The demo
#: binds semantic ``status`` to ``orders.business_status`` while legacy
#: fixture SQL keeps using the integer code (change design D4).
BUSINESS_STATUS_BY_CODE: dict[int, str] = {0: "paid", 1: "shipped", 2: "refunded"}

#: Customer acquisition channels for the two-/three-entity demo questions.
CUSTOMER_CHANNELS: dict[int, str] = {1: "web", 2: "retail", 3: "partner", 4: "web"}

#: Stock levels for the low-stock question (q8): appliances and clothing
#: are below the low-stock threshold of 10.
PRODUCT_STOCK: dict[int, int] = {1: 42, 2: 8, 3: 120, 4: 3}

#: Monday-based order dates spanning three evaluation weeks.  Per customer
#: the six orders land on 2026-01-05/06/07, 2026-01-12/13 and 2026-01-19,
#: so weekly order counts are 12 / 8 / 4 and the week-over-week prior
#: counts are NULL / 12 / 8 (q5).
ORDER_DATES: tuple[str, ...] = (
    "2026-01-05",
    "2026-01-06",
    "2026-01-07",
    "2026-01-12",
    "2026-01-13",
    "2026-01-19",
)

#: Deterministic per-order product pairs (order_id -> two product ids).
#: A hand-authored table keeps the per-category refund-order rates varied
#: for q2: electronics 6/12, furniture 4/12, clothing 4/12 and appliances
#: 2/12 refunded orders, with 12 distinct orders per category.
ORDER_PRODUCT_PLAN: dict[int, tuple[int, int]] = {
    1: (1, 2),
    2: (1, 3),
    3: (1, 2),
    4: (1, 2),
    5: (1, 3),
    6: (1, 3),
    7: (1, 3),
    8: (1, 4),
    9: (1, 4),
    10: (2, 3),
    11: (1, 4),
    12: (2, 3),
    13: (2, 3),
    14: (1, 2),
    15: (2, 4),
    16: (2, 4),
    17: (3, 4),
    18: (2, 4),
    19: (2, 4),
    20: (3, 4),
    21: (3, 4),
    22: (3, 4),
    23: (1, 2),
    24: (3, 4),
}


def _build_seed() -> dict[str, tuple[tuple[Any, ...], ...]]:
    """Deterministic synthetic seed; insertion order is stable by construction."""
    customers = tuple(
        (
            customer_id,
            name,
            region,
            CUSTOMER_CHANNELS[customer_id],
        )
        for customer_id, name, region in (
            (1, "Acme", "emea"),
            (2, "Beta", "apac"),
            (3, "Gamma", "emea"),
            (4, "Delta", "amer"),
        )
    )
    orders: list[tuple[Any, ...]] = []
    order_id = 0
    for customer_id, _name, region, _channel in customers:
        for offset in range(6):
            order_id += 1
            status = order_id % 3
            orders.append(
                (
                    order_id,
                    customer_id,
                    10.0 * order_id,
                    region,
                    status,
                    #: Monday-based dates spanning three evaluation weeks.
                    ORDER_DATES[offset],
                    #: Governed business status derived from the legacy code.
                    BUSINESS_STATUS_BY_CODE[status],
                )
            )
    products = tuple(
        (
            product_id,
            category,
            price,
            PRODUCT_STOCK[product_id],
        )
        for product_id, category, price in (
            (1, "electronics", 299.99),
            (2, "appliances", 89.5),
            (3, "furniture", 450.0),
            (4, "clothing", 29.99),
        )
    )
    product_price = {
        product_id: price for product_id, _category, price, _stock in products
    }
    order_items: list[tuple[Any, ...]] = []
    item_id = 0
    for order_id, _ in enumerate(orders, start=1):
        first_product, second_product = ORDER_PRODUCT_PLAN[order_id]
        for index, product_id in enumerate((first_product, second_product)):
            item_id += 1
            quantity = index + 1
            order_items.append(
                (
                    item_id,
                    order_id,
                    product_id,
                    quantity,
                    product_price[product_id],
                )
            )
    return {
        "customers": tuple(customers),
        "orders": tuple(orders),
        "products": tuple(products),
        "order_items": tuple(order_items),
    }


#: Deterministic synthetic seed shared by all profiles.
SEED: dict[str, tuple[tuple[Any, ...], ...]] = _build_seed()

#: Expected object counts derived from the seed.
EXPECTED_COUNTS: tuple[TableCount, ...] = (
    TableCount(table="customers", count=len(SEED["customers"])),
    TableCount(table="orders", count=len(SEED["orders"])),
    TableCount(table="products", count=len(SEED["products"])),
    TableCount(table="order_items", count=len(SEED["order_items"])),
)

#: Canonical setup fingerprint of schema + seed; identical for every profile
#: because provisioning is defined by the logical data alone.
FIXTURE_SETUP_FINGERPRINT: str = fixture_setup_fingerprint(SCHEMA, SEED)


def make_fixture_spec(
    *,
    dialect: str,
    reset_strategy: Literal["recreate", "truncate_reseed"] = "recreate",
) -> FixtureSpec:
    """Build the versioned fixture spec for one profile dialect."""
    return FixtureSpec(
        fixture_id="sales-orders-v1",
        dialect=dialect,
        reset_strategy=reset_strategy,
        expected_counts=EXPECTED_COUNTS,
    )


#: Default spec for the SQLite profile.
FIXTURE_SPEC: FixtureSpec = make_fixture_spec(dialect="sqlite")

#: Spec for the optional PostgreSQL profile; same logical data, so the
#: schema/seed setup fingerprint is identical to the SQLite profile.
POSTGRES_FIXTURE_SPEC: FixtureSpec = make_fixture_spec(dialect="postgres")


@dataclass(frozen=True)
class PolicyCase:
    """One shared governance expectation: typed facts against a scope."""

    name: str
    scope: PolicyScope | None
    facts: GovernanceFacts | None
    expected: GovernanceDecision


def _policy_scope() -> PolicyScope:
    return PolicyScope(
        policy_id="fixture-policy",
        source_ids=frozenset({"sales"}),
        resource_ids=frozenset({"orders"}),
        operation_ids=frozenset({"select"}),
        field_ids=frozenset(
            {"order_id", "customer_id", "amount", "region", "status", "created_at"}
        ),
    )


#: Shared governance policy cases; every profile proves the same expectations.
POLICY_CASES: tuple[PolicyCase, ...] = (
    PolicyCase(
        name="allowed orders query",
        scope=_policy_scope(),
        facts=GovernanceFacts(
            source_id="sales",
            operation="select",
            resource_ids=frozenset({"orders"}),
            field_ids=frozenset({"order_id", "amount", "region"}),
        ),
        expected=GovernanceDecision.ALLOW,
    ),
    PolicyCase(
        name="denied customer resource",
        scope=_policy_scope(),
        facts=GovernanceFacts(
            source_id="sales",
            operation="select",
            resource_ids=frozenset({"customers"}),
            field_ids=frozenset({"customer_id"}),
        ),
        expected=GovernanceDecision.DENY,
    ),
    PolicyCase(
        name="denied out-of-scope field",
        scope=_policy_scope(),
        facts=GovernanceFacts(
            source_id="sales",
            operation="select",
            resource_ids=frozenset({"orders"}),
            field_ids=frozenset({"order_id", "secret"}),
        ),
        expected=GovernanceDecision.DENY,
    ),
    PolicyCase(
        name="unsupported operation",
        scope=_policy_scope(),
        facts=GovernanceFacts(
            source_id="sales",
            operation="update",
            resource_ids=frozenset({"orders"}),
            field_ids=frozenset({"order_id"}),
        ),
        expected=GovernanceDecision.UNSUPPORTED,
    ),
    PolicyCase(
        name="missing policy scope",
        scope=None,
        facts=GovernanceFacts(
            source_id="sales",
            operation="select",
            resource_ids=frozenset({"orders"}),
            field_ids=frozenset({"order_id"}),
        ),
        expected=GovernanceDecision.DENY,
    ),
)


@dataclass(frozen=True)
class ResultAssertion:
    """One shared protected-result expectation against the fixture.

    ``sql`` is the portable assertion query.  ``postgres_sql`` optionally
    overrides it for the PostgreSQL profile when a deterministic week
    bucket needs a dialect-specific rendering; the two variants produce
    identical columns and rows.
    """

    name: str
    sql: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    postgres_sql: str | None = None


#: Shared result assertions; every profile must produce exactly these rows.
RESULT_ASSERTIONS: tuple[ResultAssertion, ...] = (
    ResultAssertion(
        name="emea orders by amount desc",
        sql=(
            "SELECT order_id, amount FROM orders WHERE region = 'emea' ORDER BY amount DESC LIMIT 3"
        ),
        columns=("order_id", "amount"),
        rows=((18, 180.0), (17, 170.0), (16, 160.0)),
    ),
    ResultAssertion(
        name="status group counts",
        sql="SELECT status, COUNT(*) AS cnt FROM orders GROUP BY status ORDER BY status",
        columns=("status", "cnt"),
        rows=((0, 8), (1, 8), (2, 8)),
    ),
    ResultAssertion(
        name="latest order id",
        sql="SELECT MAX(order_id) AS latest FROM orders",
        columns=("latest",),
        rows=((24,),),
    ),
    ResultAssertion(
        name="emea orders with customer name",
        sql=(
            "SELECT o.order_id, c.name FROM orders o "
            "INNER JOIN customers c ON o.customer_id = c.customer_id "
            "WHERE o.region = 'emea' ORDER BY o.amount DESC LIMIT 3"
        ),
        columns=("order_id", "name"),
        rows=((18, "Gamma"), (17, "Gamma"), (16, "Gamma")),
    ),
    # -- Governed demo golden results (q2/q5/q6/q8) ---------------------------
    ResultAssertion(
        name="q2 refund rate by category",
        sql=(
            "SELECT p.category, "
            "COUNT(DISTINCT CASE WHEN o.business_status = 'refunded' "
            "THEN o.order_id END) AS refunded_orders, "
            "COUNT(DISTINCT o.order_id) AS total_orders, "
            "100e0 * COUNT(DISTINCT CASE WHEN o.business_status = 'refunded' "
            "THEN o.order_id END) / COUNT(DISTINCT o.order_id) AS refund_rate "
            "FROM orders o "
            "JOIN order_items oi ON o.order_id = oi.order_id "
            "JOIN products p ON oi.product_id = p.product_id "
            "GROUP BY p.category "
            "ORDER BY refund_rate DESC, p.category ASC"
        ),
        columns=("category", "refunded_orders", "total_orders", "refund_rate"),
        rows=(
            ("electronics", 6, 12, 50.0),
            ("clothing", 4, 12, 33.333333333333336),
            ("furniture", 4, 12, 33.333333333333336),
            ("appliances", 2, 12, 16.666666666666668),
        ),
    ),
    ResultAssertion(
        name="q5 weekly order growth",
        sql=(
            "SELECT DATE(created_at, '-6 days', 'weekday 1') AS week_start, "
            "COUNT(*) AS order_count, "
            "LAG(COUNT(*)) OVER (ORDER BY DATE(created_at, '-6 days', 'weekday 1')) "
            "AS prev_order_count "
            "FROM orders GROUP BY DATE(created_at, '-6 days', 'weekday 1') "
            "ORDER BY week_start"
        ),
        postgres_sql=(
            #: created_at is TEXT in the shared schema, so the bucket needs
            #: an explicit ::date cast; DATE_TRUNC has no text overload.
            "SELECT TO_CHAR(DATE_TRUNC('week', created_at::date), 'YYYY-MM-DD') AS week_start, "
            "COUNT(*) AS order_count, "
            "LAG(COUNT(*)) OVER (ORDER BY DATE_TRUNC('week', created_at::date)) "
            "AS prev_order_count "
            "FROM orders GROUP BY DATE_TRUNC('week', created_at::date) "
            "ORDER BY week_start"
        ),
        columns=("week_start", "order_count", "prev_order_count"),
        rows=(
            ("2026-01-05", 12, None),
            ("2026-01-12", 8, 12),
            ("2026-01-19", 4, 8),
        ),
    ),
    ResultAssertion(
        name="q6 first-order conversion by channel",
        sql=(
            "SELECT c.channel, "
            "COUNT(DISTINCT fo.customer_id) AS new_customers, "
            "COUNT(DISTINCT CASE WHEN o.business_status IN "
            "('paid', 'shipped', 'refunded') THEN o.customer_id END) "
            "AS converted_customers, "
            "100e0 * COUNT(DISTINCT CASE WHEN o.business_status IN "
            "('paid', 'shipped', 'refunded') THEN o.customer_id END) "
            "/ COUNT(DISTINCT fo.customer_id) AS conversion_pct "
            "FROM (SELECT customer_id, MIN(created_at) AS first_order_at "
            "FROM orders WHERE business_status IN "
            "('paid', 'shipped', 'refunded') GROUP BY customer_id) fo "
            "JOIN customers c ON fo.customer_id = c.customer_id "
            "JOIN orders o ON fo.customer_id = o.customer_id "
            "GROUP BY c.channel ORDER BY c.channel"
        ),
        columns=("channel", "new_customers", "converted_customers", "conversion_pct"),
        rows=(
            ("partner", 1, 1, 100.0),
            ("retail", 1, 1, 100.0),
            ("web", 2, 2, 100.0),
        ),
    ),
    ResultAssertion(
        name="q8 low stock products",
        sql=(
            "SELECT product_id, category, stock_quantity, unit_price "
            "FROM products WHERE stock_quantity < 10 "
            "ORDER BY stock_quantity ASC, product_id ASC"
        ),
        columns=("product_id", "category", "stock_quantity", "unit_price"),
        rows=(
            (4, "clothing", 3, 29.99),
            (2, "appliances", 8, 89.5),
        ),
    ),
)
