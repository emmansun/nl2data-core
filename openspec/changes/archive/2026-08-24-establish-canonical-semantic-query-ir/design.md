## Context

DDS-019 identifies the canonical Semantic Query IR as the boundary between probabilistic intent interpretation and deterministic backend compilation. The repository already has immutable `SemanticQueryPlan`, structural validation, stable fingerprints, SQL/MongoDB compilers, and governed workflow checkpoints. However, the current plan includes an optional `PhysicalBinding`, and compilers consume the plan directly, so physical concerns can leak into the planning contract as additional adapters are introduced.

This change is a contract evolution, not an architecture reset. Existing plan and adapter callers must continue to work while the new IR becomes the canonical logical representation.

## Goals / Non-Goals

**Goals:**

- Define a versioned immutable `SemanticQueryIR` with backend-neutral logical semantics.
- Preserve safe scalar constraints, bounded selections/filters/orderings, provenance, result shape, and stable fingerprints.
- Separate logical IR from physical bindings and compiled artifacts.
- Provide explicit translation and compatibility behavior for existing `SemanticQueryPlan` users.
- Link compiler output and workflow checkpoint compatibility to the IR version/fingerprint.
- Establish tests and fixtures that future SQL, MongoDB, and other compilers can share.

**Non-Goals:**

- A complete Semantic Model DSL, model bundle publisher, or Semantic View resolver.
- LLM/provider changes, context retrieval, approved examples, or autonomous repair loops.
- Rewriting SQL/MongoDB compilers or adding new adapters.
- Public exposure of internal IR models through `nl2data`.
- Distributed workflow state, leases, fencing, or execution exactly-once guarantees.

## Decisions

### Add a distinct logical IR instead of renaming in place

Introduce `SemanticQueryIR` under the planning boundary and keep `SemanticQueryPlan` as a compatibility model during migration. A direct rename would create avoidable breakage and would not remove the existing physical binding leak. The compatibility bridge will translate legacy plans into IR and, where necessary, translate IR into the legacy compiler input until compiler migration is complete.

### Keep v1 IR deliberately small

The first IR version covers selections, filters, grouping, ordering, bounded limits, time/result-shape metadata, view/source references, provenance, and capability requirements. It uses typed discriminated expressions where needed but does not invent the full DDS-019 semantic DSL. Unsupported or extension nodes are explicit and fail closed unless a declared capability accepts them.

### Make physical compilation a separate boundary

Define compiler-facing input/context and artifact evidence so physical bindings are supplied by the resolved semantic model/compiler context, not embedded in the IR. SQL and MongoDB compilers may initially use compatibility adapters, but their canonical entry points must validate the IR fingerprint and emit an artifact fingerprint linked to it.

### Canonical serialization and fingerprinting

Use the repository canonical JSON utility and SHA-256 representation. Serialization includes an explicit IR version and only safe logical fields. Fingerprints cover the canonical IR payload and provenance inputs, while physical artifact fingerprints remain compiler-specific. Raw SQL/MQL, credentials, executable code, native objects, chart configuration, and hidden policy material are structurally rejected.

### Compatibility and rollout

Intent and existing static plan resolvers continue to produce `SemanticQueryPlan` initially; the runtime normalizes them to IR at the planning boundary. New code targets IR. Existing compiler callables remain supported through a legacy adapter that receives a validated legacy plan. After SQL and MongoDB compiler alignment, legacy direct-plan paths can be deprecated without changing the adapter protocol.

### Legacy-plan compatibility window (as implemented)

During the window, the following behaviors are guaranteed and covered by contract/security/integration tests:

- **Plan-to-IR normalization is lossless for the logical core.** `plan_to_ir` preserves selection/filter/ordering identifiers, filter fingerprints (`ir.filter_fingerprints() == plan.filter_fingerprints()`), the bounded limit, source/entity lineage, and derives result shape, groupings, required capabilities, and the deterministic `ir-<plan.fingerprint[-16:]>` id. The physical binding never enters the canonical IR payload.
- **IR-to-plan translation is compiler compatibility only and lossy.** `ir_to_plan` drops time context, extensions, capabilities, and explicit groupings (legacy compilers re-derive them) and requires the caller to supply the physical binding. Compiling `ir_to_plan(ir)` produces the same SQL/MQL as the original plan.
- **Both compiler entry points accept validated IR.** `compile_ir` and `compile_mongo_ir` verify the IR fingerprint and fail closed (no artifact) on tampered IR; the legacy `compile_plan`/`compile_mongo_plan` paths remain byte-compatible (e.g. the Mongo spec id stays `mongo-<plan.fingerprint[-16:]>` for plans, `mongo-<ir.fingerprint[-16:]>` for IR).
- **The planning boundary rejects IR-invalid plans.** The plan builder, static plan resolver, and evaluation fixtures normalize at the boundary, so duplicate filters, unbounded limits, and unsupported extensions are rejected before any adapter is reached.
- **Legacy checkpoints resume untouched.** Checkpoints without IR identity metadata have no `ir` key in compatibility evidence and resume exactly as before; checkpoints with a stale or partial IR identity are rejected before adapter execution.
- **Direct new usage of `SemanticQueryPlan` should be avoided.** New code targets `SemanticQueryIR`; the translator remains for the supported window and is removed after downstream callers migrate.

## Risks / Trade-offs

- [Two plan models during migration] → Keep one normalization function and test fingerprint/evidence equivalence; mark direct legacy compiler paths as compatibility-only.
- [IR grows SQL-shaped] → Forbid physical bindings and target-language syntax in the model; require backend-neutral golden fixtures and capability tests.
- [Semantic information is underspecified] → Keep unsupported operations explicit and fail closed; add fields only through a versioned change.
- [Checkpoint incompatibility] → Include IR version/fingerprint in compatibility evidence and reject stale checkpoints rather than silently resuming them.
- [Fingerprint changes break replay] → Document canonicalization rules and use golden serialized fixtures before changing field order or defaults.

## Migration Plan

1. Add IR models, validation, serialization, and golden fixtures without changing existing execution paths.
2. Add legacy-plan-to-IR normalization and IR-to-legacy compiler compatibility adapters.
3. Update AI planning and workflow evidence to normalize at the boundary and record IR identity.
4. Align SQL and MongoDB compiler entry points and tests with IR provenance while retaining legacy callables.
5. Deprecate direct new usage of `SemanticQueryPlan` after downstream callers migrate; retain the compatibility alias/translator for the supported compatibility window.
6. Roll back by disabling IR normalization and using the existing plan/compiler path; no persisted raw data migration is required.

## Open Questions

- Which time-context and result-shape variants belong in IR v1 versus the next version.
- Whether compiler registries should be introduced in this change or in the later compiler-framework change.
- Whether the legacy plan fingerprint should remain equal to the IR fingerprint or be recorded as a separate migration reference.
