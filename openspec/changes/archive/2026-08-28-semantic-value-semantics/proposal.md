# semantic-value-semantics Proposal

## Why

Natural-language questions constantly filter on coded values ("refunded orders", "pending payments"), and today the planner must guess the stored representation (`status = 4`) or the question fails. Value-level semantics makes the business-word → stored-value mapping a governed, fingerprinted semantic asset so enum filters compile deterministically instead of being probabilistically constructed. This is the v4.1 slice of the agreed semantic-layer enhancement roadmap (P0 value semantics → P1 metric DSL → P2 named queries) and the only slice in this change.

Grounding the design against the real pipeline surfaced four seam corrections with explicit rulings, all captured in `design.md`: resolution belongs in the intent resolver before IR freeze (not the compiler), new optional semantic members must be omitted from canonical payloads when unset (new invariant N6, the staged-fingerprint promise otherwise silently breaks on day one), value-mapping edits are snapshot-breaking events that require bundle republish (not just evidence invalidation), and the `pii` masking acceptance criteria are deferred behind a result-row serialization spike because compilation evidence is fingerprints-only by construction.

## What Changes

- Add `ValueSemantics` as an optional member of `SemanticFieldDescriptor` (`value_mapping`, `display_order`, `sample_values`, `pii`, `unknown_value_policy`), fully inside the descriptor fingerprint domain.
- Perform value resolution in the **intent resolver stage, before IR freeze**, reading the mapping from the **bundle-referenced descriptor snapshot** (by catalog fingerprint, never a live registry; unavailable snapshot fails closed): a filter value that is a business word resolves via `value_mapping` lookup (deterministic lookup is not construction — N4 restated); a filter value equal to a governed stored value **passes through** unchanged; values outside the mapping's keys and values follow `unknown_value_policy` (`reject` → structured `VS_001` error carrying `known_business_terms`; `warn` → proceed with a warning on the outcome channel). In v4.1, mapped fields accept only `eq`/`in` operators.
- Introduce a structured **resolution outcome channel** — per filter-value outcome (`hit` / `pass_through` / `warned` / `miss` / `unpolicied`) aggregated per filter occurrence, plus the descriptor-snapshot fingerprint — consumed by orchestration and evaluation (attaching to evaluation-layer evidence); it never enters compilation evidence, which stays fingerprints-only.
- Establish invariant **N6**: unset optional semantic members MUST be omitted from `canonical_payload`, so adding `ValueSemantics` cannot change fingerprints of bundles that do not use it (verified by a dedicated test).
- Extend `verify_pre_execution_guard` with a planner-identity drift check; any **one-sided** planner identity (context without evidence, or evidence without context) is a guard failure reason, and once planner identity versioning is active, evidence **missing** `planner_identity` is rejected outright (fail closed, strictness before version divergence).
- Document and enforce the full change-propagation chain for **any ValueSemantics content change** (mapping, sample values, policy, display order): content change → catalog fingerprint change → bundle republish required (`catalog_incompatible` is expected fail-closed behavior) → stale evidence requires re-audit.
- Defer `pii` runtime masking: the flag is schema+fingerprint only in this change; enforcement point (candidate: adapter post-processing contract) is resolved by a spike into the result-row serialization path.
- Annotate the demo question corpus for enum-filter questions and add `VS_HIT` / `VS_MISS` / `VS_UNPOLICIED` attribution dimensions to the evaluation report.

Out of scope (later slices, trigger- or gate-gated): `CalculatedField` expression DSL (v4.2), `Metric`/`TimeSemantics` time intelligence (v4.3), `NamedQuery` (v4.4), fiscal calendars, per-metric timezones, derived ratio operators, pii masking implementation.

## Capabilities

### New Capabilities

- `value-level-semantics`: Business-word to stored-value semantics as a first-class, fingerprinted field-level asset: the `ValueSemantics` member, resolver-stage deterministic resolution, `VS_001` structured failure with self-explaining `known_business_terms`, `unknown_value_policy` handling, and the N6 omit-when-unset fingerprint invariant that makes staged semantic-layer rollout safe.

### Modified Capabilities

- `structured-intent-resolution`: Resolution now performs deterministic value-semantics lookup on filter values against the bundle-anchored snapshot before the IR freezes; the IR fingerprint equals the final query semantics. Governed stored values pass through; enum values outside the mapping domain produce a structured resolution error (or outcome-channel warning per policy) instead of a guessed literal; mapped fields accept only `eq`/`in` in v4.1.
- `compiler-governance-boundaries`: The pre-execution guard additionally verifies planner-identity consistency between compilation evidence and context, and treats missing `planner_identity` on evidence as a guard failure once identity versioning is active.
- `semantic-model-bundles`: New optional semantic members enter the fingerprint domain without changing fingerprints of bundles that leave them unset; ValueSemantics content changes are snapshot-breaking events that require bundle republication against the new catalog snapshot.
- `evaluation-runner`: Evaluation reports carry value-semantics attribution dimensions (`VS_HIT`, `VS_MISS`, `VS_UNPOLICIED`) for annotated enum-filter corpus questions, distinguishing correct hits, misses, and questions with no value semantics defined.

## Impact

- **Code**: `src/nl2data_core/views/models.py` (ValueSemantics member + canonical payload), `src/nl2data_core/ai/resolver.py` + `src/nl2data_core/ai/models.py` (value resolution, VS_001 error type, clarification options), `src/nl2data_core/compilation/contract.py` (planner-identity guard lines), evaluation report models for attribution dimensions.
- **Corpus/demo assets**: `demo/questions/questions.yml` (enum-filter annotations); no demo schema or demo runner behavior change required.
- **Fingerprints/evidence**: No existing fingerprint changes when `ValueSemantics` is unset (N6-tested); value-mapping adoption by a bundle changes bundle fingerprints and requires republication — existing evidence chains stay intact for untouched bundles.
- **Tests**: unit (model bounds, N6 fingerprint stability, canonical payload omission), contract (resolution hit/miss/warn paths), security (no physical value leakage, error self-explanation without data exposure), evaluation (attribution dimensions).
- **Docs**: README semantic-layer section, ADR-style decision records for N4 restatement, N6, snapshot-breaking upgrades, and planner-identity strictness.
