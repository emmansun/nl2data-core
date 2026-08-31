# semantic-assembly-lifecycle Tasks

## 1. Preconditions and Design Reconciliation

- [x] 1.1 Verify `calculated-field-semantics` is complete or that its remaining tasks no longer modify bundle validation, fingerprint semantics, ADR numbering, or semantic-layer documentation.
- [x] 1.2 Create or update the unified ADR registry and assign non-conflicting ADR numbers: calculated fields use ADR-045; DDS-020 uses ADR-046 through ADR-052.
- [ ] 1.3 Record the final naming decision for the published runtime artifact: keep `SemanticModelBundle` with revised semantics or introduce `PublishedSemanticBundle` with compatibility aliases.
- [ ] 1.4 Decide the publish-time verification boundary: core-only structural checks versus host-provided smoke/semantic contract callbacks.

## 2. Assembly Lifecycle Core Models

- [ ] 2.1 Add lifecycle enums and bounded models for `AssemblyDraft`, `AssemblyState`, `SemanticAssertion`, `AssertionType`, `ReviewState`, `AssertionProvenance`, `ReviewBinding`, and `DeploymentBinding`.
- [ ] 2.2 Implement required file `apiVersion` validation, fail-closed unknown-version handling, and draft serialization helpers.
- [ ] 2.3 Implement deterministic assertion ID derivation from identity semantics for entity, field, relationship, mapping, policy, calculated-field, measure, and grain assertions; calculated-field identity is descriptor/entity/name while all other canonical definition members remain review-bound payload.
- [ ] 2.4 Implement canonical assertion payload hashing and review binding validation.
- [ ] 2.5 Implement optimistic `draft_revision` mutation helpers for edit, review, approval, and publish attempts.
- [ ] 2.6 Add unit tests for lifecycle state transitions, apiVersion rejection, assertion ID stability, identity-vs-payload diff behavior, review binding invalidation, and draft revision conflicts.

## 3. Canonical Fingerprint Domain

- [ ] 3.1 Tighten canonical serialization helpers as needed for DDS-020 canonical semantic bytes, including deterministic object ordering, assertion ordering, omit-when-unset behavior, and string normalization.
- [ ] 3.2 Refactor published bundle canonical payload so provenance, review state, reviewer identity, approval chain, rejected assertions, deployment bindings, audit records, activation state, supersession metadata, and file-format metadata are excluded from semantic fingerprints.
- [ ] 3.3 Move safe provenance and trust/audit references into non-semantic metadata or publish audit records while preserving safe serialization.
- [ ] 3.4 Update golden fingerprint expectations and add tests proving provenance, reviewer identity, deployment binding, YAML key order, comments, and formatting do not change semantic fingerprints.
- [ ] 3.5 Add tests proving semantic content changes, including every calculated-field canonical member, still change the published bundle fingerprint and projection content-hash anchor.

## 4. Discovery to Assertion Assembly

- [ ] 4.1 Add conversion from `SemanticProposalSet` records to `SemanticAssertion` records while preserving proposal provenance as audit-side metadata.
- [ ] 4.2 Adapt manual bundle-as-code input and discovery output into the same assembly draft structure.
- [ ] 4.3 Implement incremental rediscovery alignment by assertion ID against a baseline draft or published bundle.
- [ ] 4.4 Add negative-evidence handling for rejected assertions and optional replay of repeated rejected candidates.
- [ ] 4.5 Add tests for proposal adaptation, unreviewed proposal exclusion, stale snapshot rejection, incremental modified/add/delete/stale classification, and rejected-assertion replay.

## 5. Review, Approval, and Authorization

- [ ] 5.1 Implement assertion-level approve, reject, and edit operations with provenance transfer rules and bounded audit metadata.
- [ ] 5.2 Implement draft submit-for-review and approve operations, including pending assertion checks and content freeze semantics.
- [ ] 5.3 Add host-supplied lifecycle authorization hooks for Author, Reviewer, Approver, and Publisher actions.
- [ ] 5.4 Add configurable separation-of-duties policy modes and audited solo-mode waiver metadata.
- [ ] 5.5 Add tests for unauthorized lifecycle mutations, LLM-suggested explicit review requirements, edited suggestion provenance transfer, pending assertion publish blocks, and separation-of-duties policy outcomes.

## 6. Publish, Catalog, and Version Lifecycle

- [ ] 6.1 Implement publish as an atomic operation over freeze, inherited bundle/calculated-field verification, fingerprint computation, duplicate-content detection, immutable artifact persistence, audit persistence, and supersession update.
- [ ] 6.2 Change in-memory catalog publish identity from duplicate business-version conflict to semantic-fingerprint idempotency while retaining business version metadata.
- [ ] 6.3 Add published version states and supersession-chain metadata: active, superseded, deprecated, and retired.
- [ ] 6.4 Update activation and rollback to operate by published fingerprint and preserve immutable artifact history.
- [ ] 6.5 Add publish audit record models with bounded approval chain, assertion provenance summary, verification result summary, idempotency status, and deployment binding redaction summary.
- [ ] 6.6 Add contract tests for atomic publish rollback on failures, identical-content idempotency, supersession chain append, activation pointer changes, rollback by fingerprint, and audit/artifact same-transaction behavior.

## 7. Deployment Binding and Secret Safety

- [ ] 7.1 Add `DeploymentBinding` validation for allowed reference forms such as `env:`, `vault:`, and `file:` and reject likely inline credentials.
- [ ] 7.2 Ensure deployment bindings are excluded from semantic fingerprints and raw audit payloads.
- [ ] 7.3 Add verification-time secret resolution interfaces without persisting resolved credentials.
- [ ] 7.4 Add security tests for inline secret rejection, redacted audit summaries, admin DTO redaction, and unchanged fingerprints across deployment binding changes.

## 8. Admin Service Alignment

- [ ] 8.1 Update admin-service DTOs for assembly draft summaries/details, assertion decisions, review outcomes, approval outcomes, publish outcomes, audit references, version listings, and lifecycle errors.
- [ ] 8.2 Add service methods for draft creation, draft read, draft edit, submit-for-review, assertion approve/reject/edit, draft approval, publish, audit lookup, and version listing.
- [ ] 8.3 Update existing proposal review methods so approved proposals adapt into assembly assertions rather than directly implying published bundle authority.
- [ ] 8.4 Update publish, activate, and rollback service methods to delegate lifecycle validation, fingerprinting, idempotency, supersession, and audit generation to core/catalog.
- [ ] 8.5 Add admin-service contract and security tests for safe DTO bounds, missing role rejection, stale draft revision conflicts, pending assertion publish rejection, idempotent publish response, rollback response, and absence of secret leakage.

## 9. Durable Catalog Persistence

- [ ] 9.1 Extend PostgreSQL semantic catalog schema/envelopes for assembly drafts, assertion review state, publish audit records, supersession edges, lifecycle state, and safe deployment binding references.
- [ ] 9.2 Implement durable draft revision compare-and-swap for edit/review/approval/publish operations.
- [ ] 9.3 Implement durable atomic publish transaction with artifact, audit, and supersession writes committed together.
- [ ] 9.4 Implement durable idempotent publish lookup by semantic fingerprint and idempotency key where available.
- [ ] 9.5 Add integration tests for restart reload, stale revision conflict, concurrent publish/activation, failed publish rollback, supersession traversal, and secret-safe persistence.

## 10. Docs, Demo, and Quality Gates

- [ ] 10.1 Update English and Chinese metadata-to-bundle guides to describe manual/discovery assembly draft -> review -> approval -> publish -> activate.
- [ ] 10.2 Update semantic-layer, bundle, admin-service, operations/secrets, architecture/evidence, and error-code docs for lifecycle states, assertion review, publish audit, fingerprint domain, deployment binding safety, and version rollback.
- [ ] 10.3 Update demos and integration fixtures to use assembly drafts and publish-time fingerprints.
- [ ] 10.4 Run focused unit, contract, security, admin-service, durable catalog, and integration tests for touched areas.
- [ ] 10.5 Run full pytest, ruff, mypy, `scripts/check_docs.py`, and `openspec validate semantic-assembly-lifecycle --type change`.