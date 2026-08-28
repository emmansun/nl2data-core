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
#: setup is limited to connection and placeholder handling.
SCHEMA: dict[str, str] = {
    "customers": "CREATE TABLE customers (customer_id INT, name TEXT, region TEXT)",
    "orders": (
        "CREATE TABLE orders (order_id INT, customer_id INT, amount REAL, "
        "region TEXT, status INT, created_at TEXT)"
    ),
    "products": "CREATE TABLE products (product_id INT, category TEXT, unit_price REAL)",
    "order_items": (
        "CREATE TABLE order_items (item_id INT, order_id INT, product_id INT, "
        "quantity INT, unit_price REAL)"
    ),
}


def _build_seed() -> dict[str, tuple[tuple[Any, ...], ...]]:
    """Deterministic synthetic seed; insertion order is stable by construction."""
    customers = (
        (1, "Acme", "emea"),
        (2, "Beta", "apac"),
        (3, "Gamma", "emea"),
        (4, "Delta", "amer"),
    )
    orders: list[tuple[Any, ...]] = []
    order_id = 0
    for customer_id, _name, region in customers:
        for offset in range(6):
            order_id += 1
            orders.append(
                (
                    order_id,
                    customer_id,
                    10.0 * order_id,
                    region,
                    order_id % 3,
                    #: Fixed dates inside the January 2026 evaluation window.
                    f"2026-01-{offset + 1:02d}",
                )
            )
    products = (
        (1, "electronics", 299.99),
        (2, "appliances", 89.5),
        (3, "furniture", 450.0),
        (4, "clothing", 29.99),
    )
    product_price = {product_id: price for product_id, _category, price in products}
    order_items: list[tuple[Any, ...]] = []
    item_id = 0
    for order_id, _ in enumerate(orders, start=1):
        for index in range(2):
            item_id += 1
            product_id = ((order_id + index - 1) % len(products)) + 1
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
    """One shared protected-result expectation against the fixture."""

    name: str
    sql: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


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
)
