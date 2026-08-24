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

from nl2data_core.adapters.mongodb.models import MongoOperation, MongoQuerySpec
from nl2data_core.canonical import sha256_fingerprint
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
    return {"customers": tuple(customers), "orders": tuple(orders)}


#: Deterministic synthetic seed shared by all profiles.
SEED: dict[str, tuple[tuple[Any, ...], ...]] = _build_seed()

#: Expected object counts derived from the seed.
EXPECTED_COUNTS: tuple[TableCount, ...] = (
    TableCount(table="customers", count=len(SEED["customers"])),
    TableCount(table="orders", count=len(SEED["orders"])),
)

#: Canonical setup fingerprint of schema + seed; identical for every profile
#: because provisioning is defined by the logical data alone.
FIXTURE_SETUP_FINGERPRINT: str = fixture_setup_fingerprint(SCHEMA, SEED)


#: MongoDB collections with their canonical dotted field paths (no values).
MONGO_SCHEMA: dict[str, tuple[str, ...]] = {
    "customers": ("customer_id", "name", "region"),
    "orders": ("order_id", "customer_id", "amount", "region", "status", "created_at"),
}


def _build_mongo_seed() -> dict[str, tuple[dict[str, Any], ...]]:
    """Same logical seed as :data:`SEED`, shaped as MongoDB documents."""
    customers = (
        {"_id": 1, "customer_id": 1, "name": "Acme", "region": "emea"},
        {"_id": 2, "customer_id": 2, "name": "Beta", "region": "apac"},
        {"_id": 3, "customer_id": 3, "name": "Gamma", "region": "emea"},
        {"_id": 4, "customer_id": 4, "name": "Delta", "region": "amer"},
    )
    orders: list[dict[str, Any]] = []
    order_id = 0
    for customer in customers:
        customer_id = customer["customer_id"]
        region = customer["region"]
        for offset in range(6):
            order_id += 1
            orders.append(
                {
                    "_id": order_id,
                    "order_id": order_id,
                    "customer_id": customer_id,
                    "amount": 10.0 * order_id,
                    "region": region,
                    "status": order_id % 3,
                    #: Fixed dates inside the January 2026 evaluation window.
                    "created_at": f"2026-01-{offset + 1:02d}",
                }
            )
    return {"customers": tuple(customers), "orders": tuple(orders)}


#: Deterministic MongoDB seed; insertion order is stable by construction.
MONGO_SEED: dict[str, tuple[dict[str, Any], ...]] = _build_mongo_seed()

#: Canonical setup fingerprint of the MongoDB schema + seed.
MONGO_FIXTURE_SETUP_FINGERPRINT: str = sha256_fingerprint(
    {"schema": MONGO_SCHEMA, "documents": MONGO_SEED}
)


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
)


@dataclass(frozen=True)
class MongoResultAssertion:
    """One shared protected-result expectation against the Mongo fixture."""

    name: str
    spec: MongoQuerySpec
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


#: MongoDB result assertions with the same logical outcomes as
#: :data:`RESULT_ASSERTIONS`, so SQL/Mongo equivalence is comparable.
MONGO_RESULT_ASSERTIONS: tuple[MongoResultAssertion, ...] = (
    MongoResultAssertion(
        name="emea orders by amount desc",
        spec=MongoQuerySpec(
            spec_id="assertion-emea-top3",
            operation=MongoOperation.FIND,
            collection="orders",
            filter={"region": {"$eq": "emea"}},
            projection={"order_id": 1, "amount": 1},
            sort={"amount": -1},
            limit=3,
        ),
        columns=("order_id", "amount"),
        rows=((18, 180.0), (17, 170.0), (16, 160.0)),
    ),
    MongoResultAssertion(
        name="status group counts",
        spec=MongoQuerySpec(
            spec_id="assertion-status-counts",
            operation=MongoOperation.AGGREGATE,
            collection="orders",
            limit=10,
            pipeline=(
                {"$group": {"_id": "$status", "cnt": {"$sum": 1}}},
                {"$sort": {"_id": 1}},
                {"$project": {"status": "$_id", "cnt": 1, "_id": 0}},
            ),
        ),
        columns=("status", "cnt"),
        rows=((0, 8), (1, 8), (2, 8)),
    ),
    MongoResultAssertion(
        name="latest order id",
        spec=MongoQuerySpec(
            spec_id="assertion-latest-order",
            operation=MongoOperation.AGGREGATE,
            collection="orders",
            limit=1,
            pipeline=(
                {"$group": {"_id": None, "latest": {"$max": "$order_id"}}},
                {"$project": {"latest": 1, "_id": 0}},
            ),
        ),
        columns=("latest",),
        rows=((24,),),
    ),
)
