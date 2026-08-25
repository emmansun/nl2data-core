## Context

The repository has adapter-specific metadata discovery for MongoDB that lists bounded collections and dotted field paths without exposing values. SQL and MongoDB do not yet share a discovery contract, and Semantic Model Bundles still assume that descriptors are prepared manually. DDS-019 needs a safe path from source metadata to reviewable semantic proposals without allowing inferred facts to become authorization or execution truth.

## Goals / Non-Goals

**Goals:**

- Define a backend-neutral immutable `MetadataSnapshot` with source/catalog identity, objects, fields, types, constraints, relationships, statistics, freshness, and provenance.
- Add a replaceable metadata discovery capability that adapters may implement independently of query execution.
- Provide bounded SQL and MongoDB discovery adapters, with MongoDB's existing path discovery normalized into the common snapshot.
- Generate semantic proposals with explicit `declared`, `observed`, and `inferred` trust levels, confidence, and evidence references.
- Add schema drift comparison and a review boundary that converts approved proposals to Bundle inputs.
- Preserve tenant/source authorization, safe serialization, and no-raw-value guarantees.

**Non-Goals:**

- Automatic publication or authorization of inferred semantics.
- Full semantic DSL parsing, vector retrieval, LLM-based inference, or data profiling over unrestricted values.
- Replacing QueryAdapter execution or exposing native database metadata objects.
- Remote metadata registry, distributed catalog, or workflow orchestration.
- Guaranteeing that sampled metadata represents the complete source schema.

## Decisions

### Optional metadata capability beside QueryAdapter

Add a separate provider-neutral `MetadataDiscoverer` protocol rather than expanding mandatory query execution methods. Adapters can advertise metadata discovery through an optional capability/profile, while simple adapters remain valid without discovery. This avoids making metadata availability a prerequisite for query execution.

### Safe snapshots, not raw samples

Snapshots contain object/field names only when authorized, normalized type names, bounded constraints, protected statistics, and opaque evidence fingerprints. Sampling may inspect bounded structure for dynamic sources, but raw values, documents, rows, credentials, and native objects never cross the discovery boundary.

### Three trust levels

`DECLARED` means supplied by an authoritative source or human; `OBSERVED` means directly observed metadata; `INFERRED` means an analysis suggestion. Inference records its method, confidence, and evidence but cannot grant View visibility, policy access, mandatory filters, or execution authorization.

### Deterministic inference first

The first inference engine uses deterministic rules: identifier/name patterns, native constraints, type metadata, bounded path observation, and explicit adapter facts. LLM assistance is deferred and, if added later, must produce the same proposal contract and remain untrusted until review.

### Human approval before Bundle publication

Discovery produces proposals, not active models. A reviewer or trusted publication process explicitly approves/rejects each proposal or a bounded proposal set. Bundle publication includes proposal provenance and trust markers; unresolved inferred facts remain excluded or marked non-authoritative.

### Snapshot fingerprints and drift

Every snapshot is canonicalized and fingerprinted. Comparison emits safe added/removed/changed object and field references, never values. A changed snapshot invalidates compatible bundle/view assumptions unless the host publishes a new compatible bundle.

## Risks / Trade-offs

- [Dynamic sources are incomplete] → Mark observations as observed/inferred, include freshness and sampling bounds, and require review before publication.
- [Names leak sensitive metadata] → Apply source allowlists, field redaction/classification rules, bounded identifiers, and safe error handling before snapshot creation.
- [False relationship/measure inference] → Require evidence/confidence and human approval; inference never grants authority.
- [Metadata discovery is expensive] → Bound objects, fields, samples, statistics, timeout, and concurrency; allow profiles to disable sampling.
- [Schema drift breaks plans] → Fingerprint snapshots and reject stale Bundle/View/IR references at validation boundaries.
- [Adapter-specific semantics diverge] → Keep common snapshot facts minimal and retain backend-specific extensions behind explicit capability names.

## Migration Plan

1. Add metadata models, discovery protocol, trust/provenance records, snapshot serialization, and drift comparison.
2. Adapt existing MongoDB discovery and add a bounded SQL discovery implementation.
3. Add deterministic proposal generation and review/approval conversion into Bundle inputs.
4. Integrate active snapshot fingerprints with Bundle validation and View resolution.
5. Add contract/security/conformance tests and document that discovery is optional and never authorization.
6. Roll back by retaining manual descriptor/Bundle construction and disabling discovery providers; no source data migration is required.

## Open Questions

- Whether metadata snapshots should be persisted locally or remain host-owned artifacts in v1.
- Which SQL catalog statistics are safe and useful without requiring elevated database privileges.
- Whether the first review workflow should approve individual facts or complete proposal batches.
