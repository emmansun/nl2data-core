# Package Boundaries

> **Reader**: integrators and contributors. **Prerequisites**:
> [Architecture overview](overview.md).

## Two distributions, two layers

The repository ships **two distributions**:

| Distribution | Public import | Purpose |
| --- | --- | --- |
| `nl2data-core` | `nl2data` (public), `nl2data_core` (internal, contributor-only) | The governed library |
| `nl2data-openai` | `nl2data_openai` | Optional OpenAI structured-output provider |

Applications import **only** `nl2data`. `nl2data_core` is internal: it
exists so optional providers can be plugged in without leaking into the
public surface. Direct application imports from `nl2data_core` are
deprecated; migrate through the facade.

## Component / package boundary diagram

```mermaid
flowchart TD
    subgraph APP["Application (imports only nl2data)"]
        APP1["CompositionProfile + facade calls"]
    end

    subgraph PUBLIC["nl2data (public API)"]
        FAC["NL2Data / create_facade<br/>FacadePort"]
        MOD["Models: QueryRequest, QueryOutcome,<br/>WorkflowHandle, CancellationResult,<br/>FacadeCapabilities, ErrorRecord"]
        COMP["CompositionProfile<br/>ports: WorkflowRuntimePort, ModelProviderPort,<br/>QueryAdapterPort, MemoryProviderPort"]
    end

    subgraph CORE["nl2data_core (internal)"]
        CFG["config"]
        EN["engine ports"]
        WF["workflow<br/>runtime + state stores"]
        AI["ai<br/>protocol, resolver, instructions, evaluation"]
        AD["adapters<br/>SQL"]
        MEM["memory"]
        META["metadata"]
        VIEW["views"]
        BUND["bundles"]
        GOV["governance"]
        TEN["tenancy"]
        TEL["telemetry"]
        PLUG["plugins"]
    end

    subgraph OPT["Optional packages (lazy)"]
        OAI["nl2data_openai<br/>OpenAIProviderConfig, OpenAIModelProvider"]
        DRV["Drivers: psycopg, pymongo, redis, sqlglot"]
    end

    APP --> FAC
    FAC --> COMP
    FAC --> MOD
    COMP -. "composition boundary" .-> WF
    COMP -. "structural ports" .-> AI
    COMP -. "structural ports" .-> AD
    COMP -. "structural ports" .-> MEM
    WF --> AI
    WF --> AD
    WF --> MEM
    WF --> META
    WF --> VIEW
    WF --> BUND
    WF --> GOV
    WF --> TEN
    WF --> TEL
    AD --> DRV
    AI --> OAI
    MEM --> DRV
    WF --> DRV

    classDef pub fill:#e8f4e8,stroke:#2e7d32
    classDef opt fill:#f3e8f4,stroke:#7b1fa2
    class FAC,MOD,COMP pub
    class OAI,DRV opt
```

**Reader question**: which packages may import which, and where do
optional dependencies load?

**Text equivalent**: the application talks to the public facade, which
exposes public models and accepts a typed composition profile. The
facade composes the internal runtime across the composition boundary;
structural ports (`WorkflowRuntimePort`, `ModelProviderPort`,
`QueryAdapterPort`, `MemoryProviderPort`) let internal implementations
satisfy the public shape without the application importing `nl2data_core`
or any backend type. Internal packages (workflow, ai, adapters, memory,
metadata, views, bundles, governance, tenancy, telemetry, plugins)
depend on each other through narrow contracts, and only they may import
optional drivers or the sibling OpenAI package — lazily, at client build
time.

## Import rules

| Rule | Enforced by |
| --- | --- |
| `nl2data` never imports optional backends (database drivers, LLM SDKs, HTTP frameworks, telemetry backends) | AST-based import-boundary tests + `sys.modules` checks |
| Applications import only `nl2data` public symbols | `tests/contract/test_public_imports.py` conformance |
| Documentation examples follow the same public boundary | `scripts/check_docs.py` smoke checks |
| Optional packages import their SDK lazily — never at package import | `tests/security/test_import_boundary.py` |
| No driver/SDK type enters the public API or framework-neutral contracts | import-boundary suite |

## Semantic control-plane layers

The authoring → verification → publication → catalog path is organized
into acyclic layers pinned in
[`semantic-control-plane-manifest.yaml`](semantic-control-plane-manifest.yaml)
and enforced by
`tests/contract/test_semantic_control_plane_architecture.py`:

```text
shared_contracts (canonical, views, bundles.models, bundles.publication,
                  verification.models, verification.policy)
         ↓
assembly_lifecycle (assembly models, manifest, store, lifecycle, …)
         ↓
verification_execution (structural, semantic, smoke, suite, …)
         ↓
publication_orchestration (control_plane.publication.*,
                           assembly.publishing compatibility facade)
         ↓
catalog_ports (bundles.catalog, control_plane.ports)
         ↓
admin_adapter  /  semantic_catalog_postgres   (optional adapters)
```

Edges may only point downward. Two adapter-side exceptions keep the
direction inward: the PostgreSQL catalog implements the durable
`draft_lifecycle_store` port (so it imports `assembly_lifecycle`) and
runs production verification-evidence policy checks (so it imports
`verification_execution`). The publication path never touches mutable
draft state: catalog implementations receive the immutable
`PublicationAggregate` — `FrozenReleaseBinding` + Bundle + accepted
manifest + verification evidence + audit, cross-validated once before
persistence — and never an `AssemblyDraft`.

### UnitOfWork and repository ownership (PostgreSQL catalog)

`nl2data_semantic_catalog_postgres` splits persistence behind its
unchanged `PostgreSQLSemanticCatalog` facade:

| Module | Owns |
| --- | --- |
| `sql.py` | Categorized immutable SQL templates |
| `unit_of_work.py` | Transaction, timeout, cursor execution, envelope/error infrastructure |
| `repositories/snapshots.py`, `drafts.py`, `evidence.py`, `publications.py`, `activation.py` | Single-domain reads/writes over a passed connection or their own transaction |
| `maintenance.py` | Cleanup and active-pointer reload functions |
| `store.py` | Protocol-compatible facade; owns cross-repository atomic operations (`publish`, `activate`, `rollback`) |

Repositories never open or commit transactions for atomic cross-domain
operations — the facade-level `CatalogUnitOfWork` owns those. Line
budgets are pinned in the manifest hotspot budgets (store facade
<= 600, each repository <= 700).

### Compatibility facades

| Facade | Canonical target | Logic allowed |
| --- | --- | --- |
| `nl2data_core.assembly.publishing` | `nl2data_core.control_plane.publication.coordinator` | Delegating only |
| `nl2data_core.verification` package re-exports | `verification.models`, `verification.policy` | None |
| `nl2data` public facade | `nl2data_core` internals via composition | Delegating only |

A compatibility facade must not accumulate logic; the architecture
manifest's `compatibility_reexports` section records the canonical
owner of every re-exported symbol.

## Optional dependency loading

Constructing a facade imports no optional modules. The loading points
are explicit:

| Module | Loaded when |
| --- | --- |
| Model provider (e.g. `nl2data_openai`) | First `generate()` call — never at import, construction, or capability inspection |
| Database drivers (psycopg, pymongo, redis, sqlglot) | First real-service client build |
| Workflow runtime composition | `initialize()` — the earliest point optional modules load |

This is why the base library works with **no** extras installed: the
public API, models, configuration, and the deterministic runtime import
only `pydantic` and `PyYAML`. The `not-configured` fallback and the
deterministic fake provider (`FakeModelProvider`) need no credentials and
no network access.

## Configuration and telemetry are internal

- Configuration loads through the public `load_config`; the typed
  configuration model remains internal until a public configuration API
  ships.
- Telemetry/audit ports are internal contracts with in-memory sinks;
  application code composes a `TelemetryPort` if it wants to observe
  audit events — internal details stay bounded and redacted.

## Future hosting boundary

A later `nl2data_http` package (out of scope) may host the library
behind HTTP. The boundary is already fixed: it will program against the
transport-neutral `FacadePort` and the public models without importing
internal types. The core adds no HTTP dependency and never loads
`fastapi`, `flask`, or `starlette`.

## Next steps

- [Governance and tenancy](governance-and-tenancy.md) — trust boundaries
  inside the core.
- [Adding an adapter or provider](../development/adding-adapter-or-provider.md)
  — how new backends plug in without bypassing these boundaries.
