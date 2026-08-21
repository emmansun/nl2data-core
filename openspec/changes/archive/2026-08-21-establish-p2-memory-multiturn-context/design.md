## Context

P2.1 resolves one natural-language request into a structured intent, P2.2 provides trusted tenant scope, and P2.3 provides durable workflow checkpoints and idempotency. The system still has no bounded representation for follow-up context such as a confirmed clarification or a logical reference to a prior approved query.

Memory must remain a context provider rather than an authority. It may suggest prior semantic intent and protected references, but every turn must rebuild or validate the current intent against the current tenant scope, authorized semantic view, policy snapshot, catalog snapshot, and adapter artifact.

## Goals / Non-Goals

**Goals:**

- Define immutable, typed, provider-neutral Memory records and a replaceable Memory provider.
- Support working, session, query-reference, semantic-decision, and audit-reference records with bounded TTL and size.
- Scope every record by tenant scope, principal/session/conversation, adapter, and source where applicable.
- Recall only safe logical context and protected fingerprints, never raw prompts, queries, rows, documents, credentials, or native objects.
- Resolve follow-up references into bounded context for the P2.1 intent resolver and revalidate every turn.
- Provide deterministic in-memory behavior and safe degradation when memory is unavailable.

**Non-Goals:**

- Vector search, embeddings, Redis, production transcript retention, or autonomous learning.
- Result-set caching or authorization bypass through remembered plans.
- Public HTTP memory endpoints or identity-provider integration.

## Decisions

1. **Use immutable records plus a provider port.** Records are append-only logical facts and references; providers implement append, recall, compare-and-set, compact, expire, and delete. A transcript-first API was rejected because it encourages raw prompt/result retention.

2. **Store logical references, not raw data.** A query reference contains intent/plan/artifact/policy/catalog fingerprints and bounded semantic identifiers. A semantic decision contains the confirmed interpretation and its fingerprint. Raw result rows and documents are never accepted by the record models.

3. **Bind records to trusted scope and conversation identity.** Every record includes the P2.2 scope fingerprint plus bounded session/conversation namespace. Scope mismatch returns no records or a structured conflict; it never broadens recall.

4. **Revalidate on every turn.** The multi-turn resolver uses recalled context only to assemble provider-safe context. It requires current scope, policy, catalog, and semantic-view fingerprints to match before inheriting logical references. Stale records are ignored or require clarification; they cannot authorize execution.

5. **Use bounded deterministic in-memory storage first.** P2.4 provides an in-memory provider for contract and integration tests. Durable storage can be added later using P2.3 primitives without changing record semantics.

6. **Safe degradation is explicit.** If Memory is unavailable, the resolver may proceed with a stateless request when the request is independently resolvable. It must not invent remembered context or silently use stale records.

## Risks / Trade-offs

- [Risk] A logical reference can still encode sensitive semantic identifiers. → Store only authorized IDs and fingerprints, apply record classification, and protect evidence serialization.
- [Risk] Revalidation may make older follow-up references unusable after policy/catalog changes. → Return a bounded clarification/stale-context result rather than silently using old authorization.
- [Risk] In-memory storage cannot survive process restart. → Keep the provider replaceable and defer durable Memory integration to a later change.
- [Risk] Multi-turn context can increase model input size. → Enforce record count, field count, character, and token budgets before provider invocation.

## Migration Plan

1. Add Memory contracts and deterministic provider without changing stateless P2.1 behavior.
2. Add safe query-reference and semantic-decision records with tenant scope binding.
3. Add multi-turn context assembly and per-turn validation before intent resolution.
4. Add conformance cases for isolation, stale references, expiry, deletion, and unavailable Memory.
5. Roll back by disabling Memory injection; stateless intent resolution remains available.

## Open Questions

- Which record classes should be durable first when Memory persistence is added?
- Should stale policy/catalog references always request clarification or be silently dropped when the prompt is independently clear?
- What host policy controls whether session summaries may contain user-authored labels?