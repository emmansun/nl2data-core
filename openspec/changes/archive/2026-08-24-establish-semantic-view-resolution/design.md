## Context

The canonical Semantic Query IR now carries logical field references and provenance, but the repository does not yet resolve the authorized semantic surface visible to planning or model-provider context. Existing tenant context and policy scopes provide trusted scope and governance facts; they are not themselves a semantic projection. DDS-019 requires a Semantic View that combines those facts with a bounded semantic descriptor and produces a stable, reviewable result.

## Goals / Non-Goals

**Goals:**

- Define immutable, versioned Semantic View definitions and resolved projections.
- Resolve entity/field visibility using trusted tenant scope, principal scope, purpose, policy, adapter capabilities, model version, and feature flags.
- Keep physical bindings, credentials, hidden policy rules, and excluded metadata out of planner/provider context.
- Bind `SemanticQueryIR` to a resolved-view fingerprint and expose safe provenance for workflow, telemetry, and Memory revalidation.
- Preserve a controlled compatibility mode for existing unbound IR with no configured view registry.

**Non-Goals:**

- Implement the complete Semantic Model DSL or model bundle publisher.
- Replace authentication, tenant trust establishment, or the governance policy engine.
- Compile or execute physical SQL/MQL or introduce new adapters.
- Build vector retrieval, approved-example indexing, or a general knowledge store.
- Make Semantic View a public `nl2data` API in this change.

## Decisions

### View definitions consume bounded semantic descriptors

Use a small host-supplied descriptor containing semantic entity, field, relationship, operation, and result-shape metadata. This avoids coupling the view resolver to a future DSL while making visibility and capability checks testable. A later model-bundle change can publish descriptors into the same resolver contract.

### Resolution is a pure fail-closed projection

The resolver takes a view definition plus immutable trusted resolution context and returns either a resolved projection or a structured denial/unavailable result. It filters members before context assembly, then applies a second IR reference check so a caller cannot use a field merely because it was present in the original descriptor.

### Stable identity includes every security dimension

The resolved-view fingerprint covers view identity/version, model/catalog fingerprint, tenant scope fingerprint, principal authorization fingerprint, purpose, policy fingerprint, adapter capability fingerprint, and feature flags. Raw tenant IDs, principal claims, credentials, and hidden policy rules are never included in the serialized projection.

### View restrictions are constraints, not authority

A view can restrict fields, relationships, operations, aggregations, and result shapes. It cannot grant access beyond the trusted policy/tenant context, and it cannot bypass IR validation, artifact guards, authorization, or result protection. The resolved projection is context for planning, not an authorization substitute.

### Unbound IR compatibility mode is explicit

Existing unbound IR remains executable only when no view registry is configured. An explicitly requested view that is missing, stale, or unauthorized fails closed. New IR-producing paths must carry a view reference once a view registry is configured; there is no plan-model compatibility path.

## Risks / Trade-offs

- [Resolver duplicates policy logic] → Consume policy decisions and trusted fingerprints; keep the view as a constrained projection rather than a second authority.
- [Incomplete descriptors hide required semantics] → Reject unresolved references and record bounded missing-member issues; do not infer authorization from absence.
- [Fingerprint contains too little security context] → Include all listed identity, purpose, policy, catalog, capability, and feature dimensions.
- [Context leakage through errors] → Return opaque member IDs and safe reason codes, never hidden fields, physical names, or policy internals.
- [Legacy mode persists indefinitely] → Mark it compatibility-only and require view binding for new semantic planning features.

## Migration Plan

1. Add view models, descriptor models, resolution context, resolver, projection, and fingerprints without changing existing IR execution.
2. Add an IR view reference/provenance field and compatibility defaults; validate references when a resolved view is present.
3. Update AI context assembly and workflow evidence to use resolved projections and view fingerprints.
4. Add tenant, purpose, principal, capability, stale-view, and excluded-member conformance tests.
5. Migrate configured applications to explicit views; retain unscoped legacy mode until the next compatibility policy review.
6. Roll back by disabling view registry binding and retaining the current unbound-IR path; no physical data migration is required.

## Open Questions

- Whether principal authorization should be represented by a host-provided fingerprint only or by a dedicated internal authorization snapshot model.
- Whether model/catalog bundles require signatures in the first publication workflow.
- Which semantic relationship and metric constraints belong in this resolver versus the later Semantic Model DSL validator.
