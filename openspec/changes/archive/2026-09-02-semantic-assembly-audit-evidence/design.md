## Context

The semantic control plane now has separate gates for authoring/import, lint, assertion review, approval, Verification Suite, publication, activation, and rollback. Each gate already preserves some safe evidence or audit reference, but hosts still lack one coherent, queryable audit-evidence view that explains the lifecycle of a draft or published Bundle end to end.

The existing publication aggregate and `FrozenReleaseBinding` provide the right immutable boundary for publish-time facts. This change builds on that boundary by defining a safe assembly audit-evidence trail that links lifecycle events without entering the semantic Bundle fingerprint domain or exposing internal mutable models.

## Goals / Non-Goals

**Goals:**

- Define bounded immutable audit-evidence entry and trail summary contracts for assembly lifecycle events.
- Link authoring/import, lint, assertion review, approval, verification, publish, activation, and rollback through stable safe references.
- Persist audit-evidence envelopes with catalog lifecycle/publication records and validate them on reload.
- Add Admin inspection APIs for draft, assertion, bundle, publication, activation, and rollback history.
- Preserve tenant/source isolation, canonical fingerprinting, redaction, and control-plane dependency boundaries.

**Non-Goals:**

- No raw event log streaming backend, SIEM integration, or vendor-specific audit sink.
- No change to semantic Bundle fingerprints, Verification Suite pass/fail policy, or lifecycle authorization decisions.
- No exposure of raw prompts, SQL/MQL, physical names, credentials, resolved deployment values, unrestricted sample values, native objects, raw backend exceptions, or raw operator identities.
- No attempt to reconstruct complete historical state from mutable drafts after publication; publication history is validated against immutable bindings.

## Decisions

1. **Use immutable audit-evidence entries, not mutable draft reconstruction.**
   - Each entry records event kind, subject reference, tenant/source scope fingerprints, revision/fingerprint bindings, safe outcome, optional operator audit reference, predecessor references, and entry fingerprint.
   - Rationale: audit inspection must keep working after drafts evolve, are superseded, or are no longer loaded.
   - Alternative considered: compute audit history from the current draft plus publication rows. Rejected because later draft edits would distort historical evidence.

2. **Keep lifecycle audit evidence outside semantic identity.**
   - Audit entries and trail summaries are linked from publish/activation records but never included in semantic Bundle canonical payloads.
   - Rationale: the existing semantic fingerprint contract excludes lifecycle metadata; audit history must not change semantic identity.
   - Alternative considered: include an audit-trail fingerprint in Bundle payload. Rejected because it would make identical semantic content produce different Bundle fingerprints.

3. **Separate operator audit references from evidence facts.**
   - Operator identity remains an opaque host-provided audit reference. Evidence facts are bounded system facts such as assertion ID, reviewed payload hash, plan fingerprint, evidence fingerprint, policy profile, and status.
   - Rationale: core should enforce lifecycle and evidence integrity without owning authentication identity semantics.
   - Alternative considered: persist raw operator identity fields. Rejected because host identity policy is outside core and may be sensitive.

4. **Publish-time audit evidence binds release readiness inputs.**
   - Publication entries bind approved draft revision, accepted manifest fingerprint, Verification Suite evidence fingerprint, selected verification policy, lint readiness reference when present, separation-of-duties outcome, tenant/source scope, and Bundle fingerprint.
   - Rationale: release operators need one safe summary explaining why a semantic Bundle became authoritative.
   - Alternative considered: leave lint and verification evidence as separate inspect calls only. Rejected because it makes release readiness hard to explain and easy to mismatch.

5. **Admin inspection reads bounded trails by subject.**
   - Admin supports lookup by draft ID/revision, assertion ID, bundle fingerprint, publication reference, activation reference, and rollback reference. Results are ordered, bounded, scoped, and redacted.
   - Rationale: authoring tools, release pipelines, and production operators need different entry points into the same evidence graph.

6. **Durable catalog stores versioned envelopes and validates cross-links.**
   - PostgreSQL persistence treats audit-evidence entries like other safe lifecycle envelopes: schema-versioned, fingerprinted, tenant/source scoped, and checked against immutable publication aggregate records on reload.
   - Rationale: audit trails are part of explainability and must survive restarts without trusting mutable rows.

## Risks / Trade-offs

- [Risk] Audit evidence becomes a second lifecycle state machine. -> Entries are summaries of completed lifecycle actions and cannot approve, publish, activate, or change state.
- [Risk] Trails grow without bound. -> Admin queries require bounded limits/cursors, and catalog retention must preserve active/publication dependencies while allowing explicit inactive cleanup.
- [Risk] Cross-link validation blocks legacy records. -> Provide explicit compatibility classification for legacy publications missing audit-evidence entries; never fabricate production-valid evidence from mutable drafts.
- [Risk] Redaction removes too much operator context. -> Preserve opaque host audit references and safe role/outcome fields while leaving identity resolution to host systems.
- [Risk] Coupling grows across control-plane modules. -> Route audit evidence through core contracts and catalog/Admin ports; optional packages import inward only.

## Migration Plan

- Add core audit-evidence contracts and public exports without changing existing lifecycle behavior.
- Add in-memory catalog support and tests for entry creation, ordering, fingerprinting, and bounded lookup.
- Extend publication aggregate construction to include audit-evidence references while preserving existing publish outcomes.
- Add PostgreSQL envelopes/repositories/migrations for durable audit evidence and reload validation.
- Add Admin DTOs and inspection methods, then update schema/capability output.
- Existing publications without full entries remain readable through an explicit legacy compatibility classification until backfilled or superseded.

## Open Questions

- Should lint readiness references be optional forever, or required for production publications after lint profiles stabilize?
- Should custom host lifecycle events be supported as opaque extension entries, or should v1 allow only core-defined event kinds?