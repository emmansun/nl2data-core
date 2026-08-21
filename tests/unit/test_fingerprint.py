"""Unit tests for artifact fingerprint stability and secret exclusion."""

from __future__ import annotations

from nl2data_core.adapters.fingerprint import artifact_fingerprint, safe_artifact_payload


class TestStableFingerprints:
    def test_same_payload_same_fingerprint(self) -> None:
        payload = {"query": "select 1", "adapter": "sql", "snapshot": "s1"}
        assert artifact_fingerprint(payload) == artifact_fingerprint(
            dict(reversed(list(payload.items())))
        )

    def test_fingerprint_format(self) -> None:
        fingerprint = artifact_fingerprint({"a": 1})
        assert fingerprint.startswith("sha256:")
        assert len(fingerprint) == 7 + 64
        assert fingerprint[7:].islower()

    def test_different_payload_different_fingerprint(self) -> None:
        assert artifact_fingerprint({"a": 1}) != artifact_fingerprint({"a": 2})


class TestSecretExclusion:
    def test_credentials_excluded(self) -> None:
        with_secret = artifact_fingerprint({"query": "q", "password": "hunter2", "api_key": "k"})
        without_secret = artifact_fingerprint({"query": "q"})
        assert with_secret == without_secret

    def test_unapproved_tenant_identifier_excluded(self) -> None:
        fingerprint = artifact_fingerprint({"query": "q", "tenant_id": "acme-123"})
        assert fingerprint == artifact_fingerprint({"query": "q"})

    def test_approved_tenant_identifier_included(self) -> None:
        payload = {"query": "q", "tenant_id": "acme-123"}
        approved = artifact_fingerprint(payload, approved_tenant_keys=frozenset({"tenant_id"}))
        unapproved = artifact_fingerprint(payload)
        assert approved != unapproved

    def test_nested_secrets_excluded(self) -> None:
        payload = {"artifact": {"statement": "q", "credentials": {"password": "p"}}}
        assert artifact_fingerprint(payload) == artifact_fingerprint(
            {"artifact": {"statement": "q"}}
        )

    def test_secrets_nested_in_sequences_are_excluded(self) -> None:
        payload = {
            "items": [{"password": "p", "value": "ok"}],
            "nested": ({"api_key": "k", "value": "ok"},),
        }
        cleaned = safe_artifact_payload(payload)
        assert cleaned == {
            "items": [{"value": "ok"}],
            "nested": ({"value": "ok"},),
        }
        assert artifact_fingerprint(payload) == artifact_fingerprint(
            {"items": [{"value": "ok"}], "nested": ({"value": "ok"},)}
        )

    def test_safe_payload_has_no_secrets(self) -> None:
        cleaned = safe_artifact_payload({"query": "q", "token": "t", "tenant_id": "x"})
        assert "token" not in cleaned
        assert "tenant_id" not in cleaned
        assert cleaned == {"query": "q"}
