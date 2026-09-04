# Design: assembly-builder-api

## Context

The authoring pipeline is `YAML bytes → SemanticAssemblyAuthoringLoader (bounded parse, source marks) → SemanticAssemblyAuthoring (pydantic, camelCase aliases, fail-closed model validators) → validate_authoring → lower_authoring(draft_id, author_reference) → AssemblyDraft`, with `export_authoring` as the round-trip exit. `models.py` is the single validation boundary: bounded sequences, identifier patterns, safe descriptions, policy-parameter normalization (`normalize_policy_parameters`), verification-payload normalization with forbidden-key rejection, and descriptor-global uniqueness checks on the top-level document model.

DDS-020 §9.2 (A2) sketches a fluent `BundleBuilder` and states the design point: the Builder must be isomorphic with YAML so both entry points share all validation logic — "no fork where YAML passes and the API fails". The DDS sketch shows physical names (`table=`, `column=`, `dsn=`), which the repo's authoring schema deliberately rejects (DDS-011/ADR-011 boundary: authoring carries references, not physical credentials or bindings).

This is the second of three M2 changes (after the archived `assembly-policy-templates`, before deterministic bundle export §9.6).

## Goals / Non-Goals

**Goals:**
- A fluent, typed, in-process construction path for authoring documents, usable by hosts, scaffolding, and future LLM tooling.
- Zero validation fork: builder output is byte-for-byte the same model object graph the YAML loader produces.
- Deterministic equivalence with YAML documents across validation, lowering, and export.
- Fail-closed at construction with bounded, non-echoing errors.

**Non-Goals:**
- Re-routing the YAML parser through builder calls (D2 explains why this literal reading is rejected).
- Scaffold/discovery generation (§9.3), round-trip export of published bundles (§9.6), runtime policy enforcement (DDS-011 alignment deferred).
- Any new bounds, identity rules, or template semantics — the models remain the single source of truth.

## Decisions

### D1 — Builder is a pure constructor over the existing model; pydantic validators are the validation boundary
`SemanticAssemblyBuilder` builds and holds `AuthoringMetadata`/`AuthoringSource`/`AuthoringEntity`/… model instances. Every `field()`/`relationship()`/`policy()`/… call constructs the corresponding pydantic model immediately, so **all** existing model-level validators (bounds, safe descriptions, scalar profiles, forbidden keys, uniqueness on the top-level document) run at call time — the same code the YAML path runs. The builder adds **no validation logic of its own** beyond structural misuse checks (D5). Consequence: a document that constructs successfully has, by construction, the same semantics as the equivalent YAML document.

### D2 — Literal "YAML parser internally calls Builder" is rejected; equivalence is guaranteed by the shared model + shared pipeline + differential tests
Re-routing the bounded YAML parser through the fluent API would destroy source marks (the loader attaches line/column marks per node), complicate bound enforcement, and buy nothing — the fork DDS-020 warns about cannot occur because both paths converge on the same model and the same `validate_authoring`/`lower_authoring`/`export_authoring` pipeline. The guarantee is enforced by a differential test suite: builder-constructed documents and equivalent YAML documents must produce identical validation summaries, identical ordered lowered assertion identities and payload hashes, and identical export bytes. The design doc of record for DDS-020 traceability notes this substitution explicitly.

### D3 — Name: `SemanticAssemblyBuilder`, not `BundleBuilder`
The repo reserves "bundle" for published semantic model bundles (`semantic-model-bundles`, catalog/bundle lifecycle). Authoring produces a *document* that lowers into an *assembly draft*. Naming the builder `SemanticAssemblyBuilder` (module `assembly/authoring/builder.py`) avoids re-introducing the bundle/authoring noun collision the same way the policy-template naming disambiguation did (D7 of the previous change). DDS-020's `BundleBuilder` name is acknowledged as the design-source sketch name.

### D4 — Fluent surface mirrors the schema sections exactly
Top level: `SemanticAssemblyBuilder(bundle_id, model_version, description="")` constructs metadata; `.source(source_id, catalog_fingerprint=None)`; `.entity(entity_id, label, description="")` enters an entity sub-builder; `.measure(...)`, `.grain(...)`, `.policy(template, **parameters)`, `.source_reference(...)`, `.compatibility(...)`, `.deployment_binding(...)`, `.verification_plan(...)`; `.build() -> SemanticAssemblyAuthoring` (top-level uniqueness/aggregate checks run here, since they live on the document model).

Entity scope: the sub-builder returned by `.entity()` offers `.field(field_id, label, data_type, ...)`, `.relationship(relationship_id, target_entity_id, source_fields, target_fields, label)`, `.calculated_field(name, label, expression, output_type, requires, ...)`, and `.done()` returning the top-level builder. Cross-references (relationship targets, verification query entity/field references) are validated by the existing document-model validators at `.build()` — the builder does not pre-resolve them, keeping one validation owner.

`expression` for calculated fields accepts an `ExprNode` (the governed DSL AST) — the builder does not accept expression strings, so no parser duplication; string parsing stays with the existing calculated-field DSL entry point. `verification_plan` accepts an `AuthoringVerificationPlan` instance or a JSON-compatible mapping, which is routed through the same `_normalize_verification_payload`-equipped model construction (camelCase aliases accepted, forbidden keys rejected) — identical to YAML behavior.

### D5 — Error surface: one typed error, bounded message, no value echo, no source marks
All construction failures raise `AuthoringBuilderError` (module-level, exported), wrapping the underlying pydantic failure as a bounded message plus an authoring path (e.g. `$.spec.entities[2].fields[0]`) where one exists. Programmatic input has no line/column, so diagnostics carry paths but never source marks — matching the existing `AuthoringPath` vocabulary without `AuthoringSourceMark`. Messages never echo rejected values (inherited from the models' own message discipline; the builder must not interpolate argument values into errors). Structural misuse — `field()` before `.entity()`, `.field()` after `.done()`, `.build()` twice, `.done()` at top level — also raises `AuthoringBuilderError` with a misuse message. A single error class keeps the surface small; the message text distinguishes content rejections from misuse.

### D6 — Determinism: insertion order in, identity order out
The builder appends in call order; the model tuples preserve it. This is fine: lowering and export already order assertions and exported collections by identity, and top-level uniqueness runs at `.build()`. The differential equivalence tests pin that call order across independent sections cannot change any downstream artifact. The builder performs no sorting of its own.

### D7 — No new bounds, no argument-side escape hatches
Bounds live exclusively in the models. Builder parameters are snake_case kwargs mirroring model fields; there are no convenience parameters for physical names, DSNs, credentials, fingerprints, lifecycle state, or approval bindings — the DDS §9.2 sketch's `table=`/`dsn=` style parameters are intentionally unsupported because the authoring schema rejects that content class (deployment bindings carry `connection_reference`, a reference scheme, not a DSN). The guide will document this divergence from the DDS sketch explicitly.

## Risks / Trade-offs

- **[Risk] Fluent misuse patterns** (orphan entity sub-builder, double `build()`) → mitigated by explicit state checks in the builder raising `AuthoringBuilderError`; misuse tests cover each.
- **[Risk] Equivalence drift** if models gain new sections without builder coverage → mitigated by a parity test asserting every `AuthoringSpec`/`AuthoringEntity` field is reachable through the builder surface (reflection-based), so a new schema section fails the suite until the builder supports it.
- **[Trade-off] Rejected the literal parser-calls-builder reading of DDS-020** → accepted because source marks and bounded parsing are load-bearing; the shared-model convergence plus differential tests delivers the intended no-fork guarantee with less risk.
- **[Trade-off] Single error class** instead of a hierarchy → accepted for a small surface; `PolicyTemplateError` remains for direct registry use, and builder callers see it wrapped in `AuthoringBuilderError` via the model validators.

## Migration Plan

Purely additive: new module, new exports, new tests, new guide section. No existing API, model, loader, or pipeline behavior changes; no deprecations. Documents without builder usage are unaffected (the builder is never imported by existing code paths).

## Open Questions

None — scope and surface are fixed by DDS-020 §9.2 and the existing schema.
