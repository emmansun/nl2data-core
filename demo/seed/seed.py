#!/usr/bin/env python3
"""Deterministic seed generator for the NL2Data mainflow demo.

The script connects to a PostgreSQL database, creates the reference schema, and
inserts a realistic order-fulfillment dataset with two tenant partitions and
documented anomaly samples.

Environment variables:
    PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD

Example:
    python demo/seed/seed.py --scale small
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

psycopg: Any = None
try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None


SCALE_PROFILES = {
    "small": {
        "customers": 10_000,
        "products": 1_000,
        "orders": 50_000,
        "items_per_order_min": 1,
        "items_per_order_max": 6,
    },
    "medium": {
        "customers": 40_000,
        "products": 5_000,
        "orders": 100_000,
        "items_per_order_min": 1,
        "items_per_order_max": 6,
    },
    "large": {
        "customers": 80_000,
        "products": 10_000,
        "orders": 200_000,
        "items_per_order_min": 1,
        "items_per_order_max": 6,
    },
}

REGIONS = ["north", "south", "east", "west"]
CHANNELS = ["web", "mobile", "partner", "retail"]
CATEGORIES = ["electronics", "apparel", "home", "sports", "books"]
TENANTS = ["acme", "beta"]


class _SeedGenerator:
    def __init__(self, seed: int = 2026) -> None:
        self.rng = random.Random(seed)
        #: Six-month horizon ending now so "previous month" and "last 7 days"
        #: evidence queries always observe fresh data.
        self.start = datetime.now(UTC).replace(microsecond=0) - timedelta(days=180)

    def generate(
        self,
        cursor: Any,
        profile: dict[str, int],
    ) -> None:
        self._truncate_tables(cursor)
        customers = self._generate_customers(profile["customers"])
        products = self._generate_products(profile["products"])
        self._bulk_insert(cursor, "customers", customers)
        self._bulk_insert(cursor, "products", products)

        orders = self._generate_orders(
            profile["orders"], profile["customers"], profile["products"]
        )
        self._bulk_insert(cursor, "orders", orders)

        items = self._generate_items(orders, profile)
        self._bulk_insert(cursor, "order_items", items)

        payments = self._generate_payments(orders)
        self._bulk_insert(cursor, "payments", payments)

        shipments = self._generate_shipments(orders)
        self._bulk_insert(cursor, "shipments", shipments)

    def _truncate_tables(self, cursor: Any) -> None:
        tables = [
            "shipments",
            "payments",
            "order_items",
            "orders",
            "products",
            "customers",
        ]
        for table in tables:
            cursor.execute(f"TRUNCATE TABLE {table} CASCADE")

    def _generate_customers(self, count: int) -> list[dict]:
        rows = []
        for i in range(1, count + 1):
            tenant = TENANTS[i % 2]
            rows.append(
                {
                    "customer_id": i,
                    "tenant_id": tenant,
                    "region": self.rng.choice(REGIONS),
                    "channel": self.rng.choice(CHANNELS),
                    "email": f"customer{i}@example.local",
                    "created_at": self._random_timestamp(),
                }
            )
        return rows

    def _generate_products(self, count: int) -> list[dict]:
        rows = []
        for i in range(1, count + 1):
            tenant = TENANTS[i % 2]
            rows.append(
                {
                    "product_id": i,
                    "tenant_id": tenant,
                    "category": self.rng.choice(CATEGORIES),
                    "unit_price": round(self.rng.uniform(10.0, 500.0), 2),
                    "stock_quantity": self.rng.randint(0, 1_000),
                    "created_at": self._random_timestamp(),
                }
            )
        return rows

    def _generate_orders(
        self,
        count: int,
        customer_count: int,
        product_count: int,
    ) -> list[dict]:
        rows = []
        start = self.start
        for i in range(1, count + 1):
            customer_id = self.rng.randint(1, customer_count)
            tenant = TENANTS[customer_id % 2]
            created_at = start + timedelta(seconds=self.rng.randint(0, 15_552_000))
            amount = round(self.rng.uniform(20.0, 2_000.0), 2)
            status = "paid"
            paid_at = created_at + timedelta(minutes=self.rng.randint(1, 60))
            shipped_at: datetime | None = paid_at + timedelta(hours=self.rng.randint(1, 72))
            refunded_at: datetime | None = None

            # Anomaly samples (deterministic based on order id)
            if i % 997 == 0:
                status = "cancelled"
                paid_at = None  # type: ignore[assignment]
                shipped_at = None
            elif i % 500 == 0:
                status = "refunded"
                refunded_at = shipped_at or created_at + timedelta(days=3)
            elif i % 250 == 0:
                shipped_at = None  # paid but unshipped

            rows.append(
                {
                    "order_id": i,
                    "tenant_id": tenant,
                    "customer_id": customer_id,
                    "region": self.rng.choice(REGIONS),
                    "channel": self.rng.choice(CHANNELS),
                    "status": status,
                    "created_at": created_at,
                    "paid_at": paid_at,
                    "shipped_at": shipped_at,
                    "refunded_at": refunded_at,
                    "amount": amount,
                }
            )
        return rows

    def _generate_items(
        self,
        orders: list[dict],
        profile: dict[str, int],
    ) -> list[dict]:
        rows = []
        product_count = profile["products"]
        item_id = 1
        for order in orders:
            if order["status"] == "cancelled":
                continue
            n = self.rng.randint(
                profile["items_per_order_min"], profile["items_per_order_max"]
            )
            for _ in range(n):
                rows.append(
                    {
                        "item_id": item_id,
                        "tenant_id": order["tenant_id"],
                        "order_id": order["order_id"],
                        "product_id": self.rng.randint(1, product_count),
                        "quantity": self.rng.randint(1, 5),
                        "unit_price": round(self.rng.uniform(10.0, 500.0), 2),
                        "created_at": order["created_at"],
                    }
                )
                item_id += 1
        return rows

    def _generate_payments(self, orders: list[dict]) -> list[dict]:
        rows = []
        payment_id = 1
        for order in orders:
            if order["status"] == "cancelled":
                continue
            base = {
                "payment_id": payment_id,
                "tenant_id": order["tenant_id"],
                "order_id": order["order_id"],
                "amount": order["amount"],
                "status": "completed",
                "created_at": order["created_at"],
            }
            rows.append(base)
            payment_id += 1
            # Duplicate payment attempt anomaly
            if order["order_id"] % 1234 == 0:
                rows.append(
                    {
                        "payment_id": payment_id,
                        "tenant_id": order["tenant_id"],
                        "order_id": order["order_id"],
                        "amount": order["amount"],
                        "status": "failed",
                        "created_at": order["created_at"],
                    }
                )
                payment_id += 1
        return rows

    def _generate_shipments(self, orders: list[dict]) -> list[dict]:
        rows = []
        shipment_id = 1
        for order in orders:
            if order["status"] in {"cancelled", "refunded"} or order["shipped_at"] is None:
                continue
            shipped_at = order["shipped_at"]
            status = "shipped"
            delivered_at = shipped_at + timedelta(hours=self.rng.randint(1, 120))
            # Partial shipment anomaly
            if order["order_id"] % 777 == 0:
                status = "partial"
                delivered_at = None
            rows.append(
                {
                    "shipment_id": shipment_id,
                    "tenant_id": order["tenant_id"],
                    "order_id": order["order_id"],
                    "shipped_at": shipped_at,
                    "delivered_at": delivered_at,
                    "status": status,
                    "created_at": order["created_at"],
                }
            )
            shipment_id += 1
        return rows

    def _random_timestamp(self) -> datetime:
        return self.start + timedelta(seconds=self.rng.randint(0, 15_552_000))

    def _bulk_insert(self, cursor: Any, table: str, rows: list[dict]) -> None:
        if not rows:
            return
        columns = rows[0].keys()
        col_list = ", ".join(columns)
        placeholders = ", ".join(f"%({col})s" for col in columns)
        cursor.executemany(
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
            rows,
        )


def _run_sql_script(connection: Any, path: Path) -> None:
    with path.open(encoding="utf-8") as handle:
        sql = handle.read()
    with connection.cursor() as cursor:
        cursor.execute(sql)
        connection.commit()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Seed the mainflow demo database.")
    parser.add_argument(
        "--scale",
        choices=list(SCALE_PROFILES),
        default="small",
        help="Row-scale target for the seed dataset.",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path(__file__).with_name("..").resolve() / "schema" / "schema.sql",
        help="Path to the reference schema DDL.",
    )
    args = parser.parse_args(argv)

    if psycopg is None:
        print("psycopg is required: pip install 'nl2data-core[postgres]'")
        return 1

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("NL2DATA_POSTGRES_DSN")
    if dsn is None:
        print("DATABASE_URL or NL2DATA_POSTGRES_DSN must be set")
        return 1
    try:
        connection = psycopg.connect(dsn)
    except Exception as exc:  # pragma: no cover
        print(f"Failed to connect to PostgreSQL: {exc}")
        return 1

    try:
        if not args.schema.exists():
            print(f"Schema file not found: {args.schema}")
            return 1
        _run_sql_script(connection, args.schema)

        with connection.cursor() as cursor:
            _SeedGenerator().generate(cursor, SCALE_PROFILES[args.scale])
            connection.commit()

        print(f"Demo seed loaded successfully (scale={args.scale}).")
    finally:
        connection.close()

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
