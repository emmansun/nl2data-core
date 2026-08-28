# semantic-value-semantics Design

## Context

The semantic layer enhancement roadmap (see "Semantic enhancement.md": P0 value semantics → P1 calculated fields → P2 metrics/time intelligence → P3 named queries) starts with value-level semantics as the only mandatory v4.1 deliverable. Grounding the design against the actual pipeline produced four seam corrections, each with an explicit ruling that this design records:

1. **The compiler cannot host value resolution.** `CompilationContext` carries the IR plus fingerprints only — the `SemanticDescriptor` (and therefore any `value_mapping`) never enters compilation. The sole content exception is `compiler_context: PhysicalBinding`.
2. **Canonical payloads include every key unconditionally.** `SemanticFieldDescriptor.canonical_payload()` always emits all keys, so adding an optional member naively would change every descriptor fingerprint on day one — breaking the staged-rollout promise ("bundles without value mappings keep their fingerprints").
3. **Compilation evidence is fingerprints-only by construction.** There are no field values in evidence to mask, so the doc's `pii` masking acceptance criterion has no enforcement point at that layer; the real exposure is result rows, which travel outside the evidence chain.
4. **Descriptor fingerprints feed snapshot compatibility.** `descriptor.catalog_fingerprint` participates in the `catalog_incompatible` bundle validation check, so value-mapping edits are snapshot-breaking events, not merely fingerprint-changing ones.

Existing assets this change builds on: `IntentFilter`→`IRFilter` translation already lives in `ai/resolver.py`; `CompilationContext`/`CompilationEvidence` already carry `planner_identity` (in the evidence fingerprint domain) and `PLANNER_IDENTITY = "deterministic-join-planner"` exists in `planning/join_planner.py`, set by the workflow runtime; `verify_pre_execution_guard` currently never compares the two; the evaluation framework has report/evidence models ready for attribution extensions.

## Goals / Non-Goals

**Goals:**

- Enum filters resolve from a governed, fingerprinted business-word → stored-value mapping instead of planner guesses.
- IR fingerprint equals final query semantics (resolution completes before IR freeze).
- Staged fingerprint rollout is safe: unset `ValueSemantics` changes no fingerprint (N6).
- Planner-identity drift becomes detectable at the pre-execution boundary.
- Value-semantics quality becomes measurable (`VS_HIT` / `VS_MISS` / `VS_UNPOLICIED` attribution).

**Non-Goals:**

- `CalculatedField` DSL, `Metric`/`TimeSemantics`, `NamedQuery` (later slices; N6 is designed to serve them).
- Fiscal calendars, per-metric timezones, derived ratio operators, display-order-driven `ORDER BY` generation (schema fields may be reserved, behavior is not).
- `pii` runtime masking implementation (deferred behind the result-row serialization spike; the flag itself is schema + fingerprint only).
- Any relaxation of N1–N5 (fail-closed, fingerprint-domain consistency, physical-name isolation, planner non-construction, evidence auditability).

## Decisions

### D1 — Resolution happens in the intent resolver, before IR freeze (ruling: Option B)

A filter value that is a business word resolves via `value_mapping` lookup inside the intent-resolution stage (`ai/resolver.py`), before the `SemanticQueryIR` is built and frozen.

- **Why not the compiler (Option A)?** The compiler context carries fingerprints, not descriptor content; injecting the mapping via `compiler_context` would make the IR say `"refunded"` while the artifact says `4`, so audit replay would need to trust an extra non-fingerprinted mapping. It also forces a new evidence record type for the resolved value, which the fingerprints-only evidence model forbids.
- **Why not a new pipeline stage (Option C)?** `ai/resolver.py` already owns structured-intent → IR translation; a dedicated stage adds pipeline surface for no benefit.
- **N4 restated (goes in docs/ADR):** the planner must not *probabilistically* construct values; *deterministic lookup* against a governed mapping is permitted. This is a precision improvement, not a relaxation.
- **Consequence:** `VS_001` is a resolution-stage error, not a `CompilationError`. Error code, `field`, `attempted_value`, and `known_business_terms` design are unchanged; only ownership moves. The compilation-evidence chain needs **no new record type** — resolution-process observability lives in the outcome channel (D9), not in evidence. Moving the resolution point creates three follow-on obligations that D8–D10 close: where the mapping is read from, how the resolution process becomes observable, and what happens when the planner emits a stored value directly.

### D2 — `ValueSemantics` shape and attachment

`ValueSemantics` is an optional member of `SemanticFieldDescriptor` (frozen Pydantic model, matching views package conventions):

- `value_mapping: dict[str, str | int]` — direction is **business word → stored value**; the reverse (result interpretation) belongs to adapter/result layers, not the semantic layer. The v4.1 value domain is `str | int` only: `float` is excluded because mapped values enter canonical payloads and canonical-JSON float representation (`1.0` vs `1`, `-0.0`, cross-version repr) threatens fingerprint stability — reintroducing `float` requires a dedicated ADR with normalization rules. Because `bool` is an `int` subclass in Python, a validator SHALL reject boolean mapping values explicitly.
- `display_order: list[str] | None` — ordered enum presentation; reserved in schema, no `ORDER BY` behavior in v1.
- `sample_values: list[str | int] | None` — prompt-context enrichment only; **never** a SQL constraint or enum domain, and subject to the same `str | int` value domain as the mapping (these values enter the fingerprint domain too). The distinction from `value_mapping` must be stated in field docs (confusing them would let sample values masquerade as an exhaustive domain).
- `pii: bool = False` — schema + fingerprint only in this change (see D6).
- `unknown_value_policy: Literal["reject", "warn"] = "reject"` — resolution-stage policy for values outside `value_mapping` (N1: default is reject); `warn` is observable through the resolution outcome channel (D9), never through compilation evidence.
- **Set means non-empty:** a provided `ValueSemantics` with an empty `value_mapping` is a validation error, not a silent default — an explicitly passed empty mapping must not count as a "set" member with no content (N6 boundary).
- Decision record: ADR-030 (value semantics enter the fingerprint domain; pii masking does not affect fingerprints) is the ADR of record for this member.
- JSON-wire discipline: `dict`/`list` fields only — **no `frozenset`** (this repo has been bitten by frozenset serialization before). The canonical payload includes `value_semantics` **only when set** (D3).

### D3 — Invariant N6: unset optional members are omitted from canonical payloads

New invariant, staged alongside N1–N5 in the semantic-layer docs: *an optional semantic member that is unset MUST be omitted from its owner's `canonical_payload`, so its introduction cannot change any fingerprint of artifacts that do not use it.* Enforced by a dedicated test ("adding an unset `ValueSemantics` does not change the descriptor fingerprint") and applied prospectively to `CalculatedField` and `Metric` in later slices. This is the precondition for the doc's §6.1 staged-fingerprint promise; without it the promise fails on day one.

### D4 — ValueSemantics content changes are snapshot-breaking: the full propagation chain

Any `ValueSemantics` content change — a `value_mapping` entry, `sample_values`, `unknown_value_policy`, or `display_order` — produces, in order: descriptor fingerprint change → catalog snapshot fingerprint change → bundles referencing the old snapshot **fail** `catalog_incompatible` validation against the new snapshot (expected fail-closed behavior) → the bundle must be **republished** against the new snapshot → previously issued evidence for queries under the old bundle is stale and requires re-audit. The upgrade checklist in the docs records all four steps; "re-audit evidence" alone is insufficient. A test pins the chain: editing any ValueSemantics content invalidates a bundle built from the older snapshot until republished.

### D5 — Planner-identity drift guard at the pre-execution boundary

`verify_pre_execution_guard` gains, symmetrically: (a) rejection when `evidence.planner_identity` differs from `context.planner_identity`; (b) rejection when exactly one side carries an identity — context-without-evidence **and** evidence-without-context, since a one-sided identity cannot be drift-checked and must not pass silently; (c) once planner-identity versioning is actively used (identity constant versioned), evidence missing `planner_identity` is rejected outright — strictness lands **before** version divergence so no evidence can exist in an "unversioned" state. Compatibility: today the workflow runtime already sets `planner_identity` on both sides; direct-compile paths that set neither keep working until versioning is enabled (the strictness clause is activated with versioning, per ADR-033).

### D6 — `pii` is deferred behind a result-row serialization spike

Since compilation evidence carries no values, `pii=True` has nothing to mask at that layer. This change: flag is schema + fingerprint domain only; acceptance criteria for masking are **moved out** of v4.1 and into a spike task that maps the result-row serialization path (facade/demo/export) and evaluates three candidate enforcement points — result-level obligations (analogous to `mandatory_filter_fingerprints`), a facade-level export filter, and an **adapter post-processing contract** (working preference: masking must happen before data leaves the process boundary, and the adapter is the single choke point all result rows cross). The spike produces an ADR; implementation lands in a later change.

### D7 — Corpus annotations and attribution dimensions

`demo/questions/questions.yml` entries whose filters hit enum-coded fields gain value-semantics annotations (field, business terms, expected mapping hits). The evaluation report gains a bounded attribution enum — `VS_HIT` (filter value resolved from `value_mapping`), `VS_MISS` (defined mapping but unresolved value), `VS_UNPOLICIED` (no value semantics defined for the field) — with granularity: recorded **per filter occurrence**, aggregated **per case**, reported **per run**. The data source is the resolution outcome channel (D9) — attribution and warn observability are one fix, not two. Pass-through outcomes (D10) are reported distinctly so audits can distinguish a business-word hit from an accepted stored value. The v4.2 gate (`VS_HIT ≥ 90%`) reads this attribution; the gate itself lives in the roadmap, not in code.

### D8 — Mapping lookups read the bundle-referenced descriptor snapshot, fail-closed

The resolver SHALL resolve against the descriptor snapshot referenced by the active bundle (located by catalog fingerprint), never against a live registry; an unavailable snapshot fails resolution closed. Rationale: if the registry has evolved (`refunded→5`) while the active bundle anchors snapshot S1 (`refunded→4`), a live lookup would freeze IR value 5 under evidence whose `bundle_fingerprint` corresponds to 4 — silently breaking "IR fingerprint = final semantics" with no alarm mechanism. This also closes D4's chain on the query path: mapping edits become visible to resolution only after bundle republication, eliminating the invisible back door.

### D9 — Resolution outcome channel (the third piece of D1)

The resolver returns a structured resolution outcome — per filter value: `hit` (business word resolved), `pass_through` (stored value accepted by membership), `warned` (warn-policy miss), `miss` (reject-policy miss, paired with VS_001), `unpolicied` (no mapping declared) — aggregated per filter occurrence for consumers. The mapping-source fingerprint is the **descriptor fingerprint** (finest granularity: it distinguishes which field's which mapping revision was used; the bundle fingerprint is already carried evidence-side and is not duplicated). Persistence boundary: outcomes are in-process transient at the resolver; in evaluation scenarios they attach to `CaseEvidence` — evaluation-layer evidence, not `CompilationEvidence` — so offline report reruns keep the attribution data. The channel never enters `CompilationEvidence`, which stays fingerprints-only. Division of labor completing D1: errors → resolver (VS_001/VS_002), values → IR, **process → outcome channel**. `warn` stays in v4.1 precisely because this channel makes it observable; without the channel, an unobservable policy would be worse than none.

### D10 — Pass-through of stored values and operator applicability

Planners may emit the raw stored value directly (`status = 4`), especially when few-shot context contains codes. Ruling: **controlled pass-through** — a filter value that is a member of `value_mapping.values()` passes through unchanged (a deterministic membership check against the governed domain, so nothing outside governance enters); a value that is neither a mapping key (business word) nor a mapping value (stored value) fails closed with `known_business_terms`. All membership checks and lookups are **type-strict**: values arriving over the JSON wire are explicitly canonicalized to the mapping's declared domain type (`str` or `int`) before comparison, and a value that cannot be uniquely canonicalized is treated as a miss, never silently coerced — silent coercion could pass a stringified code into `WHERE status = '4'` (a hard error on strict databases, silently-empty results on loose ones). `IN` lists resolve **per value independently** (a mixed list like `("refunded", 4)` is legal); duplicate resolved values are removed **before IR freeze** so the IR fingerprint stays canonical and construction-path-independent; a per-value miss under the reject policy fails the whole filter. Rejected alternative (strict rejection of everything but business words): it would break previously-working queries the week mappings are enabled — the model emitting the *correct* code would start failing — producing mass false negatives. Operator applicability: in v4.1, filters on fields with declared value semantics accept only `eq` and `in`; other operators (`ne`, `lt`, `gt`, ...) on a mapped field are rejected with `VS_002` — same shape as VS_001 (`field`, `attempted_operator`, `allowed_operators`) — because comparing status codes carries no business meaning and the mapping is not order-total. Relaxation deferred to a later ADR.

## Tests

- **D3/N6**: unset member leaves descriptor/snapshot/bundle fingerprints unchanged; setting one changes them; empty-mapping and boolean-value validation.
- **D4**: any ValueSemantics content edit breaks old-snapshot bundle validation until republication; old evidence treated as stale.
- **D8**: lookup uses the bundle-anchored snapshot (a stale registry does not leak in); unavailable snapshot fails closed.
- **D9**: five outcome states produced correctly; outcomes attach to `CaseEvidence` in evaluation scenarios and never appear in `CompilationEvidence`.
- **D10**: type-strict membership (a wire-stringified code is canonicalized to the declared domain type or treated as a miss); pass-through of an exact stored value; mixed `IN` list per-value resolution with pre-freeze dedup; `VS_002` operator rejection.
- **Planner identity guard**: drift rejection, both one-sided cases, both-unset legacy paths, strictness-clause activation.

## Risks / Trade-offs

- [Resolver-stage resolution moves an error earlier than some integrators expect (they may probe the compiler for VS_001)] → Document the ownership change prominently; `known_business_terms` makes the error self-explaining so orchestrators can prompt for clarification directly.
- [Strict planner-identity guard could reject legacy evidence produced before the field existed] → Strictness clause activates together with identity versioning; the mismatch/missing-vs-context checks are safe today because the runtime already populates both sides.
- [Snapshot-breaking mapping edits surprise bundle authors] → The `catalog_incompatible` failure message and upgrade checklist make the required republication step explicit; the behavior is the intended fail-closed semantics, not a defect.
- [Omit-when-unset is easy to regress with each new optional member] → N6 gets a named invariant, a dedicated test, and a code-review checklist entry; later slices inherit the test pattern.
- [Sample values mistaken for an enum domain by prompt authors] → Field-level docstrings and docs state that only `value_mapping` constrains SQL.
- [Declaring a mapping on a previously unmapped field flips behavior: model guesses outside the governed domain that used to pass now fail] → Roll out per field with corpus validation first; controlled pass-through (D10) keeps in-domain stored values working; the corpus annotations and attribution dimensions measure the flip instead of hiding it.
- [Pass-through widens what a mapping accepts beyond business words] → Membership is a deterministic check against the governed value set; the outcome channel records `pass_through` distinctly so audits can separate business-word hits from accepted stored values.

## Migration Plan

1. Land `ValueSemantics` with N6 (no behavior change for existing bundles — fingerprints provably unchanged).
2. Enable resolver-stage resolution for fields that declare `value_mapping`; fields without it are untouched (`VS_UNPOLICIED` attribution, not failure). **Behavior-switch warning:** declaring a mapping on a previously unmapped field changes outcomes — guesses outside the governed domain flip from passing to rejection (see Risks); enable per field, with corpus validation and pass-through (D10) confirmed first.
3. Land the planner-identity guard lines with tests; activate the missing-identity strictness clause when identity versioning starts.
4. Publish corpus annotations + attribution dimensions; record the v4.2 gate in the roadmap.
5. Rollback is trivial at every step: resolution only activates for fields with declared mappings, and unset members leave no fingerprint trace.

## Open Questions

- **pii enforcement point** (D6 spike): result-level obligation vs facade export filter vs adapter post-processing contract; spike deliverable is an ADR plus a follow-up change proposal.
- **Per-tenant vocabularies**: if business vocabularies differ per tenant while descriptors are shared across tenants, `value_mapping` ownership may need to move to the view/policy layer. Not a v4.1 question, but recorded now so a future relocation is a conscious fingerprint-domain decision rather than an accident.
- Whether `display_order` should later drive ordering via a dedicated IR directive or stay presentation-only (v2 concern; no v1 commitment).
