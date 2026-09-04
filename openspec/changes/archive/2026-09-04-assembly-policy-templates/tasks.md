## 1. Template registry and models

- [x] 1.1 Create `src/nl2data_core/assembly/authoring/policy_templates.py`: closed registry constants for `tenant-isolation`, `row-restriction`, `purpose-gating`, `field-masking` with typed parameter schemas, identifying-target definitions, and bounds (declaration count, list lengths, scalar lengths)
- [x] 1.2 Add `AuthoringPolicyTemplate` model (template identifier + bounded parameter mapping) and bounded `policies` tuple on `AuthoringSpec` with camelCase YAML alias and pydantic bounds; reject raw policy payloads, fingerprints, lifecycle fields, and non-scalar values at model level
- [x] 1.3 Implement pure `expand_policy_templates(model) -> tuple[ExpandedPolicy, ...]` with deterministic target-derived identity (D3: dotted target form, blake2b-16 digest fallback) and fully-resolved typed payloads

## 2. Validation (fail-closed, source-located)

- [x] 2.1 Extend the authoring validator to run expansion checks with source locations: unknown template, unknown/missing parameter, wrong value kind, bounds violations, unresolved entity/field/entity.field targets, duplicate expanded identity
- [x] 2.2 Ensure diagnostics follow the existing authoring diagnostic shape (path + source mark, no value echoing for rejected content) and are covered for each failure class

## 3. Lowering and export

- [x] 3.1 Extend `lower_authoring` to emit expanded `AssertionType.POLICY` assertions (manual provenance, pending review state, no review binding) ordered by expanded identity
- [x] 3.2 Extend `export_authoring` to emit the `policies` section sorted by expanded identity; verify export → parse → lower preserves policy assertion identities and payload hashes
- [x] 3.3 Confirm documents without a `policies` section take a no-op path through validation, lowering, and export (byte-identical to previous behavior)

## 4. Tests

- [x] 4.1 Unit tests: registry schemas and bounds; identity derivation (target-derived, digest fallback within identifier pattern, value-parameter change preserves identity)
- [x] 4.2 Authoring tests: each validation failure class with source-located diagnostics; equivalent-YAML determinism (key order, comments, whitespace, bounded anchors) for policies
- [x] 4.3 Contract tests: expanded assertions are pending/manual-provenance with no review binding or fingerprint; canonical payload contains no `template` key or raw parameter mapping; draft carries no template construct
- [x] 4.4 Export round-trip tests: policies preserved presentation-invariantly; no-policies documents unchanged; lint regression (`lint_authoring`/`lint_draft`) on documents containing policy assertions

## 5. Documentation

- [x] 5.1 Add a "Policy templates" section to `docs/guides/semantic-assembly-authoring.md` (+ zh-CN): the four templates, parameter tables, expansion-before-review semantics, and the governance-policy vs verification-policy-profile naming distinction (D7)
- [x] 5.2 Note in the guide that expanded policies describe governance intent reviewed as assertions, not a new runtime decision engine (risk acknowledgment from design)

## 6. Quality gates

- [x] 6.1 Run full pytest (non-integration + integration), ruff, and mypy; fix any issues
- [x] 6.2 Run `openspec validate assembly-policy-templates --type change --strict` and `scripts/check_docs.py`; confirm `git diff --check` clean
