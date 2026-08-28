# ADR: pii masking enforcement point (result-row serialization spike)

> **Readers**: architects, security reviewers, and the author of the
> follow-up pii-masking change.
> **Prerequisites**: [Semantic layer](semantic-layer.md) and
> [Evidence and fingerprints](evidence-and-fingerprints.md).
>
> [简体中文](adr-pii-masking-enforcement-point.zh-CN.md)
>
> Status: **Accepted (spike deliverable)** — v4.1 slice
> `semantic-value-semantics`, design decision D6.

## Why

`SemanticFieldDescriptor.value_semantics.pii: bool` entered the schema
and the fingerprint domain in v4.1, but masking has **no runtime
implementation** in this slice. The reason is structural: compilation
evidence carries fingerprints only — there are no field values at that
layer to mask. The real exposure is **result rows**, which travel
outside the evidence chain. This spike maps where result rows are
serialized and evaluates the three candidate enforcement points so the
follow-up change starts from a decided seam instead of re-litigating it.

## Where result rows cross a boundary today

1. **Adapter execution** — every adapter returns a bounded result whose
   rows are scalar tuples (`nl2data_core/adapters/models.py` enforces
   `str | int | float | bool | None` per cell). This is the only place
   in the library where physical column values become in-process data.
2. **Workflow outcome** — the runner wraps the adapter result into the
   execution outcome; protected results also produce a
   `result_fingerprint` for `ResultLineageEvidence` (rows themselves
   never enter evidence).
3. **Facade boundary** — `facade.aquery(...)` hands the outcome,
   including `result.rows` and `result.column_names`, to the host
   application.
4. **Demo / export** — the demo entrypoints print
   `outcome.result.rows` directly; any host-side export (CSV, notebook
   rendering) serializes from the same facade result object. There is
   no library-side export API, so "export" is a host activity fed by
   the facade result.

## Candidate enforcement points

### A. Result-level obligations (analogous to `mandatory_filter_fingerprints`)

Declare per-field masking obligations at planning time and verify them
at the artifact-guard boundary, like mandatory filter obligations.

- **Pros**: reuses the existing obligation/verification machinery;
  obligations are auditable fingerprints; fails closed pre-execution.
- **Cons**: obligations verify *intent*, not output. Rows still leave
  the adapter unmasked; a correct obligation check cannot stop a column
  that was never masked from reaching the facade. Needs a second,
  output-side mechanism anyway.

### B. Facade-level export filter

Mask (or drop) `pii` fields in the facade before handing results to
the host.

- **Pros**: single choke point for host-facing payloads; close to the
  consumer; easy policy plumbing (the facade already composes policy
  scope).
- **Cons**: too late and too leaky. The result object has already
  crossed internal boundaries; durable state, evaluation evidence, and
  any internal consumer see unmasked rows. Host-side exports bypass any
  non-facade path. Masking at the consumer edge contradicts the
  library's fail-closed style: exposure is prevented *before* data
  travels, not redacted *after*.

### C. Adapter post-processing contract (recommended)

Adapters post-process rows before constructing the result: any column
bound to a `pii=True` semantic field is masked (or dropped) inside the
adapter's execute step, driven by the binding plus a masking obligation
carried in the validated execution context.

- **Pros**: masking happens **before data leaves the process boundary**
  at the single choke point **all result rows cross**; every downstream
  consumer (facade, demo, evaluation, durable state, host exports)
  inherits masked data with no per-consumer logic; consistent with the
  adapter being the only component that already owns result-shaping
  rules (row bounds, scalar enforcement).
- **Cons**: needs `pii` field identities to reach the execution context
  (via field→column bindings, fingerprints-only, per the physical
  boundary rules); adapters must remain deterministic so result
  fingerprints stay stable; masking changes result values, so the
  result fingerprint must be computed **after** masking (masked output
  is the canonical result).

## Decision

Adopt **C (adapter post-processing contract)** as the enforcement point
for the follow-up change, with **A (result-level obligations)** retained
as the *declaration and audit* mechanism: obligations declare which
fields must be masked and are verified pre-execution; the adapter
post-processor is the *enforcement* mechanism that satisfies them.
**B is rejected**: consumer-edge redaction is a convenience, not a
boundary.

The follow-up change must specify:

1. How `pii` field identities travel fingerprints-only from descriptor
   to execution context (binding-derived, never raw descriptor content).
2. The masking operators (hash, drop, redact) and their determinism
   guarantees, so `result_fingerprint` and offline reruns stay stable.
3. That masking precedes fingerprinting: `ResultLineageEvidence`
   describes the *masked* result.
4. A fail-closed default: an unresolvable `pii` obligation fails the
   execution rather than passing rows through unmasked.

Until that change lands, `pii` remains schema + fingerprint only, and
this document is the ADR of record for the deferred masking behavior
(companion to ADR-030, which placed value semantics in the fingerprint
domain).
