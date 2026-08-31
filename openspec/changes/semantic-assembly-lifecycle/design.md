# semantic-assembly-lifecycle Design

## Context

The current repository has three mature but separate pieces of lifecycle machinery:

- `SemanticProposalSet` models discovery-time candidate facts and immutable approve/reject/revise operations.
- `SemanticModelBundle` models an immutable runtime artifact and currently computes its fingerprint at construction time.
- `SemanticBundleCatalog` publishes immutable versions and changes the active pointer explicitly.

DDS-020 v1.1 fills the gap before publication: manual assembly, discovery output, review, approval, verification, publishing, audit, and deployment binding need one shared lifecycle. Because the library has no external production users, this change can intentionally break current internal fingerprint expectations and update tests, demos, and docs instead of preserving a weaker contract.

`calculated-field-semantics` completed and established the inherited bundle validation, fingerprint, projection-hash, and documentation contracts. Assembly implementation MUST preserve those contracts when it changes publication semantics.

## Goals / Non-Goals

**Goals:**

- Introduce an explicit assembly workspace for pre-publication artifacts: draft content, assertions, provenance, review state, deployment bindings, and optimistic revision.
- Make `SemanticAssertion` the common unit for review, audit, diff, approval, and incremental rediscovery.
- Assign semantic bundle fingerprints only during publish and only from canonical semantic payload.
- Exclude provenance, review state, deployment bindings, audit records, file-format metadata, and activation metadata from the semantic fingerprint domain.
- Make publish atomic and idempotent by semantic fingerprint, including immutable artifact persistence, audit persistence, and supersession metadata.
- Keep the admin service as a thin transport-neutral projection over core lifecycle rules.
- Update integration tests, demos, and documentation to the new lifecycle model without carrying user-facing migration compatibility.

**Non-Goals:**

- No end-user query execution through the admin service.
- No UI/TUI implementation in this change; API/service contracts and batch-safe primitives are sufficient.
- No new query planner semantics, adapter execution semantics, or LLM orchestration framework.
- No automatic promotion for inferred or LLM-suggested content based on confidence.
- No secret manager implementation; deployment bindings define reference forms and safety rules, while hosts provide resolution.

## Decisions

### D1 — Add an assembly workspace instead of overloading runtime bundles

`AssemblyDraft` is the editable control-plane artifact. It contains file `apiVersion`, bundle identity metadata, assertions, deployment bindings, draft lifecycle state, draft revision, and safe review metadata. It has no semantic bundle fingerprint.

`SemanticModelBundle` or its renamed published equivalent remains the immutable runtime artifact emitted by publish. Runtime callers, view resolution, IR planning, and evidence continue to consume published bundles, not drafts.

Alternative considered: add draft/review fields directly to `SemanticModelBundle`. Rejected because the current type already represents an immutable runtime artifact and computes fingerprint eagerly. Mixing mutable review state into it would blur the exact boundary DDS-020 is trying to enforce.

### D2 — Assertions are the control-plane review unit

Every reviewable fact becomes a `SemanticAssertion` with:

- deterministic `id` derived from identity semantics,
- `type` such as entity, field, relationship, mapping, policy, calculated field, measure, or grain,
- semantic `payload`,
- audit-side provenance,
- `review_state`,
- `review_binding` over the canonical assertion payload.

Proposal IDs remain discovery-local. Discovery adapts proposals into assertions so manual and discovered assembly converge before review.

Alternative considered: reuse `SemanticProposal` for all review. Rejected because proposal provenance answers "how this candidate was generated," while assertion review must also model manual edits, policy templates, deployment-independent publication, stable identity, and review invalidation.

### D3 — Fingerprint is publish-time semantic identity

Published bundle fingerprint is computed during publish as `sha256:` over canonical bytes of semantic payload only. The canonical domain excludes provenance, review state, reviewer identity, approval chain, rejected assertions, audit records, deployment bindings, activation state, supersession metadata, file `apiVersion`, comments, and YAML presentation.

This is intentionally breaking relative to the current bundle model, whose canonical payload includes `BundleProvenance` and whose fingerprint is computed at construction time. Existing golden fingerprints will be updated.

Alternative considered: keep current bundle fingerprints and add a second DDS-020 fingerprint. Rejected because two bundle identities would produce confusing evidence, cache, compatibility, and audit behavior.

### D4 — Review decisions bind to payload hashes

An approved or rejected assertion stores a `review_binding` calculated from the assertion payload at the moment of decision. Any payload change invalidates the binding and downgrades the assertion to pending. Identity changes are treated as delete/add; stable identity with changed payload is treated as modified.

This makes incremental rediscovery useful without allowing stale approvals to survive content edits.

### D5 — Draft writes and review actions use optimistic concurrency

Every draft carries a monotonic `draft_revision`. Edit, review, approve, and publish requests include the revision they observed. Revision mismatch returns a conflict and leaves state unchanged.

This supports future UI/TUI/batch review clients without long-lived locks.

### D6 — Publish is one atomic core operation

Publish performs freeze, verification, canonical fingerprint computation, duplicate-content check, immutable artifact persistence, audit record persistence, and supersession-chain update as one transaction at the catalog boundary. A failure leaves the draft approved and produces no externally visible partial publication.

Repeated publish of identical content is idempotent and returns the existing published artifact and audit reference. Different content for the same bundle name appends to the supersession chain.

### D7 — Deployment bindings are outside semantic identity

Deployment bindings describe runtime connectivity by environment using safe reference forms such as `env:`, `vault:`, or `file:`. They never enter the semantic fingerprint or audit logs as raw credentials. Publish may resolve bindings temporarily for verification, but resolved secrets are never persisted.

Physical field/table metadata needed to compile semantics remains distinct from runtime connection material. This preserves semantic portability across SQLite, PostgreSQL, MongoDB, and host environments.

### D8 — Admin service projects lifecycle operations, core owns rules

`nl2data-admin-service` exposes create/read draft, submit review, assertion decision, approve, publish, activate, rollback, versions, and audit lookup operations. It requires trusted host authorization and returns bounded safe DTOs. It never implements competing fingerprint, review invalidation, verification, publish atomicity, or active-pointer rules.

Role checks for Author, Reviewer, Approver, and Publisher are host-supplied policy inputs. Core validates lifecycle preconditions and records safe audit references.

### D9 — ADR numbering is resolved before implementation

The unified ADR registry assigns ADR-045 to `calculated-field-semantics`; DDS-020 v1.1 therefore reserves ADR-046 through ADR-052. Documentation must use those non-overlapping identifiers.

### D10 — Calculated fields remain reviewed semantic content

A calculated-field assertion carries the complete canonical definition: name, label, description, expression tree, output type, exact dependencies, and zero-division policy. Any definition edit invalidates its review binding. Publish emits approved definitions into the descriptor semantic payload, so they continue to affect descriptor, snapshot, and published-bundle identity. Projection assembly derives each safe `ResolvedCalculatedField.content_hash` from that published definition; expressions never enter prompt context or runtime evidence.

Publish-time verification preserves the inherited invariants: descriptor-global calculated-field names, base-field-only dependencies, pii isolation, adapter capability gating, and the expression bounds/type rules. A draft or publication cannot replace a reviewed definition with another definition under the same name and retain approval.

## Risks / Trade-offs

- **Fingerprint churn across tests and demos** -> Accept as intentional because no real users rely on current fingerprints; update golden expectations in one pass.
- **Scope creep into admin UI or service persistence** -> Keep UI out of scope and define transport-neutral service contracts first.
- **Proposal/assertion duplication feels redundant** -> Keep the distinction crisp: proposals are discovery candidates; assertions are lifecycle-governed semantic decisions.
- **Durable catalog work may grow large** -> Implement in-memory core semantics first, then durable catalog persistence after contracts and tests pin behavior.
- **Calculated-field publication may lose inherited validation** -> Treat complete calculated-field definitions as reviewed semantic assertions and rerun bundle validation at publish.
- **JCS/NFC canonicalization may require helper changes** -> Treat canonical helper tightening as part of the fingerprint-breaking implementation and cover with golden tests.

## Migration Plan

1. Preserve the completed `calculated-field-semantics` contracts in assertion and publish models.
2. Introduce assembly lifecycle models and pure validation helpers without changing runtime query behavior.
3. Add assertion identity, canonical payload, review binding, and draft revision tests.
4. Change published bundle fingerprint domain and update affected unit/contract/golden tests.
5. Refactor proposal conversion to produce assembly assertions/workspaces, then publish to immutable bundles.
6. Add publish audit and supersession semantics to in-memory catalog, then durable catalog packages.
7. Update admin service DTOs and service methods to expose lifecycle-safe operations.
8. Update demos and documentation to describe discovery/manual assembly -> review -> approval -> publish -> activate.

Rollback for implementation branches is source-control rollback only; this repository has no external published artifacts requiring data migration.

## Open Questions

- Should the runtime artifact keep the `SemanticModelBundle` name with changed semantics, or should published output be renamed to `PublishedSemanticBundle` with compatibility aliases inside the package?
- Should `model_version` remain user-supplied business metadata only, or should the publish API assign a monotonically increasing semantic version alongside the fingerprint?
- How much of `Verification Suite` Layer 2/3 belongs in core versus host-provided callbacks for adapter-backed execution?