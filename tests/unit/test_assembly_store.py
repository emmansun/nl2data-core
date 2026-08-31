"""Unit tests for tenant-scoped assembly draft compare-and-swap storage."""

from __future__ import annotations

import pytest

from nl2data_core.assembly import (
    ASSEMBLY_API_VERSION,
    AssemblyDraft,
    DraftRevisionConflict,
    InMemoryAssemblyDraftStore,
)


def draft(*, revision: int = 0) -> AssemblyDraft:
    return AssemblyDraft(
        apiVersion=ASSEMBLY_API_VERSION,
        draft_id="draft-1",
        bundle_id="sales",
        source_id="sales",
        model_version="1.0.0",
        draft_revision=revision,
        author_reference="author-1",
    )


def test_store_is_tenant_scoped() -> None:
    store = InMemoryAssemblyDraftStore()
    store.create(draft(), tenant_scope_fingerprint="tenant-a")
    assert store.get("draft-1", tenant_scope_fingerprint="tenant-a") is not None
    assert store.get("draft-1", tenant_scope_fingerprint="tenant-b") is None


def test_replace_enforces_current_and_resulting_revisions() -> None:
    store = InMemoryAssemblyDraftStore()
    original = draft()
    store.create(original, tenant_scope_fingerprint="tenant-a")
    changed = original.mutate(expected_revision=0, author_reference="author-2")
    store.replace(
        changed,
        expected_revision=0,
        tenant_scope_fingerprint="tenant-a",
    )
    with pytest.raises(DraftRevisionConflict):
        store.replace(
            changed,
            expected_revision=0,
            tenant_scope_fingerprint="tenant-a",
        )


def test_duplicate_create_fails_closed() -> None:
    store = InMemoryAssemblyDraftStore()
    store.create(draft(), tenant_scope_fingerprint="tenant-a")
    with pytest.raises(ValueError, match="already exists"):
        store.create(draft(), tenant_scope_fingerprint="tenant-a")