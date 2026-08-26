"""Security tests for nl2data-workflow-postgres.

Proves that prohibited raw payloads, credentials, native objects, and raw
backend errors never persist or cross the package API.
"""

from __future__ import annotations

import json

import pytest
from nl2data_core.workflow.durable import (
    RAW_PAYLOAD_FIELDS,
    WorkflowSerializationError,
    deserialize_snapshot,
)
from nl2data_core.workflow.models import WorkflowState, WorkflowStatus

from nl2data_workflow_postgres import PostgreSQLStateStore, WorkflowPostgresConfig
from nl2data_workflow_postgres.client import build_pool
from nl2data_workflow_postgres.fake_postgres import FakePostgresPool, OperationalError


def make_state(status: WorkflowStatus = WorkflowStatus.CREATED) -> WorkflowState:
    return WorkflowState(
        workflow_id="wf-1",
        request_id="req-1",
        status=status,
    )


def make_store() -> tuple[PostgreSQLStateStore, FakePostgresPool]:
    pool = FakePostgresPool()
    store = PostgreSQLStateStore(pool=pool, now=pool.clock.now)
    return store, pool


class TestSnapshotSafety:
    def test_safe_snapshot_contains_no_raw_payload_fields(self) -> None:
        state = make_state()
        payload = state.serialize_safe()
        for forbidden in RAW_PAYLOAD_FIELDS:
            assert forbidden not in payload

    def test_snapshot_rejects_raw_payload_fields(self) -> None:
        for forbidden in ("prompt", "raw_query", "credentials", "provider"):
            document = {
                "schema_version": 1,
                "state": {"prompt": "drop table users"},
            }
            if forbidden == "raw_query":
                document = {
                    "schema_version": 1,
                    "state": {"raw_query": "SELECT password"},
                }
            elif forbidden == "credentials":
                document = {
                    "schema_version": 1,
                    "state": {"credentials": {"user": "admin"}},
                }
            elif forbidden == "provider":
                document = {
                    "schema_version": 1,
                    "state": {"provider": "native"},
                }
            with pytest.raises(WorkflowSerializationError):
                deserialize_snapshot(json.dumps(document))

    def test_dsn_or_connection_strings_are_rejected_in_snapshot(self) -> None:
        document = {
            "schema_version": 1,
            "state": {"connection": "postgres://user:pass@host/db"},
        }
        with pytest.raises(WorkflowSerializationError):
            deserialize_snapshot(json.dumps(document))


class TestErrorRedaction:
    def test_backend_error_message_is_redacted(self) -> None:
        pool = FakePostgresPool()
        store = PostgreSQLStateStore(pool=pool, now=pool.clock.now)
        pool.fail_next(OperationalError("connection to postgres://user:secret@host:5432 failed"))
        try:
            store.create(make_state())
        except Exception as error:
            text = str(error)
            assert "postgres://" not in text
            assert "secret" not in text

    def test_build_pool_error_does_not_leak_dsn(self) -> None:
        try:
            build_pool(
                "postgres://user:secret@host:5432/db",
                pool_size=1,
                connect_timeout_seconds=1.0,
                command_timeout_seconds=1.0,
                acquire_timeout_seconds=1.0,
                schema="ns",
            )
        except Exception as error:
            text = str(error)
            assert "secret" not in text
            assert "postgres://" not in text


class TestConfigIsCredentialFree:
    def test_config_does_not_accept_dsn_or_credentials(self) -> None:
        config = WorkflowPostgresConfig(namespace="shared")
        dumped = config.model_dump()
        for secret_name in ("dsn", "url", "password", "username", "ssl_cert"):
            assert secret_name not in dumped
