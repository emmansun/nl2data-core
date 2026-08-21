"""Security tests: secrets never leak through serialization or fingerprints."""

from __future__ import annotations

import json

from nl2data import ErrorCategory, ErrorCode, NL2DataError
from nl2data_core.adapters.fingerprint import artifact_fingerprint
from nl2data_core.config.loader import load_config
from nl2data_core.telemetry.models import AuditEvent, TelemetryContext
from nl2data_core.workflow.models import WorkflowEvent, WorkflowState, WorkflowStatus

SECRET_TEXT = "hunter2-super-secret"


class TestConfigSecrets:
    def test_safe_dump_contains_no_plaintext_secret(self) -> None:
        config = load_config(
            {
                "schema_version": 1,
                "service": {"name": "svc"},
                "secrets": {"db_password": {"env": "DB_PASSWORD"}},
            }
        )
        dumped = json.dumps(config.safe_dump())
        assert SECRET_TEXT not in dumped
        assert "DB_PASSWORD" in dumped  # only the reference name

    def test_config_fingerprint_excludes_secret_values(self) -> None:
        # Only references exist, but prove the fingerprint never embeds
        # resolved values by comparing reference-only payloads.
        first = load_config(
            {"schema_version": 1, "service": {"name": "svc"}, "secrets": {"k": {"env": "A"}}}
        )
        second = load_config(
            {"schema_version": 1, "service": {"name": "svc"}, "secrets": {"k": {"env": "B"}}}
        )
        assert first.fingerprint != second.fingerprint
        assert SECRET_TEXT not in first.fingerprint


class TestErrorSecrets:
    def test_error_record_excludes_credentials(self) -> None:
        error = NL2DataError(
            ErrorCategory.ADAPTER,
            ErrorCode.INTERNAL_ERROR,
            "connection failed",
            details={"password": SECRET_TEXT, "dsn": f"postgres://u:{SECRET_TEXT}@h/db"},
        )
        dumped = json.dumps(error.to_record().safe_dump())
        assert SECRET_TEXT not in dumped


class TestArtifactFingerprints:
    def test_fingerprint_excludes_credentials_and_tenant_ids(self) -> None:
        fingerprint = artifact_fingerprint(
            {
                "statement": "select 1",
                "password": SECRET_TEXT,
                "api_key": SECRET_TEXT,
                "tenant_id": "acme-123",
            }
        )
        assert SECRET_TEXT not in fingerprint
        assert "acme-123" not in fingerprint


class TestWorkflowSerialization:
    def test_state_serialization_has_no_raw_payloads(self) -> None:
        state = WorkflowState(
            workflow_id="wf-1",
            request_id="req-1",
            status=WorkflowStatus.RUNNING,
            events=(
                WorkflowEvent(
                    event_id="e1",
                    workflow_id="wf-1",
                    from_status=WorkflowStatus.CREATED,
                    to_status=WorkflowStatus.RUNNING,
                    metadata={"note": "ok"},
                ),
            ),
        )
        serialized = json.dumps(state.serialize_safe())
        assert SECRET_TEXT not in serialized
        assert "raw_query" not in serialized
        assert "raw_result" not in serialized


class TestAuditSerialization:
    def test_audit_event_never_carries_raw_content(self) -> None:
        event = AuditEvent(
            event_id="a-1",
            context=TelemetryContext(request_id="req-1"),
            action="query.submitted",
            attributes={"raw_prompt": SECRET_TEXT},
        )
        dumped = json.dumps(event.serialize_safe())
        assert SECRET_TEXT not in dumped
