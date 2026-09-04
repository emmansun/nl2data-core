## Context

The repository uses `nl2data_core.canonical.canonical_json` and `sha256_fingerprint` across IR, Bundle, verification, audit, catalog, workflow, memory, and optional package envelopes. The helper currently provides deterministic key ordering and compact JSON, but it also accepts Python-specific values such as sets and datetimes, coerces unknown objects with `str()`, and does not define an explicit canonicalization version or JCS/RFC 8785 compatibility boundary.

As semantic assembly audit evidence and production catalog records become more inspectable, fingerprints need a stricter contract. Contributors need to know which payloads are fingerprint-critical, which values are forbidden, which normalizations happen before canonicalization, and how historical fingerprints remain explainable if the canonicalization algorithm changes.

## Goals / Non-Goals

**Goals:**

- Define a shared JCS-compatible canonical JSON contract for fingerprint-critical payloads.
- Make unsupported values fail closed instead of silently stringifying native objects.
- Keep `sha256:<lowercase hex>` as the public fingerprint representation.
- Add canonicalization version metadata where persisted evidence/envelopes need future reload compatibility.
- Add golden vectors for representative IR, Bundle, verification evidence, audit evidence, catalog envelope, and workflow snapshot payloads.
- Preserve existing historical records through explicit legacy classification or additive migration.

**Non-Goals:**

- No change to semantic meaning, authorization policy, Verification Suite requirements, lifecycle state machines, or publication idempotency semantics beyond canonicalization metadata.
- No exposure of canonical bytes through Admin responses unless a separate API explicitly allows bounded contributor diagnostics.
- No support for arbitrary Python objects, decimal extensions, NaN/Infinity, datetimes, sets, bytes, or callables inside fingerprint-critical payloads.
- No best-effort repair of already unsafe persisted payloads.

## Decisions

1. **Introduce strict JCS-compatible entry points while preserving compatibility wrappers.**
   - New strict helpers accept only JSON object, array, string, integer, finite number, boolean, and null values already prepared by domain models.
   - Existing helper names can delegate to strict helpers after compatibility migration, but fingerprint domains must call the strict path or documented wrappers.
   - Alternative considered: patch the existing helper in place immediately. Rejected because it risks silent historical fingerprint drift without a migration checkpoint.

2. **Separate model normalization from canonicalization.**
   - Domain models own conversion of datetimes, enums, sets, tuples, and constrained scalars into JSON-safe payloads before canonicalization. The canonicalizer does not stringify or invent representations.
   - Rationale: hidden coercion makes fingerprints depend on Python implementation details and object `__str__` methods.
   - Alternative considered: keep recursive Python convenience normalization. Rejected for fingerprint-critical domains because it hides unsafe data-shape bugs.

3. **Use a documented JCS profile for string, key, and number behavior.**
   - Canonicalization emits UTF-8 JSON without BOM, sorts object member names lexicographically by Unicode code point, uses minimal JSON whitespace, rejects duplicate object names after model preparation, rejects non-finite numbers, and escapes strings according to the chosen JCS-compatible encoder.
   - Unicode normalization, when needed, is an explicit model validation/preparation rule before canonicalization rather than a hidden serializer side effect.
   - Alternative considered: retain NFC normalization inside the serializer. Rejected because JCS canonicalization must not unexpectedly change string values.

4. **Version persisted canonicalization contracts.**
   - Persisted envelopes and evidence that need long-term reload validation carry canonicalization algorithm/profile metadata, such as `jcs-v1` or explicit legacy profile identifiers.
   - Rationale: future algorithm changes must be visible and fail closed where compatibility is unknown.
   - Alternative considered: infer legacy behavior from timestamps or package versions. Rejected because it is brittle and hard to audit.

5. **Golden vectors protect representative fingerprint domains.**
   - Tests pin canonical bytes and fingerprints for common payloads and edge cases: key ordering, nested arrays, Unicode strings, integer/number rendering, unsafe native values, duplicate prepared keys, and historical compatibility records.
   - Rationale: canonicalization bugs are rarely caught by behavior tests until persisted identities drift.

6. **Architecture tests enforce one canonical owner.**
   - Fingerprint producers import canonicalization from the core owner. Duplicate serializers or local `json.dumps(... sort_keys=True ...)` in fingerprint domains are rejected or explicitly allowlisted for non-identity presentation only.
   - Rationale: local serializers make identity drift easy to introduce.

## Risks / Trade-offs

- [Risk] Strict validation breaks callers that relied on native-object stringification. -> Add characterization tests, targeted migration tasks, and compatibility wrappers for non-fingerprint presentation before switching fingerprint domains.
- [Risk] JCS number rendering differs from Python `json.dumps`. -> Pin golden vectors and decide whether to implement the required formatting locally or use a vetted dependency.
- [Risk] Existing published fingerprints cannot be recomputed under strict JCS. -> Classify legacy records explicitly and preserve their stored identity; do not silently relabel legacy records as JCS-valid.
- [Risk] Hidden Unicode normalization changes user-visible semantics. -> Move normalization to model-specific validators and test duplicate/confusable key behavior at model boundaries.
- [Risk] Architecture enforcement flags legitimate presentation JSON. -> Scope checks to fingerprint-critical modules and allow explicitly documented presentation/export serializers outside identity domains.

## Migration Plan

- Add strict canonicalization helpers, profile/version constants, and golden-vector tests while leaving existing fingerprints unchanged.
- Audit fingerprint producers and classify each as identity-critical, persisted-envelope, size-estimation, or presentation-only.
- Move identity-critical payload preparation into domain models and replace ad hoc serialization with strict helpers.
- Add canonicalization metadata to persisted envelopes and evidence where reload validation needs it.
- Preserve legacy records using explicit legacy profile identifiers or additive backfill routines.
- Update docs and architecture conformance checks after all identity-critical domains use the canonical owner.

## Open Questions

- Should the implementation use a small vetted JCS package or a local encoder with exhaustive golden vectors?
- Should decimal-like business values be represented as strings in domain payloads, or deferred until the project has an explicit decimal scalar contract?

## Implementation Notes: Fingerprint Producer Inventory (task 1.2)

Resolved open question: the implementation uses a **local JCS-compatible encoder** in `nl2data_core.canonical` with exhaustive golden-vector coverage (no new dependency).

Every `sha256_fingerprint`/`canonical_json` producer found at migration time, classified per the migration plan:

- **Identity-critical (migrated to `strict_sha256_fingerprint`)**: Semantic Query IR and filter fingerprints (`planning/ir`), relationship graph (`planning/models.py`), semantic Bundle payloads and hashes (`bundles`), accepted-assertion manifests and assembly assertions (`assembly/models.py`, `assembly/manifest.py`), Verification Suite plans/evidence/execution keys (`verification`), evaluation case evidence and reports (`evaluation`), assembly audit-evidence entries and publication aggregates (`assembly/audit_evidence.py`), workflow artifact/gate/compatibility/outcome fingerprints (`workflow/runtime.py`, `workflow/durable.py`, `workflow/contract.py`), memory records, governance/policy scope identities, value-semantics and calculated-field expansion identities, SQL/Mongo predicate fingerprints (`adapters/sql/parsing.py`), AI intent/clarification/instruction identities (`ai`), metadata snapshots/configurations/proposals/drift decisions (`metadata`), plugin manifest fingerprints (`plugins/registry.py`), artifact fingerprints (`adapters/fingerprint.py`), and optional package fixture/execution fingerprints (Mongo/Postgres adapters, verification reference executor).
- **Persisted-envelope (profile metadata, strict by default)**: semantic-catalog Postgres envelopes (`packages/nl2data-semantic-catalog-postgres/.../envelope.py`) record `canonicalization_profile` per envelope; records without the member are classified legacy, unknown profiles are rejected with `incompatible_profile`.
- **Size-estimation (legacy `canonical_json` retained)**: AI instruction size estimation (`ai/resolver.py`) — output feeds token budgets, never identity.
- **Presentation-only (out of scope)**: authoring YAML export, debug dumps, admin DTO serialization, and transport JSON — no fingerprints; enforced/allowlisted by the architecture conformance tests (task 5.2).

Number-rendering note: strict JCS renders float-valued integers without a trailing `.0` (e.g. `300.0` → `300`). Golden-vector pins (IR, bundle, verification, audit, envelope) are drift-free; unpinned runtime evidence fingerprints recompute consistently under the strict profile.