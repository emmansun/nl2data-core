"""Controlled MongoDB fixture data and in-memory fake-driver profile.

The logical seed and result assertions mirror the core SQL fixtures so
SQL/Mongo conformance stays comparable; provisioning, reset, and disposal
never touch a native client or service.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.fixtures.base import FixtureProfile
from nl2data_core.fixtures.models import FixtureSpec, FixtureVerificationError, TableCount

from .fake import FakeMongoExecutor
from .models import MongoOperation, MongoQuerySpec

#: MongoDB collections with their canonical dotted field paths (no values).
MONGO_SCHEMA: dict[str, tuple[str, ...]] = {
    "customers": ("customer_id", "name", "region"),
    "orders": ("order_id", "customer_id", "amount", "region", "status", "created_at"),
}


def _build_mongo_seed() -> dict[str, tuple[dict[str, Any], ...]]:
    """Same logical seed as :data:`nl2data_core.fixtures.data.SEED`."""
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

#: Expected document counts for the MongoDB fixture (customers + orders only).
MONGO_EXPECTED_COUNTS: tuple[TableCount, ...] = (
    TableCount(table="customers", count=len(MONGO_SEED["customers"])),
    TableCount(table="orders", count=len(MONGO_SEED["orders"])),
)

#: Canonical setup fingerprint of the MongoDB schema + seed.
MONGO_FIXTURE_SETUP_FINGERPRINT: str = sha256_fingerprint(
    {"schema": MONGO_SCHEMA, "documents": MONGO_SEED}
)


@dataclass(frozen=True)
class MongoResultAssertion:
    """One shared protected-result expectation against the Mongo fixture."""

    name: str
    spec: MongoQuerySpec
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


#: MongoDB result assertions with the same logical outcomes as
#: :data:`nl2data_core.fixtures.data.RESULT_ASSERTIONS`, so SQL/Mongo
#: equivalence is comparable.
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


class MongoFixtureProfile(FixtureProfile):
    """In-memory MongoDB profile backed by the deterministic fake executor.

    The fake executor owns a copy of the seed documents, so provisioning,
    reset, and disposal never touch a native client or service.
    """

    def __init__(
        self,
        *,
        expected_setup_fingerprint: str = MONGO_FIXTURE_SETUP_FINGERPRINT,
    ) -> None:
        self._spec = FixtureSpec(
            fixture_id="sales-orders-mongo-v1",
            dialect="mongo",
            reset_strategy="recreate",
            expected_counts=MONGO_EXPECTED_COUNTS,
        )
        self._expected_setup_fingerprint = expected_setup_fingerprint
        self._executor: FakeMongoExecutor | None = None

    @property
    def spec(self) -> FixtureSpec:
        """The versioned fixture spec this profile provisions."""
        return self._spec

    @property
    def executor(self) -> FakeMongoExecutor:
        """The provisioned fake executor (adapter-internal boundary)."""
        if self._executor is None:
            raise FixtureVerificationError("mongo fixture is not provisioned")
        return self._executor

    @property
    def result_assertions(self) -> tuple[MongoResultAssertion, ...]:
        """The shared Mongo result assertions bound to this profile."""
        return MONGO_RESULT_ASSERTIONS

    def provision(self) -> None:
        """Create the fixture to its declared seed state."""
        self._executor = FakeMongoExecutor(MONGO_SEED)

    def reset(self) -> None:
        """Restore the fixture to its seed state."""
        self.dispose()
        self.provision()

    def dispose(self) -> None:
        """Release the fake executor; safe to call more than once."""
        if self._executor is not None:
            self._executor.close()
            self._executor = None

    def verify(self) -> None:
        """Verify expected document counts; raise on any mismatch."""
        if self._executor is None:
            raise FixtureVerificationError("mongo fixture is not provisioned")
        for table_count in MONGO_EXPECTED_COUNTS:
            count = self._executor.count_documents(
                collection=table_count.table, filter_={}
            )
            if count != table_count.count:
                raise FixtureVerificationError(
                    f"collection '{table_count.table}' has {count} documents, "
                    f"expected {table_count.count}"
                )
