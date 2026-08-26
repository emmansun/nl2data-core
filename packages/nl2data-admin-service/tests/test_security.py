"""Security tests for the admin service."""

from __future__ import annotations

from helpers import Permission, _FakeDependencies, _make_auth, _make_snapshot
from nl2data_admin_service.config import AdminServiceConfig
from nl2data_admin_service.service import AdminService


def test_snapshot_dto_does_not_expose_credentials() -> None:
    deps = _FakeDependencies()
    service = AdminService(deps, AdminServiceConfig())
    snapshot = _make_snapshot()
    deps.catalog.register_snapshot(snapshot, tenant_scope_fingerprint="sha256:" + "0" * 64)
    result = service.get_snapshot(
        snapshot.fingerprint, auth_context=_make_auth([Permission.SNAPSHOT_READ])
    )
    payload = result.model_dump()
    values = str(payload)
    assert "postgres://" not in values
    assert "mongodb://" not in values
    assert "secret=" not in values
    assert "password=" not in values
    assert "dsn" not in values.lower()
