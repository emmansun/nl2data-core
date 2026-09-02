# Assembly audit-evidence reference

> **Reader**: operators, release engineers, and auditors. **Prerequisites**:
> [Evidence and fingerprints](../architecture/evidence-and-fingerprints.md),
> [Semantic assembly lint](semantic-assembly-lint.md),
> [Verification Suite operations](../operations/verification-suite.md).

## What the audit-evidence trail is

Every governed semantic assembly lifecycle action records one bounded,
immutable **audit-evidence entry**: an append-only summary of a completed
action that explains *who did what to which subject, when, with what
outcome*, using fingerprints and safe references only. Entries never carry
raw prompts, SQL/MQL, physical names, credentials, resolved deployment
values, unrestricted sample values, native objects, or raw operator
identities — an opaque host audit reference stands in for the operator.

The trail is a part of explainability, not a second lifecycle state
machine: entries summarize actions that already happened and can never
approve, publish, activate, roll back, or otherwise change state. Inspecting
the trail has no side effects.

Entries live **outside the semantic Bundle fingerprint domain**. Recording,
editing, or pruning audit evidence never changes a Bundle fingerprint or
any published content.

## Event kinds and subjects

| Event kind | Subject | Recorded when | Key bound facts |
| --- | --- | --- | --- |
| `authoring_import` | draft | A draft revision is submitted for review (closes the authoring/import phase) | draft id, resulting revision, operator reference |
| `draft_approval` | draft | A draft revision is approved | draft id, resulting revision, operator reference |
| `assertion_review` | assertion | An assertion is rejected | assertion id, reviewed/resulting payload hashes |
| `assertion_edit` | assertion | An assertion is edited | assertion id, reviewed/resulting payload hashes |
| `assertion_approval` | assertion | An assertion is approved | assertion id, reviewed/resulting payload hashes |
| `lint_reference` | draft | A lint result reference is linked to a draft revision | lint run reference |
| `verification_reference` | draft | Verification Suite evidence is linked to a draft revision | evidence fingerprint, policy profile/version, plan fingerprint |
| `publication` | publication | A Bundle version is published | publish audit reference, bundle fingerprint, manifest/evidence/plan fingerprints |
| `activation` | activation | A published version becomes active | prior/resulting active fingerprints, publish audit reference |
| `rollback` | rollback | The active pointer is restored to a prior version | prior/resulting active fingerprints, publish audit reference |

Each entry records its `subject_kind` and `subject_reference`, an outcome
(`succeeded`, `rejected`, `failed`), an optional bounded status code and
reason, and up to eight `predecessor_event_ids` linking an entry to the
events it follows (for example, an activation entry links the publication
entry of the version it activated).

## Entry identity and tamper evidence

Every entry carries a SHA-256 fingerprint over its **canonical payload**.
The fingerprint deliberately excludes `occurred_at` (presentation
metadata), so identical facts recorded by workers with clock skew share one
identity, while any later mutation of a fact breaks the fingerprint
witness. Both catalog implementations (in-memory and PostgreSQL) re-verify
the fingerprint on recording and on every read; a tampered entry fails
closed instead of being returned.

## Deterministic, bounded trails

Trails are ordered by `(occurred_at, event_id)` and every query returns at
most 200 entries (`MAX_TRAIL_ENTRIES`). Paging uses the last returned
`event_id` as the cursor; an unknown or pruned cursor restarts the page
from the beginning rather than failing. `total_count` and `has_more`
accompany every page.

## Publication audit evidence

Publication adds an immutable binding — `PublicationAuditEvidence` — that
explains why one Bundle became authoritative. It links the approved draft
id/revision, verification plan fingerprint, accepted-assertion manifest
fingerprint, Verification Suite evidence fingerprint, policy
profile/version/fingerprint, tenant/source scope fingerprints,
separation-of-duties result, publish audit reference, and the Bundle
fingerprint. The binding is validated against the whole publication
aggregate before catalog persistence, and re-validated against the same
immutable identities on every durable read (reuse, activation, rollback,
reload). Any disagreement is rejected with the stable issue code
`publication_audit_evidence_mismatch`.

### Compatibility classification

Record sets are classified explicitly, never fabricated:

- `complete` — the publication carries its audit-evidence binding.
- `legacy` — a pre-audit-evidence publication with verification evidence,
  frozen release binding, and publish audit; readable as history, but not
  production-valid release evidence.
- `incomplete` — partial legacy records.

Missing evidence is never reconstructed from mutable drafts.

## Persistence and retention

Both catalogs persist audit evidence as versioned, fingerprinted,
tenant/source-scoped envelopes:

- Recording is idempotent for identical entries; cross-scope entries,
  tampered entries, and conflicting reuse of an event id are rejected.
- PostgreSQL stores entries atomically with the publication, activation,
  and rollback records they explain, and revalidates the full envelope
  (schema version, fingerprint, scope, publication cross-links) on read.
- Retention keeps every audit-evidence entry whose Bundle fingerprint
  belongs to a non-retired publication **or** that is referenced as a
  predecessor by a protected entry, so activation and rollback chains stay
  explainable; entries tied only to retired, unreferenced versions may be
  pruned by explicit cleanup.

## Admin inspection

The Admin service exposes read-only inspection under the
`assembly audit inspect` capability (`assembly:audit` permission):

- Lookups by draft ID (+ optional revision range), assertion ID, Bundle
  fingerprint, publication/activation/rollback lifecycle reference, or
  predecessor event, with cursor/limit paging.
- Results are tenant/source scoped (a caller never sees another tenant's
  or an unauthorized source's entries), ordered, bounded, and redacted:
  inspection views drop tenant/source scope fingerprints and truncate free
  text, exposing only identities, fingerprints, statuses, counts, and
  opaque operator audit references.
- Inspection is side-effect free: no review decisions, approvals, lint
  results, verification evidence, publications, activations, rollbacks, or
  retention changes are created.

## Relationship to other evidence

- **Lint**: a lint readiness reference may be linked into the trail and
  publication evidence, but lint is never a publication authority.
- **Verification Suite**: the trail links suite evidence by fingerprint;
  the suite itself remains the pass/fail authority.
- **Publish audit**: the publish audit reference is the anchor every
  publication, activation, and rollback entry shares through its
  lifecycle reference.
- **Activation/rollback**: pointer changes record prior and resulting
  active fingerprints and link their publication entry as predecessor,
  without republishing semantic content.
