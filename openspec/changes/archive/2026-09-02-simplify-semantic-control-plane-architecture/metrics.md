# Semantic Control Plane Architecture Metrics

Captured: 2026-08-31

## Before Metrics

The metrics below were captured before moving implementation code for the
`simplify-semantic-control-plane-architecture` change. Physical lines count
non-empty source lines. Declaration counts include classes, functions, and
async functions discovered through the Python AST.

| Area | Files | Physical lines | Declarations | Imports |
| --- | ---: | ---: | ---: | ---: |
| `src/nl2data_core/assembly` | 12 | 2171 | 145 | 84 |
| `src/nl2data_core/verification` | 9 | 2524 | 203 | 78 |
| `src/nl2data_core/assembly/publishing.py` | 1 | 420 | 16 | 16 |
| `packages/nl2data-admin-service/src/nl2data_admin_service/service.py` | 1 | 1499 | 65 | 27 |
| `packages/nl2data-semantic-catalog-postgres/src/nl2data_semantic_catalog_postgres/store.py` | 1 | 2561 | 57 | 29 |
| `packages/nl2data-semantic-catalog-postgres/src/nl2data_semantic_catalog_postgres/fake_postgres.py` | 1 | 1264 | 101 | 12 |
| Focused characterization and refactor tests | 6 | 2397 | 146 | 89 |

## Duplicate and Import Baseline

- Manifest-scanned Python files: 50
- Exact duplicate module groups: 1
- Current exact duplicate group:
  - `src/nl2data_core/verification/models.py`
  - `src/nl2data_core/verification/runner.py`

The architecture check harness initially recorded the known prohibited
edges and cycles as strict expected failures. All ratchets are green as
of the after metrics below: layer imports, prohibited imports, physical
line budgets, duplicate detection, cycles, canonical owners, and port
declarations pass without any waiver or expected-failure marker.

## Post-Refactor Target Budgets

These targets come from design decision D9 and are pinned in
`docs/architecture/semantic-control-plane-manifest.yaml`.

| Hotspot | Before | Target |
| --- | ---: | ---: |
| Admin facade | 1499 | <= 400 |
| Publication coordinator/facade | 420 | <= 250 |
| PostgreSQL store facade | 2561 | <= 600 |
| Admin capability service | not split | <= 500 |
| Publication gate module | not split | <= 300 |
| PostgreSQL repository | not split | <= 700 |
| Control-plane exact duplicate modules | 1 group | 0 |
| Newly introduced cross-domain imports | baseline pending | 0 |

## After Metrics

Captured: 2026-09-01, after task 8.8 and the phase-9 ratchet tightening.
Methodology matches the before snapshot: physical lines count non-empty
source lines; declarations and import-alias counts come from the Python
AST; the `assembly` area counts top-level modules only (the
`assembly/authoring` subpackage predates the change and was never
included).

| Area | Files | Physical lines | Declarations | Imports |
| --- | ---: | ---: | ---: | ---: |
| `src/nl2data_core/assembly` | 12 | 1833 | 130 | 197 |
| `src/nl2data_core/verification` | 9 | 2104 | 151 | 197 |
| `src/nl2data_core/assembly/publishing.py` | 1 | 82 | 1 | 21 |
| `packages/nl2data-admin-service/src/nl2data_admin_service/service.py` | 1 | 397 | 35 | 51 |
| `packages/nl2data-semantic-catalog-postgres/src/nl2data_semantic_catalog_postgres/store.py` | 1 | 589 | 35 | 43 |
| `.../fake_postgres` (package) | 11 | 1451 | 102 | 153 |
| Focused characterization and refactor tests | 6 | 3339 | 201 | 239 |

### Final budget conformance

| Hotspot | Before | After | Budget |
| --- | ---: | ---: | ---: |
| Admin facade (`service.py`) | 1499 | 397 | <= 400 |
| Admin capability services (largest: `bundle_admin.py`) | not split | 153–408 | <= 500 each |
| Publication coordinator (`coordinator.py`) | n/a | 73 | <= 250 |
| Publication compatibility facade (`publishing.py`) | 420 | 82 | <= 250 |
| Publication gate modules (`gates_freeze.py`, `gates_verify.py`) | 409 (single `gates.py`) | 174 / 263 | <= 300 each |
| PostgreSQL store facade (`store.py`) | 2561 | 589 | <= 600 |
| PostgreSQL repositories (5) | not split | 143–493 | <= 700 each |
| Control-plane exact duplicate modules | 1 group | 0 | 0 |
| Prohibited layer edges / import cycles | waived via xfail | 0 | 0 |
| Newly introduced cross-domain imports | — | 0 violations | 0 |

Additional observations:

- The focused test suite grew from 2397 to 3339 physical lines: the
  refactor added repository state/atomicity contracts and durability
  tests instead of weakening coverage. No behavioral test was deleted.
- `verification` shrank (2524 → 2104) after deleting the byte-identical
  `runner.py` duplicate and consolidating evaluator mechanics into
  `evaluation.py`.
- Admin capability boundaries are typed under Mypy with protocol
  conformance tests (tasks 4.2–4.4); the residual raw `Any`/`getattr`
  token counts in the adapter packages are non-boundary typing uses,
  not untyped dependency discovery.

## Reproduction

The metrics were generated from the repo root with the repository virtual
environment and the checked-in architecture manifest. The focused validation
for tasks 1.1 through 1.3 is:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\contract\test_semantic_control_plane_characterization.py tests\contract\test_semantic_control_plane_architecture.py
```

The after metrics were regenerated with the checked-in helper:

```powershell
.\.venv\Scripts\python.exe scripts\_after_metrics.py
```