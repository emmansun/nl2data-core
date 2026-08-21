## Why

P2.1 can resolve one natural-language request into structured intent and P2.2/P2.3 provide trusted tenant scope and durable workflow state, but follow-up questions still have no controlled context. A multi-turn feature must remember logical scope and confirmed meaning without storing transcripts, raw query results, credentials, or stale authorization decisions.

P2.4 adds a bounded Memory boundary that supplies context only; every turn must revalidate current tenant, semantic view, policy, and adapter artifact conditions before execution.

## What Changes

- Add provider-neutral immutable Memory records for working context, session summaries, query references, semantic decisions, and audit references.
- Add tenant/principal/session/conversation/adapter/source scoping and opaque namespace binding using the P2.2 scope fingerprint.
- Add a replaceable Memory provider protocol with deterministic in-memory implementation and optional durable integration through later changes.
- Add append, recall, compare-and-set, compact, expire, and delete operations with bounded record counts, context budgets, and retention TTLs.
- Store logical intent, plan/artifact/policy/catalog fingerprints, clarification decisions, and protected outcome references instead of raw prompts, SQL/MQL, rows, documents, secrets, or native objects.
- Add multi-turn context selection and reference resolution for follow-up requests, with stateless safe degradation when Memory is unavailable.
- Re-evaluate tenant scope, semantic authorization, policy fingerprint, catalog fingerprint, and artifact freshness on every turn; stale memory cannot authorize execution.
- Add deterministic Memory and multi-turn evaluation cases for scope isolation, stale authorization, compaction, expiry, deletion, and safe degradation.
- Defer vector stores, Redis implementation, autonomous learning, production transcript retention, and result-set caching.

## Capabilities

### New Capabilities

- `governed-memory`: Safe typed Memory records, provider protocol, bounded lifecycle, retention, and tenant isolation.
- `multiturn-context-resolution`: Authorized recall and logical follow-up resolution with per-turn revalidation and stateless fallback.
- `memory-conformance`: Deterministic Memory security, retention, stale-reference, and multi-turn evaluation.

### Modified Capabilities

None.

## Impact

- Adds internal Memory models, provider protocols, deterministic store, context selector, and multi-turn resolver under `src/nl2data_core/memory/`.
- Integrates with existing AI intent resolution, tenant scope, semantic plan fingerprints, governance policy fingerprints, and durable workflow references.
- Adds no vector database, external service, provider SDK, or public transport dependency.
- Adds contract, security, integration, retention, and evaluation tests.