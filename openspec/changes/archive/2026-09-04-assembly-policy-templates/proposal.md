## Why

The semantic assembly authoring format cannot express governance policy intent. The draft and manifest layers already define `AssertionType.POLICY` with a stable identity (`descriptor_id` + `policy_id`), but no authoring surface can produce one, so governance policies are unreachable from Path A (hand authoring). DDS-020 §9.4 (ADR-038) identifies parameterized policy templates as the ease-of-use lever for this exact gap: recurring governance patterns (tenant isolation, row restriction, purpose gating, field masking) are the same few shapes with different parameters, and hand-writing raw policy assertions for each is error-prone friction.

## What Changes

- Add a bounded `policies` section to the authoring YAML schema that declares governance policy intent as **template references with typed parameters** (for example `template: tenant-isolation` with `entity`, `field`, `claim` parameters).
- Introduce a **closed template registry** with four templates — `tenant-isolation`, `row-restriction`, `purpose-gating`, `field-masking` — each with a fixed, bounded parameter schema. Unknown template names fail closed.
- Expand template declarations into ordinary policy `SemanticAssertion` records **during lowering, before review**, using the existing type-specific assertion identity rules. Per ADR-038: the `template` reference and parameters are authoring syntax sugar only — after expansion the template form **never enters the canonical payload or the fingerprint domain**; the draft carries standard pending policy assertions that traverse the existing review, approval, verification, and publish gates unchanged.
- Validate template references fail-closed with source-located diagnostics: unknown template, missing/extra/unknown parameters, parameter values referencing undeclared entities or fields, and duplicate expanded policy identity within the document.
- Extend deterministic authoring export to round-trip template declarations with presentation-invariant ordering, so the authoring model remains losslessly exportable.
- No change to lifecycle states, review/approval/publish semantics, Admin API endpoints, fingerprint computation, or verification policies (the existing "policy profile" concept on `VerificationPlan` is verification policy and stays distinct from governance policy assertions).

## Capabilities

### New Capabilities
- `assembly-policy-templates`: the closed template registry, parameter schemas, fail-closed expansion into policy assertions before review, and the invariant that template identity never reaches the canonical payload or fingerprint domain.

### Modified Capabilities
- `semantic-assembly-yaml-authoring`: the authoring schema accepts a new bounded `policies` section; cross-reference validation and deterministic lowering/export requirements extend to template declarations.

## Impact

- **Code**: `src/nl2data_core/assembly/authoring/` (models, validation, lowering, export, diagnostics); new template registry module under `src/nl2data_core/assembly/authoring/` (or `src/nl2data_core/assembly/policies/`). Draft/manifest assertion models are consumed, not modified.
- **Tests**: new unit tests (models, registry, validation), contract tests (lowering identity, export round-trip, fingerprint-domain exclusion), and authoring lint regression (expanded policy assertions lint like any assertion).
- **Docs**: `docs/guides/semantic-assembly-authoring.md` (+ zh-CN) gains a policies/templates section; `docs/reference/semantic-assembly-lint.md` unchanged.
- **No impact**: public facade, adapters, verification suite, bundle fingerprint domain, Admin API contract (existing `lint_authoring`/`lint_draft` operate unchanged on documents containing the new section).
