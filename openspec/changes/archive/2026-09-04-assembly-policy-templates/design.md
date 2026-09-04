## Context

The authoring pipeline is `YAML → loader (bounded parse) → models → validation → lowering → revision-zero draft`. Lowering already emits `SemanticAssertion` records for entity, field, mapping, relationship, calculated-field, measure, and grain members (`lowering.py`), and `AssertionType.POLICY` exists in `assembly/models.py` with the type-specific identity `descriptor_id + policy_id` — but no authoring surface can produce a policy assertion. DDS-020 §9.4 (ADR-038) specifies policy templates as authoring syntax sugar that expands into plain policy objects before review.

Existing conventions this design must respect: camelCase YAML keys with snake_case model fields (`deploymentBindings`, `verificationPlan`); bounded, non-executable YAML parsing; fail-closed source-located cross-reference validation before draft creation; deterministic lowering independent of presentation; export contains authoring content only; jcs-v1 fingerprinting over canonical payloads; the existing `policy_profile`/`policy_version` on `VerificationPlan` is **verification policy** and must stay conceptually distinct from governance **policy assertions**.

## Goals / Non-Goals

**Goals:**

- A bounded `policies` authoring section expressing recurring governance patterns as template references with typed parameters.
- A closed, code-owned template registry (four templates) with fixed parameter schemas.
- Deterministic expansion into standard pending policy assertions during lowering — identical drafts for equivalent YAML.
- Fail-closed, source-located diagnostics for every invalid template usage, before any draft is created.
- Template identity confined to the authoring layer; canonical payloads and the fingerprint domain see only expanded policy semantics.

**Non-Goals:**

- No raw (template-free) policy assertion form in authoring YAML this slice; complex custom policies remain a draft-layer capability for a future change (DDS-020 ADR-038 keeps them on a "raw construction path" that Path A does not yet have).
- No runtime policy enforcement changes: host `PolicyScope` and the governance evaluator are untouched; policy assertions are review/audit/bundle-content units, not a new decision engine.
- No new lint rules (SAL codes) — template validation belongs to the authoring validator, consistent with how cross-reference failures are handled.
- No Admin API, lifecycle, verification-policy, or fingerprint-domain changes.
- No host-extensible or config-defined templates — the registry is closed code (fail-closed extensibility posture).

## Decisions

### D1 — Schema shape: single canonical template form

New top-level `policies` sequence; each entry is a mapping with exactly `template` (identifier) and `parameters` (mapping of string keys to JSON-compatible scalars or bounded scalar lists). No inline shorthand, no nested policy bodies. Rationale: one canonical form keeps the bounded parser, the validator, and the exporter trivially aligned; alternative free-form policy bodies would reintroduce the "policy mini-language" ADR-038 explicitly rejects.

### D2 — Closed registry with typed parameter schemas

Four templates, code-owned constants in a new `assembly/authoring/policy_templates.py`:

| Template | Parameters (all required) | Identifying target |
| --- | --- | --- |
| `tenant-isolation` | `entity`, `field`, `claim` (identifiers) | entity + field |
| `row-restriction` | `entity`, `field`, `allowed_values` (bounded scalar list, 1–256) | entity + field |
| `purpose-gating` | `purposes` (identifier list, 1–16), `effect` (`allow` \| `deny`) | purposes (sorted) |
| `field-masking` | `fields` (entity.field reference list, 1–64), `replacement` (bounded string, must not be empty; must not echo any field value) | fields (sorted) |

Unknown parameter keys, missing required parameters, wrong value kinds, and out-of-bounds lists are validation failures. Rationale: typed schemas let diagnostics be precise (which parameter, which source line) instead of deferring to expansion-time errors; a closed registry matches the repo's fail-closed whitelist posture (same pattern as the calculated-field operator whitelist).

### D3 — Expanded identity: target-derived `policy_id`, value parameters excluded

Expanded `policy_id` = `<template>.<identifying-target>` rendered as a bounded dotted identifier (e.g. `tenant-isolation.customer.tenant_id`, `purpose-gating.billing.audit` with sorted purposes). Value parameters (`claim`, `allowed_values`, `effect`, `replacement`) are **excluded from identity** — changing `allowed_values` modifies the assertion payload, not its identity, mirroring DDS-020 ADR-045's identity-vs-payload split (diff reports modified, not delete+add). The rendered form is used only when it is **injective**: identifier components may themselves contain dots, so dot-bearing components (always the case for `field-masking` entity.field entries) are joined ambiguously; ambiguous or over-length renders fall back to `<template>.` + blake2b-16 digest over a non-identifier separator, keeping the identity deterministic, injective, and within the 128-char `Identifier` bound. Duplicate expanded identity within one document is a validation failure (two declarations of the same target conflict rather than silently shadowing). Rationale: identity must be stable, human-greppable in the common case, and must never let two distinct targets share one assertion identity.

### D4 — Single shared expansion function, two consumers

Expansion lives in one pure function `expand_policy_templates(model) -> tuple[ExpandedPolicy, ...]` (returning typed parameter objects, identity, and payload). The validator calls it to run all fail-closed checks with source locations; `lower_authoring` calls it to emit `SemanticAssertion.create(type=POLICY, payload={descriptor_id, policy_id, policy_kind, ...typed params}, provenance=manual)`. Rationale: two implementations would fork validation and lowering — the exact "YAML passes, API fails" class of defect; a single pure function is the cheapest single source of truth. Expansion output is presentation-invariant (sorted by identity), so lowering stays deterministic.

### D5 — Template declarations persist in the authoring model and export

`AuthoringSpec` gains a bounded `policies` tuple (max 64). Export round-trips template declarations sorted by expanded identity (presentation-invariant), so `export → parse → lower` preserves assertion identities and payload hashes like every other authoring member. The **draft** carries no template construct — only expanded assertions — so manifest, review, audit, and bundle paths are untouched by construction. Rationale: ADR-038 requires the template form to vanish before review; keeping it only in the authoring model satisfies round-trip fidelity without touching the fingerprint domain.

### D6 — Policy kind is expanded semantics, not template syntax

The expanded payload records `policy_kind` (the template name) plus typed parameters as ordinary policy semantics. This is content, not a `template:` reference: the canonical payload contains a fully-resolved policy object, satisfying T-05 (no `template` field in canonical payload) by construction. A contract test pins that no assertion payload key is named `template` and no payload value contains the raw parameter mapping.

### D7 — Naming disambiguation

Docs and code comments use **"policy template"** (authoring sugar), **"policy assertion"** (expanded draft/bundle content), and **"verification policy profile"** (the existing `VerificationPlan.policy_profile`). No identifier reuse: the registry never touches `VerificationPolicy`/`BUILTIN_POLICIES`.

### D8 — Bounds follow the existing authoring limit table

Module-level `_MAX_POLICY_DECLARATIONS = 64`, `_MAX_POLICY_PARAMS = 8`, scalar/string bounds reuse the existing `_MAX_DESCRIPTION_CHARS`-class constants. All limits are validated before model construction where the parser can see them, and by pydantic `Field` constraints in the models.

## Risks / Trade-offs

- [Policy assertions can now reach review/publish while runtime governance enforcement still comes from the host `PolicyScope`] → Accepted for this slice: assertions are governed content units (review, diff, audit); the design documents this explicitly and the docs state that expanded policies describe intent, not a new decision engine. A future change binds policy assertions to runtime enforcement (DDS-011 alignment).
- [`purpose-gating`/`field-masking` identities derive from lists, so param-list edits can look like identity changes] → Mitigated: identity uses the *sorted set* of target references, and diagnostics name the expanded identity, so diff behavior is predictable; the contract tests pin modify-vs-recreate behavior.
- [Identifier-length digest fallback (D3) makes long or dotted identities opaque] → Mitigated: dot-free targets (the common case) stay human-readable; the digest is deterministic and pinned by golden tests, and `field-masking` identities are digest-form by design because entity.field entries are inherently dotted.
- [Template registry is code, so adding a template requires a core release] → Accepted: consistent with the operator-whitelist ADR posture; config-defined templates would need their own governance story (trust, versioning, fingerprints) and are explicitly out of scope.

## Migration Plan

Purely additive: documents without a `policies` section parse, validate, lower, and export exactly as before (no-op path covered by the existing suite). No data migration, no envelope or fingerprint changes, no Admin contract change. Rollback is a revert; the only persisted artifacts are drafts whose policy assertions are valid `AssertionType.POLICY` records readable by today's lifecycle code.

## Open Questions

None blocking. One deferred decision recorded for the future runtime-binding change: whether expanded policy assertions should surface in the `SemanticDescriptor` (bundle vocabulary) or remain bundle-provenance content until the DDS-011 alignment lands.
