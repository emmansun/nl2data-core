## Context

The completed semantic assembly lifecycle separates editable `AssemblyDraft` state from immutable published Bundles. `YamlAssemblyLoader` currently safe-loads the internal draft wire shape, which is useful for persistence interchange but unsuitable as a human authoring language: users must compute assertion IDs and may attempt to provide provenance, review bindings, revisions, or approval metadata that belong to trusted lifecycle operations. The Admin service already closes this trust boundary for programmatic draft creation; YAML needs the same discipline.

The repository already depends on PyYAML and has governed models for fields, value semantics, calculated fields, relationships, measures, grains, deployment references, assertion identity, and draft lifecycle. This change composes those models rather than introducing a second semantic domain.

## Goals / Non-Goals

**Goals:**

- Define a concise versioned bundle-as-code YAML schema containing semantic and safe deployment-reference content only.
- Parse untrusted YAML with explicit scalar semantics, structural bounds, duplicate/tag/merge/alias controls, and source-located safe diagnostics.
- Reuse existing semantic models and validators for the normalized model.
- Deterministically lower valid content to revision-zero drafts with trusted author identity and pending derived assertions.
- Add side-effect-free validation and authorized import to the Admin service.
- Provide deterministic semantic-only export and bilingual authoring documentation.

**Non-Goals:**

- No visual editor, language server, JSON Schema extension, or IDE completion in this slice.
- No includes, overlays, environment interpolation, templates, macros, remote references, arbitrary tags, or executable expressions.
- No connection-secret resolution; only existing `env:`, `vault:`, and `file:` references are expressible.
- No authoring shortcut for review, approval, publish, activation, rollback, or audit.
- No replacement of the internal `AssemblyDraft` persistence envelope or its loader.

## Decisions

### D1 — Use a distinct authoring API version and document kind

The root is `apiVersion: nl2data.io/semantic-assembly-authoring/v1alpha1`, `kind: SemanticAssembly`, `metadata` (`bundleId`, `modelVersion`, optional description), and `spec` (`source`, semantic collections, compatibility, deployment bindings). It is deliberately not the internal `nl2data.io/semantic-assembly/v1alpha1` draft envelope.

A distinct version prevents an internal persisted draft from being mistaken for trusted author input and permits the authoring grammar to evolve independently. Alternative: reuse `AssemblyDraft`. Rejected because it exposes lifecycle-owned fields and assertion mechanics.

### D2 — Author nested semantic objects; lower to flat assertions

Authors group fields, relationships, and calculated fields under entities, while measures and grains remain descriptor-level collections. A normalization pass resolves nested context into complete assertion payloads (`descriptor_id`, `entity_id`, and member identity) and then calls `SemanticAssertion.create`; no ID algorithm is duplicated.

The normalized authoring model also constructs the current `SemanticDescriptor`/Bundle component models as a validation oracle. This reuses calculated-field type inference, exact dependency checks, PII isolation, safe descriptions, and bundle reference rules. Alternative: validate raw dictionaries independently. Rejected because parallel rules would drift.

### D3 — Parse through a bounded composing loader, not plain `safe_load`

Implement a private PyYAML `SafeLoader` profile with:

- explicit JSON-compatible scalar resolvers (`true`/`false`, null, integer, finite float; timestamps and YAML 1.1 `yes`/`on` remain strings),
- constructor rejection for custom tags, merge key `<<`, duplicate and non-string mapping keys,
- raw UTF-8 byte and scalar length checks,
- event/node accounting before construction for maximum aliases, nodes, depth, and collection sizes,
- cyclic alias detection and bounded alias expansion.

Anchors and aliases are allowed within bounds because they are useful for repeated labels/settings; they are resolved to ordinary semantic values before validation and never survive lowering. Alternative: reject every anchor. Rejected as unnecessarily hostile once bounded expansion and cycle rejection exist. Plain `yaml.safe_load` alone is rejected because it does not reject duplicate keys and retains YAML 1.1 implicit scalar surprises.

### D4 — Diagnostics are structured, ordered, and value-redacted

`AuthoringDiagnostic` carries code, severity (`error` only in v1alpha1), normalized authoring path, optional one-based line/column, and a bounded message selected from controlled templates. Parser marks are associated with model paths during composition. Messages name member identities only after those identities pass the existing bounded safe identifier rules; offending scalar values, parser exception text, and secret-like content are never echoed. Results cap issue count and report truncation.

Alternative: return Pydantic/PyYAML strings. Rejected because they can expose raw values and are not stable API contracts.

### D5 — Lowering owns lifecycle defaults; Admin owns trusted identity and persistence

Core exposes a pure `lower_authoring(model, *, draft_id, author_reference)` function. It emits sorted assertions with manual provenance, pending state, no bindings, `DRAFT`, revision `0`, and no review/approval metadata. Deployment bindings reuse `DeploymentBinding` and remain outside semantic assertion payloads.

Admin `validate_authoring` accepts bounded text and returns diagnostics plus a safe semantic summary. `import_authoring` checks permission/role and source scope, derives `author_reference` from `AuthContext`, calls the pure lowerer, then invokes the same draft-create path used by programmatic clients. It never accepts author, revision, review, or state parameters from the document.

Alternative: let core read `author_reference` from metadata. Rejected because YAML is untrusted input and cannot establish operator identity.

### D6 — Export is canonical authoring, not lifecycle serialization

Export operates on `SemanticAssemblyAuthoring` or on a draft carrying sufficient authoring-origin metadata while still semantically representable. It emits semantic fields only, sorts identity-keyed collections, uses block-style safe YAML, disables aliases in emitted output, and quotes strings when YAML implicit typing could change their meaning. Parse/export/parse preserves the normalized authoring model and lowering hashes; byte identity is guaranteed only for repeated exports from the same model, not for arbitrary input formatting.

Export from arbitrary reviewed drafts is conservative: either reconstruct only when every accepted assertion type is losslessly representable or return an unsupported-export diagnostic. Review decisions, provenance, operator references, fingerprints, and secrets are never serialized.

### D7 — Keep the existing internal loader explicit

`YamlAssemblyLoader` remains the loader for the internal draft envelope and is renamed only if needed for clarity with a compatibility alias. New APIs use `SemanticAssemblyAuthoringLoader`. Call sites must choose one intentionally; no auto-detection between trusted internal envelopes and untrusted authoring files.

This avoids a confused-deputy parser that guesses trust level from root keys.

## Risks / Trade-offs

- **YAML parser complexity** → Keep the accepted YAML subset small, test adversarial events directly, and centralize all loader configuration in one module.
- **Source-location drift after normalization** → Preserve a path-to-mark table from composition and pin diagnostic paths/locations in tests.
- **Authoring and runtime models diverge** → Construct existing governed models during validation and forbid authoring-only semantic shortcuts that cannot lower losslessly.
- **Alias expansion resource exhaustion** → Enforce byte/event/node/depth/alias bounds before recursive construction and reject cycles.
- **Export suggests round-trip authority** → Name and document it as semantic authoring export; structurally omit every lifecycle field.
- **Large documents create many assertions** → Reuse assembly collection bounds and fail before partial lowering or persistence.

## Migration Plan

1. Add authoring constants, models, diagnostics, and bounded loader without changing the internal draft loader.
2. Add reference validation and deterministic lowering/export with unit and security tests.
3. Add Admin DTOs, capabilities, validate/import methods, and authorization tests.
4. Add complete example YAML, bilingual docs, and deterministic demo validation/import.
5. Run full tests, Ruff, Mypy, documentation checks, and OpenSpec validation.

Rollback removes the new authoring APIs and docs; existing internal drafts, published Bundles, and catalog schemas are unchanged.

## Open Questions

- Whether a future slice should publish a generated JSON Schema and editor integration for the authoring format.
- Whether overlays/includes should ever be introduced as a separately governed preprocessing capability rather than extending the core parser.
