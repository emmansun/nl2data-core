## Context

The semantic control plane now spans authoring, mutable assembly drafts, review/approval, three-layer verification, immutable publication, in-memory and PostgreSQL catalogs, and a transport-neutral Admin service. The domain behavior is intentionally rigorous, but implementation complexity has accumulated in several forms:

- `assembly.models` imports Bundle and Verification models while `bundles.catalog` imports mutable Assembly models and Verification evidence, creating a core dependency cycle.
- `verification/runner.py` is byte-identical to `verification/models.py`, while public exports use only `models.py`.
- `publish_assembly` coordinates authorization, revision/approval checks, Bundle emission, structural verification, external verification, evidence binding, audit construction, and persistence through a large parallel parameter list.
- Admin uses incomplete ports and `Any` helpers while one service class coordinates unrelated capabilities.
- PostgreSQL schema/SQL dispatch, drafts, publications, evidence, activation/history, cleanup, and error translation live in one large store module and matching fake driver.
- Smoke and semantic evaluators duplicate observation/scalar/status/deadline mechanics.
- Durable verification evidence reload currently compares immutable evidence to the current mutable draft row; reopening or editing a draft after publication can invalidate valid historical evidence.

The repository has no external production users, but public and persisted contracts are now broad enough that a big-bang rewrite would be riskier than staged extraction. This change is a behavior-preserving simplification with one explicit defect fix.

## Goals / Non-Goals

**Goals:**

- Fix historical verification evidence so it remains valid after the source draft evolves.
- Establish and enforce an acyclic semantic control-plane dependency graph.
- Introduce one immutable publication aggregate and frozen release binding at the catalog boundary.
- Reduce publication, Admin, and PostgreSQL change hotspots through focused typed components.
- Remove duplicate contract implementations and consolidate verification evaluator mechanics.
- Replace untyped dependency access with complete capability ports.
- Add enforceable architecture tests and complexity ratchets.
- Preserve every supported public/wire/fingerprint/error/tenant behavior.

**Non-Goals:**

- No new semantic feature, assertion type, verification operator, authoring construct, CLI, UI, or backend.
- No change to Bundle, assertion, plan, or evidence fingerprint algorithms.
- No redesign of runtime query execution, metadata discovery, or policy semantics.
- No conversion to async persistence or a general dependency-injection framework.
- No generic plugin pipeline for publication gates.
- No deletion or rewrite of immutable published artifacts.

## Decisions

### D1 — Publish through `control_plane.publication`

Create an internal `nl2data_core.control_plane.publication` package:

- `contracts.py`: `FrozenReleaseBinding`, `PublicationAggregate`, request/context/result value objects.
- `ports.py`: complete emitter, verifier/executor, publication repository/catalog, and clock interfaces.
- `gates.py`: fixed typed gate functions and bounded gate results.
- `coordinator.py`: the short ordered orchestration path.

`assembly.publishing` becomes a compatibility re-export/delegating facade during migration. `bundles.models`, `verification.models`, and assembly manifest/models remain domain inputs but do not import publication orchestration or repositories. Catalog implementations consume `PublicationAggregate`; they never receive `AssemblyDraft`.

Approved dependency direction:

```text
views + bundles.models + verification.models
                 ↓
          assembly lifecycle
                 ↓
      control_plane.publication
                 ↓
        catalog/repository ports
                 ↓
 Admin adapters / PostgreSQL implementation
```

Direct module imports are used inside core to avoid package `__init__` side effects closing an accidental cycle. Architecture tests inspect the AST import graph.

Alternative: leave publishing under `assembly`. Rejected because publication coordinates Assembly, Bundle, Verification, Audit, and Catalog equally; assigning it to Assembly caused reverse catalog dependencies.

### D2 — Freeze release identity independently of mutable drafts

`FrozenReleaseBinding` captures immutable publication-time facts:

- release-binding schema version,
- draft ID and approved revision,
- approved verification-plan fingerprint,
- tenant and source scope fingerprints,
- Bundle and manifest fingerprints,
- policy profile/version/fingerprint,
- runner identity/version,
- executor identity/capability fingerprint where applicable.

It contains no draft payload, assertions, review state, credentials, or observations. Its canonical payload has a fingerprint. `PublicationAggregate` includes this binding plus Bundle, accepted manifest, verification evidence, and audit, and validates all cross-links once before persistence.

PostgreSQL stores the binding in the versioned verification-evidence envelope and records its fingerprint in the audit verification summary. Historical evidence reload validates Bundle + manifest + evidence + audit + frozen binding. It does not read `assembly_drafts`.

Legacy evidence without a binding is classified `legacy_unverified` or decoded through an explicit schema-v1 compatibility path; the current draft is never used to invent a binding. Since the repository has no external production data, an optional deterministic test fixture backfill is sufficient; no runtime migration claims production validity.

Alternative: copy the complete draft into publication storage. Rejected because it duplicates mutable/control-plane payload and expands the sensitive persistence surface.

### D3 — Use a fixed gate sequence, not a plugin framework

Define five explicit stages:

1. `freeze`: authorization, revision, approval, plan binding, separation-of-duties preconditions.
2. `materialize`: emit Bundle, run core structural checks, derive manifest.
3. `verify`: run or validate bound Verification Suite evidence.
4. `assemble`: construct audit, frozen binding, and validated publication aggregate.
5. `persist`: one catalog call and bounded outcome mapping.

Each stage is a pure function or narrow service over immutable input/output models. The coordinator carries a single `PublicationRequest` and `PublicationContext`, replacing the parallel optional parameters. Stage composition is explicit Python, not configurable plugins; ordering is a security invariant.

Compatibility `publish_assembly(...)` builds the request/context and delegates. Existing callers and tests migrate gradually.

### D4 — One canonical contract module per concept

Delete the duplicate implementation in `verification/runner.py`. If any external/internal import is discovered, retain the file temporarily as a re-export-only compatibility shim with no definitions. Public verification exports continue to resolve to `verification.models`.

Create shared constrained annotations/helpers for identifier, fingerprint, bounded safe reference, and issue code only where current validation semantics are identical. Consolidation is incremental: a helper is adopted only with characterization tests proving accepted inputs, rejected inputs, error safety, and canonical serialization unchanged.

Add CI duplicate detection using normalized Python source hashes, excluding generated files through an explicit allowlist. Semantic-equivalence detection is limited to selected control-plane contract paths to avoid noisy heuristics.

### D5 — Complete capability ports before splitting services

Define a complete `LifecycleCatalogPort` (or smaller composed ports) covering every method Admin invokes:

- publication aggregate write,
- Bundle/fingerprint lookup,
- accepted manifest and verification evidence lookup,
- audit and publication record listing,
- activation/rollback/state transitions.

Define typed authoring, draft-store, authorizer, Bundle emitter, Verification executor/context factory, metadata catalog, discoverer, and job ports. Admin dependency helpers return these protocols and use direct optional checks; no `Any`, `getattr`, or unchecked method calls at capability boundaries.

Protocol conformance is checked by Mypy and focused runtime contract tests for structural implementations.

Alternative: keep one enormous protocol. Rejected because hosts often provide only subsets and optional capability absence should remain explicit.

### D6 — Split Admin behind the existing facade

Create internal capability services:

- `MetadataAdminService`
- `AuthoringAdminService`
- `AssemblyLifecycleAdminService`
- `VerificationPublicationAdminService`
- `PublishedBundleAdminService`

Shared `AdminRequestContext` centralizes trusted auth, tenant/source checks, audit reference, and normalized dependency access. Shared decorators/error mapping stay one layer. DTO projection helpers move beside the capability that owns them.

The public `AdminService` constructor and methods remain; it composes capability services and delegates. Generated service schema and every existing method/DTO golden test must remain unchanged.

Target ratchet after extraction: facade <= 400 physical lines; each capability service <= 500 physical lines. Exceptions require architecture-manifest revision, not arbitrary splitting.

### D7 — Split PostgreSQL persistence under one UnitOfWork

Refactor optional package internals into:

- `sql.py`: categorized immutable SQL templates.
- `unit_of_work.py`: transaction, timeout, cursor execution, envelope/error infrastructure.
- `repositories/drafts.py`
- `repositories/publications.py`
- `repositories/evidence.py`
- `repositories/activation.py`
- `store.py`: protocol-compatible facade and cross-repository operation coordinator.

Repositories accept an existing transaction/session. They never open or commit a transaction for atomic cross-domain operations. `publish`, activation, and rollback remain facade-level UnitOfWork operations. Fake PostgreSQL dispatch mirrors categorized templates but shares lock/key helpers and repository-focused handlers.

Target ratchet: `store.py` facade <= 600 physical lines; each repository <= 700; no repository imports Admin or mutable AssemblyDraft for publication. Exact SQL sequence tests are replaced where possible by state/transaction behavior tests; a small SQL-template contract retains query correctness.

### D8 — Consolidate verification evaluation primitives

Create `verification/evaluation.py` for:

- tagged scalar comparison,
- selected observation lookup,
- observation-to-verification status reduction,
- preflight candidate/query checks,
- per-case deadline context,
- execution cache invocation,
- cleanup issue merging,
- deterministic layer status aggregation.

Smoke and semantic modules supply only assertion dispatch functions and layer-specific issue codes. Shared behavior is covered once plus thin parity tests across both layers.

This extraction must preserve evidence fingerprints and all status precedence exactly. Golden tests pin current outputs before code moves.

### D9 — Architecture conformance is a ratchet

Add a checked-in architecture manifest containing:

- allowed module-layer edges and prohibited imports,
- canonical contract owners and compatibility re-exports,
- complete Admin port mappings,
- exact-duplicate allowlist,
- hotspot line budgets,
- cross-domain import budget.

A lightweight AST/test utility computes imports and physical lines (counted as non-empty source lines, matching the metrics methodology) without adding a production dependency. Initial budgets capture the post-refactor targets, not today's maxima:

- Admin facade <= 400 lines; capability service <= 500.
- publication coordinator <= 250 lines; each gate module <= 300.
- PostgreSQL store facade <= 600 lines; repository <= 700.
- no exact duplicate Python source in control-plane paths.
- no prohibited dependency cycles.

Line limits are guardrails, not quality proofs. CI also tests ports, behavior, and dependency edges; code cannot evade budgets through excluded/generated paths.

### D10 — Migrate in small compatibility-checked slices

Order matters:

1. Add historical evidence regression; introduce frozen binding and fix persistence first.
2. Remove exact duplicate verification module.
3. Introduce complete ports and architecture manifest.
4. Extract shared verification mechanics.
5. Introduce publication aggregate/gates while keeping compatibility facade.
6. Split Admin capability services.
7. Split PostgreSQL repositories and fake handlers.
8. Tighten budgets to post-refactor targets and remove temporary shims.

After every slice, run focused tests plus fingerprint/wire/schema golden checks. Never mix semantic changes with extraction. The change is complete only after no compatibility shim contains logic.

### D11 — Publication integrity is validated centrally, not per entry point

The review rounds showed one root cause producing adjacent findings:
Bundle, manifest, audit, evidence, binding, version, pointer, and history
records were stored and read in scattered ways, so each entry point
re-implemented (or missed) its own cross-record checks. Instead of adding
per-entry `if`s, integrity lives in one place:

1. `validate_publication_integrity(PublicationRecordSet)` in
   `control_plane.publication.contracts` is the single rule chain for one
   publication's persisted records (manifest, audit, evidence, and frozen
   binding cross-links, with stable issue codes).
2. Every publish, reuse, read (`publication_records`, `verification_evidence`,
   `active`), activate, rollback, and reload path validates through it:
   facades convert compatibility publish arguments via
   `build_publication_records` (the only allowed converter), and the
   PostgreSQL read side wraps persisted rows via
   `validated_publication_records`, where the published-version row's
   `audit_id` is an independent witness against deleted audit/evidence rows.
3. `validate_lifecycle_witness` unifies ID/version/fingerprint/state checks
   for pointer and history records; `witness_cause_type` maps witness codes
   to persisted-record cause types.  History rows are numbered by the
   activation that pushed them, so the rollback witness must sit exactly
   at the pointer's `activation_sequence` (`history_discontinuity`):
   a deleted newest row would otherwise let rollback silently skip the
   version it recorded.  Rollback consumes the top row and moves the
   restored version onto the freed sequence slot, keeping the invariant
   that history always ends at the activation sequence.  First-ever
   activations have no predecessor to record, so their pointer sits at
   sequence 0; an empty history beside a sequence >= 1 is therefore
   always a discontinuity, never a legitimate "no history" state.
4. Activation treats a missing pointer as "never activated"; an ACTIVE
   version row beside a missing pointer is corruption, so re-activation
   fails closed with `orphan_active_version` instead of minting a second
   ACTIVE lifecycle row, and `reload_active` sweeps for ACTIVE version
   rows without a pointer so the orphan survives no restart undetected.
5. A parameterized failure matrix deletes or tampers each artifact one at
   a time and asserts every entry point fails closed with its stable
   outcome (raised error, bounded rejection outcome, or reload issue),
   covering both the active publication and the rollback target.
6. Compatibility publish keyword arguments are accepted only at the
   outermost facades and converted to a validated record set immediately;
   repositories accept record sets/aggregates only.

Note: wrapping records in a `PublicationRecordSet` re-runs nested pydantic
validators, which recompute content-derived fingerprints in place. The
manifest cross-link is therefore checked before the audit chain so a
caller-supplied evidence with a stale recorded fingerprint is attributed
to its content mismatch, not to the audit.

## Risks / Trade-offs

- **Large refactor could hide behavior changes** → Characterization tests precede moves; migrate one boundary at a time and compare serialized/golden outputs.
- **Frozen binding adds persistence schema** → Use an additive envelope/schema migration and explicit legacy classification; no artifact rewrite.
- **More files can look more complex** → Boundaries reduce change amplification; enforce per-component budgets and keep facade/gate count fixed.
- **Protocol fragmentation burdens hosts** → Compose small runtime-checkable capability ports and preserve the existing dependency object shape during migration.
- **Line-count budgets are gameable** → Pair them with import DAG, duplicate detection, typed-port checks, and code-review ADR requirement.
- **Repository split can weaken atomicity** → One UnitOfWork owns all cross-repository transactions; failure-injection tests remain mandatory.
- **Compatibility shims become permanent** → Record removal tasks and forbid logic in shim modules.

## Migration Plan

1. Land regression tests and frozen release binding with additive PostgreSQL envelope/schema support.
2. Remove duplicated Verification contracts and establish canonical-owner checks.
3. Add complete typed ports and import-boundary manifest.
4. Consolidate evaluator utilities and publication gates.
5. Split Admin and PostgreSQL internals behind unchanged facades.
6. Run full tests, Ruff, Mypy, docs, package/import-boundary checks, and all OpenSpec validation.
7. Record before/after architecture metrics and enable reduced CI ratchets.

Rollback is performed per extraction slice. Additive frozen bindings remain readable; compatibility facades preserve old imports. No rollback step rewrites or deletes an immutable publication.

## Open Questions

- Whether shared control-plane contracts should live under `nl2data_core.control_plane` or a narrower `nl2data_core.publication` package; implementation should choose the smallest package that yields an acyclic graph.
- Whether physical-line budgets should be enforced repository-wide immediately or only on the focused control-plane manifest until other legacy modules are decomposed.
