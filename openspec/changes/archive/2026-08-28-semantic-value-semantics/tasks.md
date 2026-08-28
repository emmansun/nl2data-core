# semantic-value-semantics Tasks

## 1. ValueSemantics model (views layer)

- [x] 1.1 Add frozen `ValueSemantics` Pydantic model in `src/nl2data_core/views/models.py` with `value_mapping` (dict[str, str | int] — v4.1 value domain excludes float, per design D2), `display_order` (list[str] | None), `sample_values` (list[str | int], non-binding, documented as prompt-context only), `pii` (bool, default False), `unknown_value_policy` (Literal["reject", "warn"], default "reject"); validators: reject boolean mapping values (bool is an int subclass) and reject an empty `value_mapping` when the member is provided (set means non-empty); JSON-wire safe (no frozenset fields)
- [x] 1.2 Add optional `value_semantics` member to `SemanticFieldDescriptor` and include it in `canonical_payload()` only when set (N6 omit-when-unset); export from `views` public API
- [x] 1.3 Unit tests: model bounds, immutability, canonical payload omission when unset, fingerprint change when set, empty-mapping rejection, boolean-value rejection, sample-vs-mapping distinction documented in field docs

## 2. N6 invariant protection

- [x] 2.1 Dedicated test: introducing an unset `ValueSemantics` leaves descriptor fingerprint, catalog snapshot fingerprint, and bundle fingerprint identical to pre-introduction values
- [x] 2.2 Test pinning the snapshot-breaking chain: editing any ValueSemantics content (mapping entry, sample values, policy, display order) changes the snapshot fingerprint, a bundle built from the prior snapshot fails `catalog_incompatible` validation, and republication against the new snapshot restores validity with old evidence treated as stale
- [x] 2.3 Add N6 to the code-review/docs checklist so future optional members (CalculatedField, Metric) inherit the omit-when-unset rule and test pattern

## 3. Resolver-stage value resolution

- [x] 3.1 Add structured resolution error types in `ai/`: `VS_001` (unknown value: `field`, `attempted_value`, `known_business_terms`) and `VS_002` (disallowed operator: `field`, `attempted_operator`, `allowed_operators`); surface both as structured resolution failures (compatible with clarification flow), not compiler errors
- [x] 3.2 Implement deterministic `value_mapping` lookup on filter values in `ai/resolver.py` before IR freeze, reading the mapping from the **bundle-referenced descriptor snapshot** (by catalog fingerprint; snapshot unavailable → fail closed); accept controlled pass-through of stored values (`value ∈ mapping.values()` → recorded pass-through); restrict mapped fields to `eq`/`in` operators (others → structured operator rejection); honor `unknown_value_policy` (`reject` → VS_001 failure; `warn` → proceed with warned outcome); fields without value semantics untouched
- [x] 3.3 Contract tests: mapping hit produces frozen IR with stored value; reject-policy miss produces VS_001 with known business terms and no IR; warn-policy miss proceeds with warned outcome; stored-value pass-through resolves without clarification; **type-strict membership** (wire-stringified value canonicalized to declared domain type or treated as miss, never silently coerced); mixed `in` list resolves per value with pre-freeze dedup; operators other than eq/in on mapped fields rejected with VS_002; lookup uses the bundle-anchored snapshot (stale registry does not leak in) and missing snapshot fails closed; executable/non-scalar values still rejected by existing validation before resolution
- [x] 3.4 Security tests: resolution errors expose only the attempted business value and known terms (no physical names, no mapping contents beyond known terms), and warn-policy warnings follow evidence redaction conventions
- [x] 3.5 Implement the resolution outcome channel: per filter-value outcome (`hit` / `pass_through` / `warned` / `miss` / `unpolicied`) aggregated per filter occurrence, plus the descriptor-snapshot fingerprint, returned by the resolver for orchestration/evaluation consumption; outcomes attach to `CaseEvidence` in evaluation scenarios; test that outcomes never enter `CompilationEvidence`

## 4. Planner-identity guard (compilation boundary)

- [x] 4.1 Extend `verify_pre_execution_guard` in `src/nl2data_core/compilation/contract.py`: reject evidence/context planner-identity mismatch and any one-sided identity (context-without-evidence **or** evidence-without-context); include the strictness clause (missing evidence identity rejected) behind identity-versioning activation
- [x] 4.2 Tests: drift rejection with human-safe reason; context-without-evidence rejection; evidence-without-context rejection; both-unset legacy paths unchanged; strictness clause active/inactive behavior

## 5. Corpus annotations and attribution dimensions

- [x] 5.1 Annotate enum-filter entries in `demo/questions/questions.yml` with value-semantics metadata (field, business terms, expected mapping outcomes)
- [x] 5.2 Add bounded `VS_HIT` / `VS_MISS` / `VS_UNPOLICIED` attribution derived from the resolution outcome channel (pass-through outcomes reported distinctly), with granularity: per filter value recorded, per case aggregated, per run summarized; outcomes attach to evaluation-layer evidence so offline reruns keep attribution; counts readable by stage gates
- [x] 5.3 Evaluation tests: annotated hit reports `VS_HIT`; miss reports `VS_MISS` with failing outcome; unpolicied field reports `VS_UNPOLICIED`; serialized attribution is evidence-safe (no raw values)

## 6. pii spike (no runtime implementation)

- [x] 6.1 Spike: map the result-row serialization path (facade, demo, export) and evaluate the three candidate enforcement points (result-level obligations, facade export filter, adapter post-processing contract); deliverable is an ADR recommendation for a follow-up change — `pii` remains schema + fingerprint only in this change

## 7. Documentation

- [x] 7.1 Document the N4 restatement ("no probabilistic construction; deterministic governed lookup permitted") and the VS_001 ownership change (resolution stage, pre-IR-freeze) in README and semantic-layer docs
- [x] 7.2 Document the full upgrade checklist: mapping edit → catalog snapshot fingerprint change → bundle republication → stale-evidence re-audit; mark pii masking as deferred behind the spike with v2 outlook for display-order behavior
- [x] 7.3 Run quality gates: pytest, ruff, mypy; run `openspec validate semantic-value-semantics --type change`

## 8. Roadmap linkage

- [x] 8.1 Record the v4.2 gate (`VS_HIT ≥ 90%`, read from the new attribution dimensions) in the roadmap documentation so the next slice cannot start without the gate being met
