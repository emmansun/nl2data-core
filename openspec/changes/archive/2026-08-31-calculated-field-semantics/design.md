# calculated-field-semantics Design

## Context

v4.1 shipped value-level semantics with decisions D1–D10 and left `CalculatedField` (v4.2), `Metric`/TimeSemantics (v4.3), and NamedQuery (v4.4) explicitly out of scope. The source design doc (§3, module 2) sketches the calculated-field DSL; this design grounds that sketch in the v4.1-established invariants: N4 restated ("no probabilistic construction; deterministic governed lookup permitted"), N6 (unset optional members are omitted from canonical payloads), fingerprints-only `CompilationEvidence`, JSON-wire safety (no `frozenset`, no floats in fingerprint-critical domains), fail-closed extension gating in the IR, and the snapshot-breaking upgrade chain for descriptor content changes.

Key grounding facts from the current code:

- `IRSelection` references fields generically (`field_id` + `aggregation`), so an IR can reference a calculated field by name with **no IR schema change** — the reference path already exists.
- `SemanticQueryIR.extensions` (`IRExtension`) is a fail-closed extension mechanism: an extension is accepted only when its `kind` is declared in `required_capabilities`. This is the natural, non-breaking home for the NamedQuery placeholder reservation.
- `verify_pre_execution_guard` already rejects unsupported required capabilities, so reserving a capability name today yields correct fail-closed behavior with zero new guard code.
- `CompilationEvidence.canonical_payload()` currently includes every key unconditionally — the same trap v4.1's N6 closed for descriptors; any new evidence member must be omit-when-unset.
- The demo corpus stores business-word values directly; no bundle declares `ValueSemantics` yet. The same will be true initially for calculated fields, so the v4.2 gate measures an annotated corpus, not a live adoption rate.

## Goals / Non-Goals

**Goals:**

- One governed definition per calculated field: fingerprinted, authorized through the existing view/bundle chain, deterministically compiled.
- Closed operator whitelist with every rejection (and its alternative path) recorded in an ADR.
- Dual-adapter semantic equivalence (SQL and MongoDB) as a conformance guarantee, including `zero_division_policy` parity.
- IR-level parameterized placeholder schema reserved for NamedQuery (v4.4) with zero behavior and zero fingerprint impact in v4.2.
- Bounded `CF_*` attribution dimensions so the roadmap gate (`CF_MISSING < 10%`, DSL expression failure rate `< 5%`) is readable from `EvaluationReport`.

**Non-Goals:**

- Metric/TimeSemantics and time intelligence (v4.3) — in particular, **aggregate-composition expressions** (different aggregations per operand, e.g. `SUM(amount) / COUNT(DISTINCT id)`) are Metric territory, not v4.2 calculated fields.
- NamedQuery behavior, trigger gating, or placeholder consumption (v4.4).
- String/regex operators, function calls, subqueries, adapter UDFs, `CASE WHEN` composition (ADR-036 records each rejection).
- Cross-entity calculated fields (join-aware expressions) — v4.2 expressions reference fields of their own entity only.
- pii masking enforcement (unchanged; v4.1 spike ADR remains the plan).

## Decisions

### D1 — Calculated fields are compile-time expansions; the IR stays purely referential

An IR selection references a calculated field by name through the existing `IRSelection.field_id`. The expression tree never enters the IR; expansion happens **at compile time** from the bundle-anchored descriptor snapshot, using `PhysicalBinding` for physical columns. Nothing is interpreted at runtime. This keeps the v4.1 promise intact: the IR fingerprint equals the final query semantics because the referenced definition is pinned by the bundle fingerprint the evidence already carries, and expansion is a deterministic function of (snapshot, bindings) — no post-freeze rewriting of the IR itself.

Rationale for the asymmetry with v4.1 (value resolution pre-freeze, calculated fields post-freeze): value resolution *rewrites filter values* (semantic content of the IR), so it must precede the freeze; calculated-field references *select* a governed definition without altering any IR value, so the reference can freeze first and resolve at compile time against the pinned snapshot. Both paths guarantee "what was audited is what executes". The one input that chain leaves unanchored — the expansion implementation itself — is closed by D12.

### D2 — Expression tree model: closed whitelist, bounded, int-const, fully typed

`ExprNode` is a frozen recursive Pydantic model: leaves are `field` (identifier) and `const` (`int` only); operators are `add`, `sub`, `mul`, `div` with exactly two children. v4.2 additions beyond the source sketch:

- **Constants are `int` only (P0-2 closure)**: `float` is excluded from `const` for the same reason v4.1 excluded it from mapping values — the tree lives in the fingerprint domain (D6 hashes the canonical tree), and canonical-JSON float representation (`1.0` vs `1`, `-0.0`, cross-version repr) threatens fingerprint stability. Reintroducing float constants requires the dedicated canonical-float ADR that v4.1 already demanded. `bool` is rejected (int-subclass discipline); negative `int` constants are valid. Rational constants remain expressible as `int / int` divisions under a `float` output (e.g. `22 / 7`); exact decimal literals are not expressible in v4.2.
- **Bounds**: maximum depth 16, maximum node count 64, and entity `calculated_fields` count ≤ 32 (prevents unbounded growth; mirrors the repo's explicit-bound convention).
- **Output type with a complete inference table (P1-5 closure)**: `add`/`sub`/`mul` infer `int` only when both operands infer `int`, otherwise `float`; `div` always infers `float`; `field` leaves infer their descriptor `data_type` (`int` or `float`; non-numeric leaves are a validation error). The declared output MUST equal the inferred output (violation is a `CF_001` variant naming both types). The compiler enforces the declared output with an explicit CAST in adapter-native output, so the declared type — not dialect arithmetic — is the contract the dual-adapter equivalence tests assert against.
- **`zero_division_policy`**: `Literal["null", "error"]`, default `null`. `null` yields NULL (SQL) / missing (Mongo); `error` fails execution with the structured `CF_005` error. Adapter-generated SQL may use `CASE WHEN` internally — the whitelist constrains *definer-expressible* trees, not adapter output.
- **Static typing and aggregation context**: `field` leaves must reference numeric descriptor fields; no aggregation operators exist in the tree ("trees are row-level by construction"). The IR selection's `aggregation` kind applies uniformly to the expanded expression — including `none`, which is the legal row-level case (the expanded expression appears in the output with no aggregation).
- **`requires` is exact and order-free**: the declared dependency list must equal the *set* of `field` leaves (validated, `CF_002` on mismatch); declaration order is not constrained (set semantics; canonical payloads sort). Exactness keeps intent explicit and makes dependency audit trivial.

### D3 — Operator whitelist rejections are recorded in ADR-045

Carried from the source sketch with v4.1 discipline, each rejection names its alternative path: arbitrary function calls (break fingerprint determinism across function-library versions → versioned extension packs later), subqueries (conflicts with planner-references-only; nested authorization complexity → Metric/NamedQuery), string concatenation/regex (LLM misuse surface; dialect divergence → v2 or controlled UDFs), `CASE WHEN` composition (conditional logic belongs in Metric filter clauses → v4.3). The ADR also records the aggregate-composition rejection (Metric territory, see Non-Goals). ADR numbering uses the **merged registry**: the semantic-enhancement line starts at **ADR-045** because DDS-020 already occupies 036–044; a unified ADR registry index covering both documentation lines is part of the documentation deliverable so future slices cannot collide.

### D4 — Entity-level optional member; N6 applies verbatim

`CalculatedField` attaches to `SemanticEntityDescriptor` as `calculated_fields: tuple[CalculatedField, ...] | None` (bounded count ≤ 32). Entity-level (not field-level) because an expression references sibling fields of one entity. Inherited from v4.1 without deviation: unset members are omitted from `canonical_payload()` entirely (fingerprint-stable introduction, dedicated test); a set member must be non-empty; names are unique within the entity and **must not collide with any `field_id` of the same entity** (so an IR selection's `field_id` resolves unambiguously — `CF_003` namespace rule). **Calculated fields do not compose**: an expression references base fields only — a calculated field is never a valid expression operand, and an expression referencing one fails validation (`CF_002`); this is stated explicitly so a later whitelist extension cannot quietly open composition. Any content change (expression, policy, output type, ordering) is snapshot-breaking and follows the v4.1 five-step republish checklist verbatim. JSON-wire safe: nested models serialize as dict/list; no `frozenset`.

### D5 — Calculated-field references carry a capability; validation is fail-closed

When IR validation resolves a selection's `field_id` to a declared calculated field, the builder adds `"calculated-fields"` to `required_capabilities`. Compilers and adapters that do not support the capability reject the query through the **existing** unsupported-capability path — no new guard code. Three structured error codes, all bounded and evidence-safe:

- `CF_001` — non-whitelisted operator or bound violation (depth/nodes) at definition/validation time; details name the offending operator or bound.
- `CF_002` — expression references an unknown field, or `requires` does not exactly match the referenced set.
- `CF_003` — an IR selection references an identifier that is neither a declared field nor a declared calculated field of the entity (unknown reference; shadowing is impossible by D4's no-collision rule).
- `CF_004` — a calculated field references a field declared `pii: true` — a definition-time validation error (see D11). Future field-masking policy models must join this check when that governance surface lands.
- `CF_005` — runtime division-by-zero under `zero_division_policy: error` (structured execution failure).

Defense-in-depth: the compiler re-validates the tree before expansion and fails closed (`CF_001`) even though validation should make this unreachable.

### D6 — Evidence records expression hashes only (fingerprints-only preserved)

`CompilationEvidence` gains `calculated_field_hashes: tuple[CalculatedFieldHash, ...]` — a frozen record model with `name` and `hash` fields (never a packed `"name:hash"` wire string). For each referenced calculated field the hash is `sha256` over the canonical expression tree **plus `zero_division_policy` plus `output_type`** (both affect execution semantics and CAST behavior, so both are definition identity). The tuple is **sorted by field name**, so compiling the same IR twice — regardless of selection order — produces a byte-identical evidence fingerprint. The new evidence member is **omit-when-unset** in `canonical_payload()` (N6 applied to evidence: evidence for IRs without calculated fields keeps its previous fingerprint). No raw expressions, no physical names, no values.

### D7 — Dual-adapter semantic equivalence is a conformance requirement

The same `CalculatedField` definition over the controlled fixtures must produce semantically equivalent results on the SQL adapter (SQLite; Postgres where the backend is available) and the MongoDB aggregation pipeline, including `zero_division_policy` parity (`null` → NULL vs missing; `error` → equivalent structured failures). Known divergence risk — numeric type fidelity (SQLite dynamic typing vs BSON int/double) — is bounded by exact-value assertions on fixture data. MongoDB conformance tests follow the existing skip-gated pattern when no server is available, and the equivalence suite lives in `tests/conformance/`. SQLite's integer-division trap is called out explicitly: the SQL adapter must CAST to REAL for true division, and the conformance fixtures include an `int / int` case (e.g. `7 / 2` → `3.5`) that would silently produce `3` without the cast.

### D8 — The NamedQuery placeholder is reserved as schema, fail-closed, with zero behavior

A reserved extension kind `named_query_placeholder` with a validated payload schema: `{query_ref: identifier, parameters: [{name: identifier, scalar_type: str|int|float|bool, required: bool}]}` (bounded parameter count; JSON-wire safe; no physical names). It is only accepted when the required capability `named-query-placeholders` is declared — which nothing declares in v4.2 — so every construction path that emits it fails closed today, exactly like any unsupported capability. No planner emits it, no compiler consumes it, no adapter supports it. No `ir_version` bump: IRs without the extension are byte-identical (extensions were already in the fingerprint domain). Schema tests pin the payload validation so the reservation does not rot before v4.4 consumes it. Two forward-compatibility notes: placeholders are structurally inexpressible inside calculated-field expression trees (the closed operator whitelist has no placeholder operator — v4.4 must not open that door when extending the whitelist), and runtime parameter *values* arriving over the wire will need the v4.1 bool/int-subclass discipline when v4.4 gives the schema behavior.

### D9 — `CF_*` attribution mirrors the v4.1 `VS_*` pattern

Bounded attribution codes on evaluation-layer evidence, recorded per selection, aggregated per case, summarized per run via `EvaluationReport.calculated_field_summary()`:

- `CF_HIT` — a selection referenced a declared calculated field and compiled.
- `CF_COMPILE_FAIL` — expansion of a referenced calculated field failed (structured failure recorded on the case).
- `CF_NOT_DECLARED` — a corpus question annotated with an expected calculated field runs against an active bundle that does not declare it (bundle-authoring work, the D4 path).
- `CF_NOT_REFERENCED` — the field is declared but the case did not reference it (prompt-context quality work, the D10 path). Splitting the two keeps D10's effect measurable; the stage gate reads their **combined** rate. Recorded, not crashed: annotation-vs-actual deviations are acceptable recorded attributions, not case failures (the v4.1 questions.yml header rule).

Corpus annotations in `demo/questions/questions.yml` follow the v4.1 pattern (metadata only, not cross-validated, sync obligation documented in the header). The stage gate reads both rates from the per-run summary: **combined `CF_NOT_DECLARED + CF_NOT_REFERENCED < 10%`** and **`CF_COMPILE_FAIL < 5%`** across the annotated corpus; recorded in the roadmap notes.

### D10 — Calculated fields are discoverable to the planner as bounded prompt context

A calculated field the model cannot see cannot be referenced, and an unreferenced definition cannot move the attribution gate. Calculated field identity (`name`, `label`, `description`, `output_type`) enters the model instruction bundle as bounded prompt context, following the existing instruction-bundle bounds and safe-content rules. The model still only *references* by name (N4): it never composes expressions, and every reference is validated (`CF_003`). Risk recorded: prompt-context growth is bounded and instruction bundles already have size gates. Alternative rejected: host-side name mapping only (keeps the model blind, caps `CF_NOT_REFERENCED` improvements at orchestration quality, and duplicates governance outside the semantic layer).

### D11 — Masked and pii fields never meet calculated-field expressions (CF_004, bidirectional)

Rationale: masking is enforced by adapter post-processing on *output columns* (v4.1 spike ADR); a calculated field expands into a *new* output column that no masking rule recognizes, so any expression over a masked column would carry unmasked-derived values past the boundary the ADR carefully guards — a structural bypass of the masking enforcement point. Fail-closed (N1): derived-semantics legitimation (e.g. "mask, then compute") is deferred to a dedicated ADR and rejected outright in v4.2.

The rule is **bidirectional**, because a one-way check leaves the most common governance timing — *declare the calculated field first, apply masking later* — as a silent bypass (P0-3):

1. **CF → pii**: a calculated field referencing a field declared `pii: true` fails at **calculated-field definition time** (`CF_004`).
2. **Pii → CF**: a `pii` declaration applied to a field already referenced by a declared calculated field fails at **bundle validation time** (`CF_004`); the bundle is not publishable until the calculated field is removed or the declaration is retargeted.

The current descriptor exposes `pii` but no field-masking policy model. When DDS-020 introduces that model, its field targets must join the same bidirectional intersection check; this change does not claim an API that does not yet exist.

Both directions live in the **bundle validation layer** (the second direction has no definition-time moment to fire — it must be caught when governance state changes, not when the expression was written). This substantiates the proposal's "authorized through the existing view/bundle chain" claim with a concrete, order-independent intersection rule: the two features can arrive in either order and the combination is rejected either way.

### D12 — Expansion identity is anchored in the evidence chain (mirror of v4.1 D5)

The final output is a function of four anchored inputs (IR fingerprint, bundle fingerprint, physical bindings, declared output type) **plus one unanchored one: the expansion implementation itself** — a compiler upgrade that changes parenthesization or CAST strategy would change the produced output while bundle fingerprint and expression hashes stay constant, silently breaking "what was audited is what executes". Closure, mirroring v4.1's planner-identity guard exactly: the compilation context and evidence both carry `compiler_identity` (an expansion-implementation identity constant, e.g. `"deterministic-expression-compiler:v1"`); the pre-execution guard rejects drift and any one-sided identity (context-without-evidence or evidence-without-context); the strictness clause (missing evidence identity rejected) activates behind an identity-versioning switch. **Rollout order**: the identity must land on the producer (compilation context) and consumer (evidence) sides in the *same* release — the one-sided rejection for expansion identity activates only once both sides populate it, and the strictness clause only with the versioning switch; this is exactly how the v4.1 planner identity rolled out (guard shipped symmetric, `PLANNER_IDENTITY_VERSIONING = False` kept legacy evidence valid). This is a mirror of an existing pattern, not a new mechanism.

## Risks

- **N6 regression**: adding an entity-level member is the exact scenario v4.1's dedicated tests and the optional-member checklist exist for; the checklist in `semantic-layer.md` is extended to name `CalculatedField` and the test pattern (`TestN6OmitWhenUnset`) is reused.
- **Evidence fingerprint domain change**: the new omit-when-unset evidence member keeps evidence without calculated fields byte-identical; evidence *with* hashes is new evidence (no backward-compatibility promise exists for a not-yet-produced shape).
- **Dual-adapter drift**: numeric type fidelity differences could produce silent divergence; mitigated by fixture-based exact-value conformance tests on both adapters and `zero_division_policy` parity tests.
- **Capability gating as behavior switch**: once an entity declares calculated fields, queries referencing them fail closed on adapters without `calculated-fields` support — expected fail-closed behavior, but the docs call out assessing adapter support before first adoption (mirrors the v4.1 first-adoption note).
- **Prompt-context growth (D10)**: unbounded descriptions would bloat instruction bundles; existing bounds and safe-content validators apply; a dedicated bounds test is included.
- **Placeholder schema rot (D8)**: reserved-but-unused schema can drift from what v4.4 actually needs; mitigated by schema validation tests and the explicit note that v4.4 may revise the reservation in its own change (removal is fingerprint-safe by N6 symmetry).

## Migration Plan

1. Land the models and N6 payload changes with dedicated fingerprint-stability tests (no behavior).
2. Land validation (`CF_001/CF_002/CF_003`) and the compiler expansion path behind the `calculated-fields` capability; no descriptor in the repo declares calculated fields yet, so no query path changes.
3. Land the placeholder extension schema (`named_query_placeholder`) with schema tests; still unreachable because nothing declares the capability.
4. Land evaluation attribution and corpus annotations; annotate first, declare calculated fields in the demo bundle second, so the annotated corpus can observe `CF_MISSING → CF_HIT` movement.
5. **Behavior-switch warning**: the first bundle that declares a calculated field changes snapshot fingerprints and requires republication + evidence re-audit (v4.1 checklist verbatim), and requires adapter capability support before referencing queries can execute.

## Tests

Per decision, the following test obligations are pinned:

- **D2/D4**: unit tests — bounds (depth/node/count), bool/const rejection, output-type coherence (`int` + `div` root), name uniqueness and field-id collision rejection, set-means-non-empty, JSON-wire safety, N6 omit-when-unset fingerprint identity (descriptor → snapshot → bundle), set-changes-fingerprint, snapshot-breaking chain for any calculated-field content change.
- **D5**: contract tests — selection referencing a declared CF builds an IR with the capability; undeclared reference → `CF_003`; no-collision disambiguation; unsupported-capability rejection path unchanged.
- **D6**: evidence tests — hashes present for referencing IRs, absent (and fingerprint-stable) otherwise; no raw expression material in evidence payloads.
- **D7**: conformance tests — dual-adapter equivalence on fixtures, both `zero_division_policy` values, skip-gated Mongo as today.
- **D8**: schema tests — placeholder payload validation (types, bounds, identifiers), capability-gated acceptance, fail-closed rejection when the capability is absent, byte-identical IR fingerprints without the extension.
- **D9**: evaluation tests — `CF_HIT` / `CF_COMPILE_FAIL` / `CF_NOT_DECLARED` / `CF_NOT_REFERENCED` attribution, per-selection records with per-case/per-run summaries, evidence-safe serialization, annotation-deviation semantics.
- **D10**: instruction-bundle tests — calculated-field prompt context is bounded and safe-content validated.
- **D11 (refined)**: definition-time tests — a calculated field referencing a `pii` field is rejected with `CF_004`; bundle-validation tests — a `pii` declaration applied to a field already referenced by a declared calculated field is rejected with `CF_004` and the bundle is not publishable (both timing orders); pii fields not referenced by any expression are unaffected. DDS-020 must extend these tests to field-masking policies when that model lands.
- **D12 (refined)**: guard tests — expansion-identity drift rejected; one-sided identity rejected both ways once both sides populate it; both-unset legacy paths unchanged; rollout-order pinning (identity appearing on only one side during rollout does not reject until activation); versioning-strict activation behavior (mirror of `test_planner_identity_guard.py`).
- **D6 (refined)**: hash records are frozen models sorted by name; the hash covers tree + policy + output type; the same IR compiled with different selection orders produces identical evidence fingerprints.
- **D2 (refined)**: inference-table tests (`add`/`sub`/`mul` int×int → int, mixed → float, `div` → float; declared ≠ inferred rejected); `int / int` conformance fixture (e.g. `7 / 2` → `3.5`, catching SQLite integer division).

## Open Questions

- **Cross-entity calculated fields**: expressions over joined entities need join-plan awareness; defer to the Metric slice or a dedicated change.
- **Decimal/numeric fidelity**: if fixture-based equivalence proves fragile for division-heavy expressions, a dedicated ADR on declared numeric semantics (e.g. scale/precision contracts) may be needed; v4.2 keeps exact-value assertions instead of a general contract.
- **Per-question CF expectations**: should corpus annotations eventually be machine-checked against declared bundles (the sync obligation is currently documentation-only, as in v4.1)? Revisit when the demo bundle first declares calculated fields.
