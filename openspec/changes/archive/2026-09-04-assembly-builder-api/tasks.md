## 1. Builder module

- [x] 1.1 Create `src/nl2data_core/assembly/authoring/builder.py`: `SemanticAssemblyBuilder` and `AuthoringBuilderError` with the fluent top-level surface (`source`, `entity`, `measure`, `grain`, `policy`, `source_reference`, `compatibility`, `deployment_binding`, `verification_plan`, `build`) constructing existing authoring models directly (D1, D4)
- [x] 1.2 Implement the entity sub-builder (`field`, `relationship`, `calculated_field`, `done`) with construction-time model instantiation and structural misuse checks (entity-scoped calls outside scope, use after `done`, repeated `build`/`done`) raising `AuthoringBuilderError` (D5)
- [x] 1.3 Wrap model validation failures as bounded `AuthoringBuilderError` with authoring path and non-echoing message; no source marks; no value interpolation (D5); export `SemanticAssemblyBuilder` and `AuthoringBuilderError` from `assembly/authoring/__init__.py`

## 2. Verification plan and compatibility inputs

- [x] 2.1 Accept `verification_plan` as an `AuthoringVerificationPlan` instance or a JSON-compatible mapping routed through the same normalized model construction (camelCase aliases, forbidden-key rejection) identical to YAML behavior (D4)
- [x] 2.2 Accept `compatibility` as a `BundleCompatibility` instance or field-wise arguments mirroring the model; ensure no builder parameter exists for fingerprints, lifecycle state, credentials, or physical names (D7)

## 3. Tests

- [x] 3.1 Unit tests: fluent surface coverage for every schema section; construction-time rejection of schema-rejected content (unsafe description, oversized collection, non-scalar policy parameters, forbidden verification keys, identifier violations); misuse cases (orphan sub-builder use, post-`done` use, double `build`)
- [x] 3.2 Reflection parity test: every `AuthoringSpec` and `AuthoringEntity` field is reachable through the builder surface (guards equivalence drift)
- [x] 3.3 Differential equivalence tests: builder document vs equivalent YAML produce identical validation summaries, ordered lowered assertion identities and payload hashes, and byte-identical exports; call-order independence across independent sections
- [x] 3.4 Error-surface tests: bounded messages with authoring path, no value echo, no source marks; policies + verification-plan documents lower through the existing pipeline unchanged

## 4. Documentation

- [x] 4.1 Add a "Builder API" section to `docs/guides/semantic-assembly-authoring.md` (+ zh-CN): fluent example mirroring the YAML example, equivalence guarantee, error surface, and the deliberate divergence from the DDS-020 sketch (no `table=`/`dsn=` parameters; connection references only)

## 5. Quality gates

- [x] 5.1 Run full pytest (non-integration + integration), ruff, and mypy; fix any issues
- [x] 5.2 Run `openspec validate assembly-builder-api --type change --strict` and `scripts/check_docs.py`; confirm `git diff --check` clean
