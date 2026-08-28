# NL2Data Mainflow Demo

This directory contains the canonical, repeatable end-to-end demo for
nl2data-core. It proves the full product path from configuration through startup,
execution, and durable persistence/recovery.

## Profiles

- **Deterministic profile** (`run/demo_deterministic.py`) — runs anywhere with
  no external service dependencies. It uses the public `NL2Data` facade, a
  SQLite source fixture, a fake model provider, and a SQLite durable state
  store.
- **Real-service profile** (`run/demo_real_service.py`) — demonstrates
  production-like durability using PostgreSQL as the source database and
  workflow-state backend, plus Redis for Memory. This profile requires the
  optional packages and running services documented in the runbook.

## Directory layout

- `schema/` — PostgreSQL reference DDL for the order-fulfillment domain.
- `seed/` — Deterministic seed generator for the reference dataset.
- `questions/` — Standard 10-question demo suite and SQL evidence queries.
- `run/` — Runnable demo entry scripts and profile launchers.

## Acceptance contract (v1)

| Checkpoint | Deterministic | Real-service | Evidence |
| --- | --- | --- | --- |
| Configuration + startup | Required | Required | Public facade reaches `READY` |
| Query execution | Required | Required | `QueryOutcome.status == SUCCEEDED` |
| Durable persistence | SQLite state store | PostgreSQL state store | Workflow handle recovered after restart |
| Replay/resume semantics | Required | Required | Duplicate request returns `DUPLICATE_REQUEST`; adapter not re-executed |
| Cancellation fail-fast | Required | Required | Cancelled non-terminal workflow resumes as `WORKFLOW_CANCELLED` |
| Redis Memory participation | N/A | Required when memory enabled | Capability snapshot reports `memory=true` |

## Multi-Entity JOIN Scenarios

Both demo profiles now exercise the governed multi-entity JOIN path end-to-end,
using a static `LogicalJoinPlan` injected through the facade's `plan_compiler`
hook.

- **2-entity JOIN** — `orders` joined with `customers` on `customer_id`,
  returning the top EMEA orders with the customer name.
- **3-entity compound JOIN** — `orders` joined with `order_items` on `order_id`,
  then joined with `products` on `product_id`, returning the top EMEA order
  line items with product category and quantity.

### Expected columns and row counts

| Scenario | Columns | Row count |
| --- | --- | --- |
| 2-entity JOIN | `oid`, `customer_name` | 5 |
| 3-entity compound JOIN | `oid`, `category`, `quantity` | 5 |

Both scenarios assert `QueryOutcome.status == SUCCEEDED` and print the result
rows. The deterministic profile uses the shared SQLite fixture (now including
`products` and `order_items`); the real-service profile uses the PostgreSQL
reference schema.

### Custom compiler injection

The JOIN scenarios construct a custom `ir_compiler` that wraps `compile_sql`
with a `CompilationContext` carrying the multi-entity `PhysicalBinding`,
`AuthorizedView`, adapter capabilities, and the matching `LogicalJoinPlan`.
The `LogicalJoinPlan` flows through compilation as follows:

1. The compiler derives the set of joined entities from the plan's `JoinStep`
   entries.
2. Column references are qualified with their entity alias so the generated
   SQL references the correct table.
3. `JoinStep` entries are emitted as `JOIN ... ON ...` clauses in the order
   they appear in the plan.
4. The produced SQL artifact is then parsed, guarded, and executed by the
   adapter just like a single-entity query.

This proves the existing `_ir_compiler` injection point is sufficient for
multi-entity compilation without any core runtime changes.

## Running the demo

See [docs/guides/mainflow-demo.md](../docs/guides/mainflow-demo.md) for the
full runbook, including prerequisites, setup, run sequence, expected outputs,
and troubleshooting.

## Ownership

The demo assets are part of the `mainflow-demo-e2e` OpenSpec change. Update the
corresponding change artifacts under `openspec/changes/mainflow-demo-e2e/` when
modifying the demo contract.
