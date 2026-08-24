"""Controlled SQL and MongoDB fixtures for repeatable evaluation.

Exposes the shared deterministic data plus the SQLite (default), PostgreSQL
(optional), and MongoDB fake-driver lifecycle profiles.  Optional drivers
are loaded lazily, so importing this package never requires them.
"""

from __future__ import annotations

from nl2data_core.fixtures.base import FixtureProfile
from nl2data_core.fixtures.data import (
    EXPECTED_COUNTS,
    FIXTURE_SETUP_FINGERPRINT,
    FIXTURE_SPEC,
    MONGO_FIXTURE_SETUP_FINGERPRINT,
    MONGO_RESULT_ASSERTIONS,
    MONGO_SCHEMA,
    MONGO_SEED,
    POLICY_CASES,
    POSTGRES_FIXTURE_SPEC,
    RESULT_ASSERTIONS,
    SCHEMA,
    SEED,
    MongoResultAssertion,
    PolicyCase,
    ResultAssertion,
    make_fixture_spec,
)
from nl2data_core.fixtures.models import (
    FIXED_TIMEZONE,
    FIXTURE_SCHEMA_VERSION,
    TIME_ANCHOR,
    FixtureSpec,
    FixtureUnavailableError,
    FixtureVerificationError,
    ResetStrategy,
    TableCount,
)
from nl2data_core.fixtures.mongo import MongoFixtureProfile
from nl2data_core.fixtures.postgres import PostgresFixtureProfile
from nl2data_core.fixtures.sqlite import SQLiteFixtureProfile

__all__ = [
    "EXPECTED_COUNTS",
    "FIXED_TIMEZONE",
    "FIXTURE_SCHEMA_VERSION",
    "FIXTURE_SETUP_FINGERPRINT",
    "FIXTURE_SPEC",
    "MONGO_FIXTURE_SETUP_FINGERPRINT",
    "MONGO_RESULT_ASSERTIONS",
    "MONGO_SCHEMA",
    "MONGO_SEED",
    "POLICY_CASES",
    "POSTGRES_FIXTURE_SPEC",
    "RESULT_ASSERTIONS",
    "SCHEMA",
    "SEED",
    "TIME_ANCHOR",
    "FixtureProfile",
    "FixtureSpec",
    "FixtureUnavailableError",
    "FixtureVerificationError",
    "MongoFixtureProfile",
    "MongoResultAssertion",
    "PolicyCase",
    "PostgresFixtureProfile",
    "ResetStrategy",
    "ResultAssertion",
    "SQLiteFixtureProfile",
    "TableCount",
    "make_fixture_spec",
]
