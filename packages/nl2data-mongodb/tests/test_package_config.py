"""Tests for nl2data_mongodb configuration."""

from __future__ import annotations

import pytest

from nl2data_mongodb import MongoAdapterConfig
from nl2data_mongodb.config import MongoProfile


class TestMongoAdapterConfig:
    def test_pymongo_config_can_be_constructed_without_uri(self) -> None:
        config = MongoAdapterConfig(profile=MongoProfile.PY_MONGO)
        assert config.profile == MongoProfile.PY_MONGO

    def test_fake_profile_does_not_require_uri(self) -> None:
        config = MongoAdapterConfig(profile=MongoProfile.FAKE)
        assert config.profile == MongoProfile.FAKE

    def test_uri_reference_env_resolution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NL2DATA_MONGODB_URI", "mongodb://localhost:27017/db")
        config = MongoAdapterConfig(uri_reference="env:NL2DATA_MONGODB_URI", database="db")
        assert config.resolve_uri() == "mongodb://localhost:27017/db"

    def test_uri_reference_plain_resolution(self) -> None:
        config = MongoAdapterConfig(uri_reference="mongodb://localhost:27017/db", database="db")
        assert config.resolve_uri() == "mongodb://localhost:27017/db"

    def test_uri_reference_missing_env_raises(self) -> None:
        config = MongoAdapterConfig(uri_reference="env:MISSING_MONGODB_URI", database="db")
        with pytest.raises(ValueError):
            config.resolve_uri()

    def test_legacy_uri_field_still_works(self) -> None:
        config = MongoAdapterConfig(uri="mongodb://host/db", database="db")
        assert config.resolve_uri() == "mongodb://host/db"

    def test_uri_takes_precedence_over_reference(self) -> None:
        config = MongoAdapterConfig(
            uri="mongodb://legacy/db",
            uri_reference="mongodb://reference/db",
            database="db",
        )
        assert config.resolve_uri() == "mongodb://legacy/db"

    def test_allowlist_entries_must_be_bounded_identifiers(self) -> None:
        with pytest.raises(ValueError):
            MongoAdapterConfig(
                uri="mongodb://host/db",
                database="db",
                allowed_collections={"has space"},
            )

    def test_fingerprint_excludes_secrets(self) -> None:
        config = MongoAdapterConfig(
            uri="mongodb://user:secret@host/db",
            uri_reference="env:VAR",
            database="db",
        )
        payload = config.safe_payload()
        assert payload["uri_reference"] == "env:VAR"
        assert "mongodb://user:secret" not in str(payload)
        assert config.fingerprint().startswith("sha256:")

    def test_bounds_have_sane_defaults(self) -> None:
        config = MongoAdapterConfig(uri="mongodb://host/db", database="db")
        assert config.max_collections == 100
        assert config.max_fields_per_collection == 200
        assert config.max_rows == 100_000
        assert config.max_limit == 1_000_000
        assert config.server_selection_timeout_ms == 3_000
