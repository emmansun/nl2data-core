"""Deterministic memory/multi-turn conformance cases (P2.4).

The dataset exercises the real record boundary, provider scoping,
per-turn revalidation, and stateless fallback path: safe record
creation, raw payload rejection, cross-tenant and cross-conversation
isolation, stale reference denial, retention expiry, deletion,
compaction, stateless fallback, follow-up clarification, fresh
compatible follow-up, and bounded recall.  All records pin the fixed
clock so the dataset fingerprint is stable across runs.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.fixtures.models import TIME_ANCHOR
from nl2data_core.memory.models import (
    MemoryRecallBudget,
    MemoryRecord,
    MemoryScope,
    QueryReference,
    QueryReferencePayload,
)
from nl2data_core.planning.validation import AuthorizedView

from .models import (
    MemoryAssertionKind,
    MemoryConformanceAssertion,
    MemoryConformanceCase,
    MemoryConformanceDataset,
    MemoryConformanceDecision,
)

TENANT_A = "sha256:" + "a1" * 32
TENANT_B = "sha256:" + "b1" * 32
POLICY_A = "sha256:" + "a0" * 32
POLICY_B = "sha256:" + "b0" * 32
CATALOG_A = "sha256:" + "c0" * 32

ALLOWED = MemoryConformanceDecision.ALLOWED
DENIED = MemoryConformanceDecision.DENIED
CLARIFY = MemoryConformanceDecision.CLARIFY

#: The fixed semantic view every case is resolved under.
DEFAULT_CONFORMANCE_VIEW = AuthorizedView(
    source_id="sales",
    root_entity_ids=frozenset({"order"}),
    field_ids=frozenset({"order_id", "amount", "region", "status"}),
    catalog_fingerprint=CATALOG_A,
)
SEMANTIC_VIEW_A = sha256_fingerprint(
    {
        "source_id": DEFAULT_CONFORMANCE_VIEW.source_id,
        "root_entity_ids": sorted(DEFAULT_CONFORMANCE_VIEW.root_entity_ids),
        "field_ids": sorted(DEFAULT_CONFORMANCE_VIEW.field_ids),
        "catalog_fingerprint": DEFAULT_CONFORMANCE_VIEW.catalog_fingerprint,
    }
)

#: Fresh prompt without follow-up dependence.
FRESH_PROMPT = "top 10 order amounts in emea"
#: Follow-up prompt that depends on prior context.
FOLLOW_UP_PROMPT = "same query but only for apac"


def _reference_record(
    record_id: str,
    *,
    policy_fingerprint: str = POLICY_A,
    catalog_fingerprint: str = CATALOG_A,
    tenant_scope_fingerprint: str = TENANT_A,
    conversation_id: str = "conv-a",
    created_at: datetime = TIME_ANCHOR,
    ttl_seconds: int = 86_400,
) -> MemoryRecord:
    return MemoryRecord(
        record_id=record_id,
        scope=MemoryScope(
            tenant_scope_fingerprint=tenant_scope_fingerprint,
            session_id="session-1",
            conversation_id=conversation_id,
            adapter_id="sql",
            source_id="sales",
        ),
        payload=QueryReferencePayload(
            reference=QueryReference(
                reference_id=f"ref-{record_id}",
                intent_fingerprint="sha256:" + "11" * 32,
                ir_fingerprint="sha256:" + "22" * 32,
                artifact_fingerprint="sha256:" + "33" * 32,
                policy_fingerprint=policy_fingerprint,
                catalog_fingerprint=catalog_fingerprint,
                semantic_view_fingerprint=SEMANTIC_VIEW_A,
                adapter_id="sql",
                source_id="sales",
                root_entity_id="order",
                field_ids=frozenset({"order_id", "amount"}),
            )
        ),
        created_at=created_at,
        ttl_seconds=ttl_seconds,
    )


def _expired_record(record_id: str) -> MemoryRecord:
    return _reference_record(
        record_id,
        created_at=TIME_ANCHOR - timedelta(days=2),
        ttl_seconds=86_400,
    )


def _assertion(
    assertion_id: str,
    description: str,
    kind: MemoryAssertionKind,
    *,
    expected_decision: MemoryConformanceDecision | None = None,
    expected_count: int | None = None,
    expected_flag: bool | None = None,
) -> MemoryConformanceAssertion:
    return MemoryConformanceAssertion(
        assertion_id=assertion_id,
        description=description,
        kind=kind,
        expected_decision=expected_decision,
        expected_count=expected_count,
        expected_flag=expected_flag,
    )


def _decided(description: str, decision: MemoryConformanceDecision) -> MemoryConformanceAssertion:
    return _assertion("decision", description, "decision_equals", expected_decision=decision)


def default_memory_conformance_dataset() -> MemoryConformanceDataset:
    """The deterministic default memory conformance dataset."""
    return MemoryConformanceDataset(
        dataset_id="memory-conformance-1",
        name="Memory multi-turn context conformance",
        cases=(
            MemoryConformanceCase(
                case_id="mc-safe-record-creation",
                name="safe record creation and recall",
                kind="safe_record_creation",
                prompt=FRESH_PROMPT,
                conversation_id="conv-a",
                records=(_reference_record("mem-1"),),
                turn_tenant_scope_fingerprint=TENANT_A,
                turn_policy_fingerprint=POLICY_A,
                turn_catalog_fingerprint=CATALOG_A,
                mandatory_assertions=(
                    _decided("safe records resolve allowed", ALLOWED),
                    _assertion("recalled", "one record recalled", "recalled_count_equals",
                               expected_count=1),
                    _assertion("redacted", "evidence is protected", "evidence_redacted"),
                ),
            ),
            MemoryConformanceCase(
                case_id="mc-raw-payload-rejection",
                name="raw payload is rejected at the record boundary",
                kind="raw_payload_rejection",
                prompt=FRESH_PROMPT,
                raw_payload={
                    "record_id": "mem-raw-1",
                    "scope": {
                        "tenant_scope_fingerprint": TENANT_A,
                        "session_id": "session-1",
                        "conversation_id": "conv-a",
                    },
                    "payload": {
                        "payload_kind": "working",
                        "label": "note",
                        "detail": "SELECT * FROM orders WHERE region = 'emea'",
                    },
                    "created_at": TIME_ANCHOR.isoformat(),
                    "ttl_seconds": 86_400,
                },
                mandatory_assertions=(
                    _decided("raw payloads are denied", DENIED),
                    _assertion("no-raw", "rejected with a normalized code", "no_raw_payload"),
                    _assertion("redacted", "evidence is protected", "evidence_redacted"),
                ),
            ),
            MemoryConformanceCase(
                case_id="mc-cross-tenant-isolation",
                name="another tenant never recalls the reference",
                kind="cross_tenant_isolation",
                prompt=FRESH_PROMPT,
                conversation_id="conv-a",
                records=(_reference_record("mem-2"),),
                turn_tenant_scope_fingerprint=TENANT_B,
                turn_policy_fingerprint=POLICY_A,
                turn_catalog_fingerprint=CATALOG_A,
                mandatory_assertions=(
                    _decided("foreign tenant resolves statelessly", ALLOWED),
                    _assertion("recalled", "no record recalled", "recalled_count_equals",
                               expected_count=0),
                    _assertion("redacted", "evidence is protected", "evidence_redacted"),
                ),
            ),
            MemoryConformanceCase(
                case_id="mc-conversation-isolation",
                name="another conversation never recalls the reference",
                kind="conversation_isolation",
                prompt=FRESH_PROMPT,
                conversation_id="conv-b",
                records=(_reference_record("mem-3"),),
                turn_tenant_scope_fingerprint=TENANT_A,
                turn_policy_fingerprint=POLICY_A,
                turn_catalog_fingerprint=CATALOG_A,
                mandatory_assertions=(
                    _decided("foreign conversation resolves statelessly", ALLOWED),
                    _assertion("recalled", "no record recalled", "recalled_count_equals",
                               expected_count=0),
                    _assertion("redacted", "evidence is protected", "evidence_redacted"),
                ),
            ),
            MemoryConformanceCase(
                case_id="mc-stale-reference-denial",
                name="stale policy reference is denied with clarification",
                kind="stale_reference_denial",
                prompt=FOLLOW_UP_PROMPT,
                conversation_id="conv-a",
                records=(_reference_record("mem-4"),),
                turn_tenant_scope_fingerprint=TENANT_A,
                turn_policy_fingerprint=POLICY_B,
                turn_catalog_fingerprint=CATALOG_A,
                mandatory_assertions=(
                    _decided("stale references clarify", CLARIFY),
                    _assertion("stale", "one stale reference reported", "stale_count_equals",
                               expected_count=1),
                    _assertion("recalled", "record still visible to recall",
                               "recalled_count_equals", expected_count=1),
                    _assertion("redacted", "evidence is protected", "evidence_redacted"),
                ),
            ),
            MemoryConformanceCase(
                case_id="mc-retention-expiry",
                name="expired records are never recalled",
                kind="retention_expiry",
                prompt=FRESH_PROMPT,
                conversation_id="conv-a",
                records=(_expired_record("mem-5"),),
                turn_tenant_scope_fingerprint=TENANT_A,
                turn_policy_fingerprint=POLICY_A,
                turn_catalog_fingerprint=CATALOG_A,
                mandatory_assertions=(
                    _decided("expired memory resolves statelessly", ALLOWED),
                    _assertion("recalled", "no expired record recalled", "recalled_count_equals",
                               expected_count=0),
                    _assertion("redacted", "evidence is protected", "evidence_redacted"),
                ),
            ),
            MemoryConformanceCase(
                case_id="mc-deletion",
                name="deleted records are gone from recall",
                kind="deletion",
                prompt=FRESH_PROMPT,
                conversation_id="conv-a",
                records=(_reference_record("mem-6"), _reference_record("mem-7")),
                delete_record_ids=("mem-7",),
                turn_tenant_scope_fingerprint=TENANT_A,
                turn_policy_fingerprint=POLICY_A,
                turn_catalog_fingerprint=CATALOG_A,
                mandatory_assertions=(
                    _decided("remaining record resolves allowed", ALLOWED),
                    _assertion("recalled", "only the remaining record recalled",
                               "recalled_count_equals", expected_count=1),
                    _assertion("redacted", "evidence is protected", "evidence_redacted"),
                ),
            ),
            MemoryConformanceCase(
                case_id="mc-compaction",
                name="compaction drops expired records",
                kind="compaction",
                prompt=FRESH_PROMPT,
                conversation_id="conv-a",
                records=(_expired_record("mem-8"), _expired_record("mem-9")),
                compact=True,
                turn_tenant_scope_fingerprint=TENANT_A,
                turn_policy_fingerprint=POLICY_A,
                turn_catalog_fingerprint=CATALOG_A,
                mandatory_assertions=(
                    _decided("compacted memory resolves statelessly", ALLOWED),
                    _assertion("compacted", "two records compacted", "compacted_count_equals",
                               expected_count=2),
                    _assertion("recalled", "no record recalled", "recalled_count_equals",
                               expected_count=0),
                    _assertion("redacted", "evidence is protected", "evidence_redacted"),
                ),
            ),
            MemoryConformanceCase(
                case_id="mc-stateless-fallback",
                name="unavailable memory degrades statelessly",
                kind="stateless_fallback",
                prompt=FRESH_PROMPT,
                provider_available=False,
                conversation_id="conv-a",
                turn_tenant_scope_fingerprint=TENANT_A,
                turn_policy_fingerprint=POLICY_A,
                turn_catalog_fingerprint=CATALOG_A,
                mandatory_assertions=(
                    _decided("fresh requests still resolve", ALLOWED),
                    _assertion("unavailable", "memory unavailable recorded",
                               "memory_unavailable_equals", expected_flag=True),
                    _assertion("recalled", "no record recalled", "recalled_count_equals",
                               expected_count=0),
                    _assertion("redacted", "evidence is protected", "evidence_redacted"),
                ),
            ),
            MemoryConformanceCase(
                case_id="mc-followup-clarification",
                name="follow-up with unavailable memory clarifies",
                kind="followup_clarification",
                prompt=FOLLOW_UP_PROMPT,
                provider_available=False,
                conversation_id="conv-a",
                turn_tenant_scope_fingerprint=TENANT_A,
                turn_policy_fingerprint=POLICY_A,
                turn_catalog_fingerprint=CATALOG_A,
                mandatory_assertions=(
                    _decided("dependent follow-up clarifies", CLARIFY),
                    _assertion("unavailable", "memory unavailable recorded",
                               "memory_unavailable_equals", expected_flag=True),
                    _assertion("redacted", "evidence is protected", "evidence_redacted"),
                ),
            ),
            MemoryConformanceCase(
                case_id="mc-fresh-compatible-followup",
                name="compatible follow-up projects recalled context",
                kind="fresh_compatible_followup",
                prompt=FOLLOW_UP_PROMPT,
                conversation_id="conv-a",
                records=(_reference_record("mem-10"),),
                turn_tenant_scope_fingerprint=TENANT_A,
                turn_policy_fingerprint=POLICY_A,
                turn_catalog_fingerprint=CATALOG_A,
                mandatory_assertions=(
                    _decided("compatible follow-up resolves allowed", ALLOWED),
                    _assertion("recalled", "one record recalled", "recalled_count_equals",
                               expected_count=1),
                    _assertion("redacted", "evidence is protected", "evidence_redacted"),
                ),
            ),
            MemoryConformanceCase(
                case_id="mc-bounded-recall",
                name="recall respects the configured budget",
                kind="bounded_recall",
                prompt=FOLLOW_UP_PROMPT,
                conversation_id="conv-a",
                records=(
                    _reference_record("mem-b1"),
                    _reference_record("mem-b2"),
                    _reference_record("mem-b3"),
                ),
                budget=MemoryRecallBudget(max_records=2),
                turn_tenant_scope_fingerprint=TENANT_A,
                turn_policy_fingerprint=POLICY_A,
                turn_catalog_fingerprint=CATALOG_A,
                mandatory_assertions=(
                    _decided("bounded recall still resolves", ALLOWED),
                    _assertion("recalled", "two records recalled", "recalled_count_equals",
                               expected_count=2),
                    _assertion("truncated", "recall truncated by budget", "truncated_equals",
                               expected_flag=True),
                    _assertion("redacted", "evidence is protected", "evidence_redacted"),
                ),
            ),
        ),
    )
