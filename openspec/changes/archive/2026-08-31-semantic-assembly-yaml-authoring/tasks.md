## 1. Authoring Contract Models

- [x] 1.1 Add the authoring API version/kind constants and frozen bounded models for document metadata, source configuration, entities, fields, relationships, calculated fields, measures, grains, source references, compatibility, and deployment bindings under `src/nl2data_core/assembly/authoring/`.
- [x] 1.2 Define `AuthoringDiagnostic`, source mark/path, safe summary, parse result, validation result, lowering result, and export result models with stable bounded codes and issue truncation metadata.
- [x] 1.3 Add model validators for unknown members, unique descriptor-global identities, source consistency, collection bounds, and lifecycle-owned field exclusion.
- [x] 1.4 Add model tests for minimal/full documents, unsupported version/kind, unknown members, duplicate identities, bounds, and rejection of assertion IDs, provenance, review, revision, approval, audit, fingerprint, and activation fields.

## 2. Bounded YAML Parser

- [x] 2.1 Implement a dedicated `SemanticAssemblyAuthoringLoader` using a private PyYAML safe-loader profile with explicit JSON-compatible scalar resolution; timestamps and YAML 1.1 values such as `yes`/`on` remain strings.
- [x] 2.2 Add pre-construction UTF-8 byte, event/node, nesting-depth, scalar-length, collection-length, and alias-count/expansion bounds; reject cyclic aliases before recursive construction.
- [x] 2.3 Reject custom/object tags, merge keys, duplicate and non-string mapping keys, unsupported scalar tags, and non-finite numbers without executing constructors or external I/O.
- [x] 2.4 Preserve a normalized authoring-path-to-one-based-line/column map and translate parser/model failures into ordered controlled diagnostics that never echo unsafe scalar values or raw parser exceptions.
- [x] 2.5 Add parser/security tests for valid JSON/YAML, comments and bounded aliases, duplicate keys, merge keys, custom tags, cyclic and exponential alias inputs, malformed Unicode/YAML, ambiguous scalars, non-finite numbers, every structural bound, diagnostic truncation, deterministic locations, and secret redaction.

## 3. Semantic Validation and Lowering

- [x] 3.1 Normalize nested authoring members into complete assertion payloads and construct the existing descriptor, field/value-semantics, relationship, calculated-field, measure, grain, source-reference, compatibility, and deployment-binding models as the semantic validation oracle.
- [x] 3.2 Validate relationship endpoints/join fields, measure and grain references, source bindings, descriptor-global namespaces, calculated-field inference/dependencies/non-composition/pii isolation, and safe-content rules before lowering.
- [x] 3.3 Implement pure deterministic lowering with trusted `draft_id` and `author_reference`, calling existing assertion identity helpers and producing sorted manual/pending assertions in a revision-zero `DRAFT` with no lifecycle authority metadata.
- [x] 3.4 Ensure any parse, schema, semantic, reference, or bounds failure returns no partial authoring model or draft and performs no persistence.
- [x] 3.5 Add lowering tests covering every assertion type, calculated fields, value semantics, deployment references, mapping-order/comment/anchor equivalence, trusted identity injection, stable payload hashes, pending-only state, and unknown/ambiguous reference rejection.

## 4. Deterministic Authoring Export

- [x] 4.1 Implement deterministic block-style YAML export from the authoring model with identity-keyed collection ordering, aliases disabled, explicit safe scalar quoting, and repeatable bytes.
- [x] 4.2 Add conservative semantic export from representable authoring-derived drafts; reject lossy or unsupported assertion shapes and omit assertion IDs, provenance, review/revision/approval/audit/fingerprint data, and resolved credentials unconditionally.
- [x] 4.3 Add export round-trip tests proving parse/lower payload-hash equivalence, deterministic repeated output, ambiguous-string preservation, lifecycle metadata absence, and safe failure for non-representable drafts.

## 5. Admin Service Integration

- [x] 5.1 Add bounded Admin DTOs and schema registrations for authoring validation/import commands, diagnostics, semantic summaries, supported versions, and maximum input size.
- [x] 5.2 Add `validate_authoring` as a side-effect-free operation requiring trusted authentication and source scope, returning only bounded safe diagnostics/summary.
- [x] 5.3 Add `import_authoring` requiring Assembly Author permission and lifecycle role; derive the author reference from `AuthContext`, lower in core, and persist through the existing tenant-scoped `create_draft` boundary.
- [x] 5.4 Extend Admin capabilities and package protocols/docs to advertise validation/import prerequisites without exposing authoring-to-review/approve/publish operations.
- [x] 5.5 Add Admin contract/security tests for valid validation/import, no validation persistence, missing permission/role, cross-source and cross-tenant denial, duplicate draft conflict, diagnostics redaction, trusted author derivation, and inability to smuggle lifecycle state.

## 6. Documentation and Demo

- [x] 6.1 Add a complete versioned example under `demo/` covering entities, fields, relationships, value semantics, a calculated field, measure/grain, source reference, compatibility, and safe deployment reference.
- [x] 6.2 Extend the deterministic demo to validate and import the example before normal review/approval/publish while keeping external services and secret resolution optional.
- [x] 6.3 Add English and Chinese authoring guides documenting the root schema, supported scalar/YAML subset, bounds, diagnostics, reference rules, calculated fields, deployment references, lifecycle boundary, export guarantees, and rejected features.
- [x] 6.4 Update capability, architecture, troubleshooting, and Admin service references; register any new documentation pages in the docs index and link checker.

## 7. Quality Gates

- [x] 7.1 Run focused unit, security, Admin contract, demo, and lifecycle integration tests for all touched authoring paths.
- [x] 7.2 Run full pytest, Ruff, Mypy, `scripts/check_docs.py`, and `openspec validate semantic-assembly-yaml-authoring --type change`.
- [x] 7.3 Verify existing internal `YamlAssemblyLoader`, persisted drafts, published Bundle fingerprints, mainflow demos, and callers without authoring documents remain behaviorally and fingerprint compatible.
