# ADR Registry

> **Reader**: anyone writing or reviewing an architecture decision.
> **Prerequisites**: none. This page is the **single numbering
> authority** for architecture decision records across both
> documentation lines — the semantic-enhancement line (v4.x slices) and
> the DDS-020 semantic-assembly-lifecycle line.

## Numbering policy

1. Every ADR — numbered or unnumbered — gets a row in the registry.
2. A new ADR takes the **lowest free number** in the unified list; no
   line may privately reserve ranges without a registry entry.
3. Where a decision document exists in a change archive (not yet under
   `docs/architecture/`), the registry row links the archive and marks
   the number **occupied**; the number cannot be reused even before the
   prose migrates.
4. Renumbering a reserved-but-unpublished range requires a note in this
   registry (see the DDS-020 resolution below).

## Unified registry

### Semantic-enhancement line (v4.x slices)

| Number | Decision | Status | Document |
| --- | --- | --- | --- |
| ADR-030 | Value semantics enter the fingerprint domain; pii masking does not affect fingerprints | Accepted (v4.1 `semantic-value-semantics`) | Recorded in the change archive design (`openspec/changes/archive/2026-08-28-semantic-value-semantics/design.md`) |
| — | Pii masking enforcement point (result-row serialization spike; companion to ADR-030) | Accepted spike deliverable (v4.1) | [ADR: pii masking enforcement point](adr-pii-masking-enforcement-point.md) |
| ADR-033 | Planner-identity versioning: strict one-sided rejection activates together with identity versioning | Accepted (v4.1 `semantic-value-semantics`) | Recorded in the change archive design (same file as ADR-030) |
| ADR-045 | Calculated-field operator whitelist; every rejection recorded with its alternative path; aggregate composition is a non-goal | Accepted (v4.2 `calculated-field-semantics`) | [ADR-045: calculated-field operator whitelist](adr-calculated-field-operator-whitelist.md) |

### DDS-020 semantic-assembly-lifecycle line

| Number | Decision | Status | Document |
| --- | --- | --- | --- |
| ADR-036 – ADR-044 | DDS-020 v1.1 semantic assembly lifecycle decisions (draft/review/approved/published, assertion identity, publish fingerprints, audit) | Occupied by DDS-020 v1.1; to be documented when `semantic-assembly-lifecycle` lands | Pending (`semantic-assembly-lifecycle` change) |
| ADR-046 – ADR-052 | DDS-020 v1.1 originally reserved ADR-045 – ADR-051; **renumbered by this registry** because ADR-045 was accepted first by `calculated-field-semantics` | Reserved; to be documented when `semantic-assembly-lifecycle` lands | Pending (`semantic-assembly-lifecycle` change, task 1.2) |

### Renumbering note (ADR-045 collision resolution)

`calculated-field-semantics` and DDS-020 v1.1 both claimed ADR-045.
`calculated-field-semantics` completed first and its documentation task
created this unified registry, so **ADR-045 belongs to the calculated
field operator whitelist**. The DDS-020 reserved range shifts from
045–051 to **046–052**; `semantic-assembly-lifecycle` renumbers its
decision list when it implements (its task 1.2 consumes this registry).
Code in either change never depended on the exact numbers.

## Next steps

- [Semantic layer](semantic-layer.md) — where calculated fields and
  value semantics live in the descriptor model.
- [ADR-045](adr-calculated-field-operator-whitelist.md) — the current
  whitelist boundary for calculated-field expressions.
