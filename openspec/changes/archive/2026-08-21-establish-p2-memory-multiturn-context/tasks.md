## 1. Memory Contracts and Safety

- [x] 1.1 Define immutable Memory record models for working, session, query-reference, semantic-decision, and audit-reference data with bounded fields, TTL, scope, and fingerprints.
- [x] 1.2 Define safe logical reference models that accept intent/plan/artifact/policy/catalog fingerprints but reject prompts, SQL/MQL, rows/documents, secrets, and native objects.
- [x] 1.3 Define Memory provider, recall projection, compare-and-set, compaction, expiry, deletion, and normalized unavailable-error protocols.
- [x] 1.4 Add contract tests for immutability, raw payload rejection, deterministic fingerprints, scope binding, and bounded record/context sizes.

## 2. Deterministic Memory Provider

- [x] 2.1 Implement a tenant/session/conversation-scoped in-memory provider with append, recall, compare-and-set, compact, expire, and delete operations.
- [x] 2.2 Enforce scope mismatch denial and deterministic ordering without exposing records from another tenant or conversation.
- [x] 2.3 Enforce record TTL, recall count/character/token budgets, and safe provider-unavailable behavior.
- [x] 2.4 Add tests for append/recall, optimistic conflicts, expiry, deletion, compaction, cross-scope isolation, and provider failure.

## 3. Multi-turn Context Resolution

- [x] 3.1 Define current-turn context containing trusted tenant scope, session/conversation IDs, policy/catalog/semantic fingerprints, and adapter identity.
- [x] 3.2 Implement recall projection into P2.1 provider context using only authorized logical records and protected references.
- [x] 3.3 Revalidate tenant scope, policy/catalog fingerprints, semantic view, adapter/artifact references, and record expiry on every turn.
- [x] 3.4 Implement follow-up reference resolution for prior query scope and clarification decisions without directly executing a recalled plan.
- [x] 3.5 Implement stateless fallback when Memory is unavailable and clarification/safe rejection when the prompt depends on missing or stale history.
- [x] 3.6 Add integration tests for compatible follow-up, stale policy/catalog, cross-tenant reference, expired memory, and unavailable provider cases.

## 4. Conformance and Evidence

- [x] 4.1 Define deterministic Memory/multi-turn dataset, protected evidence, mandatory assertions, and report models.
- [x] 4.2 Add conformance cases for raw payload rejection, scope isolation, stale reference denial, retention, deletion, compaction, and stateless fallback.
- [x] 4.3 Ensure conformance reports contain only fingerprints, safe decision codes, bounded metadata, and normalized errors.
- [x] 4.4 Add repeatability tests proving equal records, scope, fingerprints, and fixed clock produce equal evidence and reports.

## 5. Workflow Integration and Quality Gates

- [x] 5.1 Add opt-in Memory injection to the AI workflow while preserving stateless P2.1 behavior when Memory is absent.
- [x] 5.2 Ensure no recalled memory path bypasses current governance, plan validation, artifact authorization, or result protection.
- [x] 5.3 Run complete P0/P1/P2.1/P2.2/P2.3 tests plus Memory security, retention, integration, Ruff, Mypy, and package-install checks.
- [x] 5.4 Document that Memory is context only, raw result caching is unsupported, and durable/Redis providers remain future work.