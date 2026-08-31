# ADR-045: Calculated-field operator whitelist and its recorded rejections

> **Readers**: architects, security reviewers, and the author of any
> change that wants to extend the calculated-field expression language.
> **Prerequisites**: [Semantic layer](semantic-layer.md) and
> [Evidence and fingerprints](evidence-and-fingerprints.md).
>
> [简体中文](adr-calculated-field-operator-whitelist.zh-CN.md)
>
> Status: **Accepted** — v4.2 slice `calculated-field-semantics`,
> design decision D3.

## Why

Calculated fields (`CalculatedField` on
`SemanticEntityDescriptor`, v4.2) expand a declared expression tree into
adapter-native output at compile time. The expression language is a
**closed whitelist** of six operators: `field`, `const`, `add`, `sub`,
`mul`, `div`. Anything outside the whitelist is rejected at definition
time with `CF_001` — the expression never reaches the IR (which carries
references only, invariant N4) and never runs at query time.

This ADR records each rejection *with its alternative path* so future
slices extend the language by decision instead of by pull-request
accident. Every rejection below was evaluated against three invariants:
fingerprint determinism (the expanded output must be byte-stable across
function-library versions and adapter dialects), planner-references-only
(the model references governed names, it never composes expressions),
and fail-closed governance (every construct must be authorizable and
auditable before it executes).

## Recorded rejections

### Arbitrary function calls — rejected

Function libraries differ across database engines and versions; a
declaration that means one thing on SQLite and another on PostgreSQL
breaks the dual-adapter equivalence guarantee (design D7) and the
fingerprint determinism of the expansion identity (design D12).

**Alternative path**: versioned extension packs — a future slice may
declare a bounded, fingerprinted set of functions per capability, so the
declared set travels with the bundle and adapters can reject what they
cannot expand deterministically.

### Subqueries — rejected

A subquery conflicts with planner-references-only (N4): the model would
compose query structure, not reference governed names. It also drags
nested authorization complexity into the compiler (which scopes apply
inside the subquery?) for zero v4.2 value.

**Alternative path**: multi-entity planning (already governed join
vocabulary, compiled at query time) for cross-entity questions, and
NamedQuery (v4.4) for parameterized named queries.

### String concatenation and regular expressions — rejected

String composition is the highest-misuse surface for an LLM planner
(format-string injection into outputs, regex complexity bombs) and the
highest dialect-divergence risk (concatenation operators, regex
dialects, and collation rules differ everywhere).

**Alternative path**: a v2 decision, or controlled UDFs once the
extension-pack mechanism exists with per-adapter conformance.

### `CASE WHEN` composition — rejected

Conditional logic belongs in Metric filter clauses (v4.3): a Metric can
declare guarded aggregations that are reviewed, fingerprinted, and
authorized as named semantics. Embedding branch logic in calculated
fields would bypass that review surface.

**Alternative path**: Metric/TimeSemantics (v4.3) filter clauses.

### Aggregate composition (`SUM(a) / COUNT(DISTINCT b)`) — rejected

Per-operand aggregations (a different aggregation per operand) are
**Metric territory** (v4.3), not a calculated-field extension. A
calculated field expands one expression over row values — possibly
aggregated uniformly by the selection's aggregation kind — while
aggregate composition needs a different planning model (aggregation
bindings per operand, `DISTINCT` handling, empty-set semantics).
Opening it here would duplicate Metric's planning responsibility with a
weaker authorization surface. This is a recorded **non-goal**, not a
deferred v4.3 feature request.

**Alternative path**: Metric (v4.3) derived metrics.

## What the whitelist does include

`field` (numeric leaf, resolved through the physical binding),
`const` (int-only — float and bool are rejected to keep the expression
fingerprint domain stable; negative ints are valid), and the arithmetic
operators `add`, `sub`, `mul`, `div` (true division; the SQL dialects
CAST to enforce the declared output type, and SQLite's integer-division
truncation is caught by the conformance fixture `7 / 2 → 3.5`).
`zero_division_policy` (`null` | `error`) is declared per field and
enforced by guarded expansion (`CF_005` under `error`).

## Related enforcement invariants

- **CF_004 (bidirectional pii isolation)**: an expression never
  references a `pii: true` field, and a bundle cannot apply `pii` to a
  field already referenced by a declared calculated field. Future
  field-masking policy targets must join the same check. Masking is
  enforced by adapter post-processing on
  output columns (see the
  [pii masking ADR](adr-pii-masking-enforcement-point.md)); a derived
  output column would carry unmasked-derived values past that
  enforcement point — a structural bypass. Fail-closed; "mask, then
  compute" is deferred to a dedicated future ADR.
- **No composition**: a calculated field cannot reference another
  calculated field (`CF_002`) — one governed definition per output, and
  no transitive fingerprint or masking chains.
- **Placeholder inexpressibility**: the reserved NamedQuery placeholder
  schema (v4.4) is structurally unreachable inside an expression tree;
  v4.4 must not open this path when it extends the whitelist.

## Consequences

- Extensions to the whitelist require a new ADR (or an amendment to
  this one) plus dual-adapter conformance coverage before any operator
  is accepted.
- The [ADR registry](adr-registry.md) is the single numbering authority;
  new ADRs take the next free number from the unified list.
