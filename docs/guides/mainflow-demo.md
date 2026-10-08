# Mainflow Demo Runbook

> **[主流程演示 (简体中文)](mainflow-demo.zh-CN.md)**

> **Audience**: operators, platform engineers, and product evaluators who want to
> see the full NL2Data path from configuration to durable execution.
> **Goal**: run the canonical demo, interpret the results, and know whether a
> failure is a service-availability gap or a real regression.

## What this demo proves

This is the canonical end-to-end demo for nl2data-core. It exercises the full
public-facade path:

```text
configuration -> initialize -> execute -> durable persist/recover
```

Two profiles are provided:

- **Deterministic profile** — runs anywhere with no external services. It uses a
  SQLite source fixture, a deterministic fake provider, and a SQLite durable
  state store.
- **Real-service profile** — uses PostgreSQL for the source database and durable
  workflow state, and optionally Redis for shared Memory. This is the profile
  that proves production-like durability.

## Prerequisites

### Common

- Python 3.11, 3.12, or 3.13.
- `nl2data-core` installed in the active environment.

### Deterministic profile only

No additional services or packages.

### Real-service profile

- PostgreSQL 16+ with a database and user that can create schemas and tables.
- Optional: Redis 7+ for shared Memory.
- Optional backend packages:

```bash
pip install nl2data-core[postgres] nl2data-workflow-postgres nl2data-memory-redis[redis] nl2data-postgres
```

## Minimal package set

For the deterministic profile:

```bash
pip install nl2data-core
```

For the real-service profile:

```bash
pip install nl2data-core[postgres]
pip install -e packages/nl2data-postgres
pip install -e packages/nl2data-workflow-postgres
pip install -e "packages/nl2data-memory-redis[redis]"
```

## Setup

### 1. Clone or enter the repository

```bash
cd nl2data-core
```

### 2. (Real-service only) Set environment variables

```bash
export NL2DATA_POSTGRES_DSN="postgresql://nl2data_user@localhost:5432/nl2data_demo"
# If a password is required, set it separately or use a .pgpass file.
export NL2DATA_REDIS_URL="redis://localhost:6379/0"  # optional
```

### 3. (Real-service only) Load the reference schema and seed data

```bash
python demo/seed/seed.py --scale small
```

This creates the order-fulfillment tables in the default schema and populates
them with two tenant partitions and documented anomaly samples. The reference
schema stores money/price columns as `DOUBLE PRECISION` so adapter results stay
inside the protected scalar set; if your database was seeded before this change,
re-run the seed command.

## Run the demo

### Deterministic profile

```bash
python demo/run/demo_deterministic.py
```

Expected output includes:

```text
facade lifecycle: created
facade initialized; health: healthy
facade configured: True
facade durable_state: True
first outcome status: succeeded
result rows: 10
replay outcome status: rejected
workflow handle: True
cancelled outcome status: rejected
Deterministic mainflow demo passed.
```

The replay is a duplicate request, so it is rejected without re-executing the
adapter. This proves durable idempotency. The `cancelled outcome status` line
proves the cancellation fail-fast path: a cancelled non-terminal workflow is
rejected with `WORKFLOW_CANCELLED` before any adapter execution.

### Real-service profile

```bash
python demo/run/demo_real_service.py
```

Expected output includes:

```text
facade lifecycle: created
facade initialized; health: healthy
facade configured: True
facade durable_state: True
facade memory: True|False
first outcome status: succeeded
result rows: 10
replay outcome status: rejected
workflow handle: True
cancelled outcome status: rejected
Real-service mainflow demo passed.
```

`facade memory` is `True` only when `NL2DATA_REDIS_URL` is set and Redis is
reachable. The `cancelled outcome status` line proves the cancellation
fail-fast path: a cancelled non-terminal workflow is rejected with
`WORKFLOW_CANCELLED` before any adapter execution.

## Governed demo questions (q2/q5/q6/q8)

Both profiles also run four governed business questions end to end: q2 (refund
rate by category), q5 (weekly order growth), q6 (first-order conversion by
channel), and q8 (low stock products). Each question is a fixed,
application-composed case that flows through the normal runtime path — intent
resolution, the deterministic join planner (q2/q6), IR validation, the
context-aware compiler, the SQL guard, governance, authorization, and result
protection. No demo-only compiler injection is used, and multi-entity IR
provenance pins the runtime join-plan fingerprint, so a stale or divergent plan
fails compilation instead of executing.

Deterministic profile — exact golden rows from the shared controlled fixture:

| # | Golden assertion | Declared shape (columns) |
| --- | --- | --- |
| q2 | `q2 refund rate by category` | `category`, `refunded_orders`, `total_orders`, `refund_rate` |
| q5 | `q5 weekly order growth` | `week_start`, `order_count`, `prev_order_count` |
| q6 | `q6 first-order conversion by channel` | `channel`, `new_customers`, `converted_customers`, `conversion_pct` |
| q8 | `q8 low stock products` | `product_id`, `category`, `stock_quantity`, `unit_price` |

Semantic time boundaries: q5 buckets orders into Monday-based weeks
(`DATE(created_at, '-6 days', 'weekday 1')` on SQLite,
`TO_CHAR(DATE_TRUNC('week', created_at), 'YYYY-MM-DD')` on PostgreSQL), so     
`prev_order_count` is `NULL` for the first observed week. The fixture seeds    
orders across the weeks starting 2026-01-05, 2026-01-12, and 2026-01-19 with   
business statuses, customer channels, and stock levels that keep every golden  
row stable.

Bounded semantics: the governed q6 keeps the canonical first-order conversion
semantics - a "new" customer is one with at least one paid/shipped/refunded    
order - but without the canonical SQL's derived `first_order` CTE, whose       
`MIN(created_at)`/`MIN(order_id)` columns its outer query never reads. The     
governed q8 implements the low-stock signal only (`stock_quantity < 10`); the  
canonical q8 also computes `units_sold_7d`/`days_of_cover` over a seven-day    
window, which needs a derived aggregate stage with its own grouping that the   
bounded IR grammar does not model. Both golden assertions are pinned to these  
bounded semantics.

Real-service profile: the same fixed intents, IRs, and provider responses run
against the PostgreSQL reference schema; the demo seed binds the legacy text
`orders.status` column where the controlled fixture carries
`business_status`. Because the seed dataset is scale-random, outcomes assert
the declared result shape (golden columns) and success rather than exact golden
rows. A missing driver or unreachable PostgreSQL is reported as skipped — an
explicit skip, never a pass.

## Expected outputs per demo question

The standard 10-question suite is defined in `demo/questions/questions.yml`.
Each question has a stable SQL evidence query with deterministic result-shape
checks. The SQL is for evidence and troubleshooting; the governed runtime does
not expose raw SQL to operators.

| # | Question | SQL evidence file | Shape check |
| --- | --- | --- | --- |
| 1 | GMV top regions in the previous month | `demo/questions/questions.yml` | `region`, `gmv`, `order_count` |
| 2 | Refund-rate outliers by product category | `demo/questions/questions.yml` | `category`, `refunded_orders`, `total_orders`, `refund_rate_pct` |
| 3 | Orders not shipped within 48 hours | `demo/questions/questions.yml` | `order_id`, `created_at`, `paid_at`, `shipped_at`, `hours_to_ship` |
| 4 | Paid but unshipped orders | `demo/questions/questions.yml` | `order_id`, `amount`, `paid_at`, `shipment_status` |
| 5 | Weekly order growth (WoW) | `demo/questions/questions.yml` | `week_start`, `order_count`, `gmv`, `prev_order_count`, `wow_growth_pct` |
| 6 | New-customer first-order conversion by channel | `demo/questions/questions.yml` | `channel`, `new_customers`, `converted_customers`, `conversion_pct` |
| 7 | AOV quantiles (P50/P90) | `demo/questions/questions.yml` | `p50_aov`, `p90_aov`, `mean_aov`, `order_count` |
| 8 | Fast-growing products with low stock signal | `demo/questions/questions.yml` | `product_id`, `category`, `stock_quantity`, `unit_price`, `units_sold_7d`, `days_of_cover` |
| 9 | Tenant-scoped order and amount summary | `demo/questions/questions.yml` | `tenant_id`, `order_count`, `total_amount`, `avg_amount` |
| 10 | Clarification branch for ambiguous term | `demo/questions/questions.yml` | `metric`, `value` (2 rows) |

## Question-to-value matrix

| # | Role | Decision intent | Suggested action threshold | Caveat |
| --- | --- | --- | --- | --- |
| 1 | Regional sales manager | Rebalance promotion budget across regions | Investigate regions with >15% MoM drop | Late-order ingestion can shift month-end totals |
| 2 | Product operations lead | Trigger quality investigation for affected categories | Alert when refund rate >2x category baseline | Refund posting lag may understate current-day ratio |
| 3 | Fulfillment operations manager | Expedite stuck queues and staffing adjustments | Alert when delayed ratio >8% | Carrier sync delays can temporarily overcount delays |
| 4 | Customer support lead | Proactive outreach before complaint spikes | Escalate when backlog grows for 3 consecutive days | Partial shipments may appear as unshipped depending on rule |
| 5 | Business analyst | Validate campaign impact and demand forecast updates | Investigate any +/-20% deviation from 8-week trend | Holiday effects can dominate normal weekly seasonality |
| 6 | Growth marketing manager | Shift acquisition spend by channel efficiency | Pause channels below 60% of median conversion | Attribution windows can reclassify conversions later |
| 7 | Pricing strategy owner | Adjust bundle/discount strategy by spend tier | Review when P90/P50 ratio shifts >25% | Extreme enterprise orders can skew upper quantiles |
| 8 | Inventory planner | Prioritize replenishment and allocation | Restock when projected cover <10 days | Stock snapshots may be stale across warehouses |
| 9 | Tenant account owner | Verify isolated business health without cross-tenant bleed | Investigate sudden zero-activity or abnormal spikes | Scope misconfiguration should fail closed and return no leakage |
| 10 | Analytics consumer | Select correct metric semantics before action | Require explicit clarification when metric mapping confidence is low | Differing accounting definitions can produce intentional discrepancies |

## Failure interpretation

### Service unavailable versus verified failure

| Symptom | Interpretation | Action |
| --- | --- | --- |
| `NL2DATA_POSTGRES_DSN is not set` | Real-service profile not configured | Set the DSN or run the deterministic profile |
| `nl2data-postgres package is not installed` | Optional backend missing | Install the optional backend packages |
| `Failed to connect to PostgreSQL` | Real service unavailable | Start the service and retry; deterministic profile still passes |
| `Governed questions skipped: PostgreSQL unavailable` | Governed questions q2/q5/q6/q8 skipped on the real-service profile | Start the service and retry; deterministic profile still passes |
| `first outcome status: rejected` with non-duplicate code | Verified runtime failure | Check the error code and the durable state record |
| `replay outcome status: succeeded` | Duplicate request executed twice — regression | Investigate idempotency store and adapter guarding |

### Recovery troubleshooting pointers

1. **Replay/resume semantics** — a duplicate request must return `REJECTED` with
   `DUPLICATE_REQUEST` and the adapter must not re-execute. If it does, check
   the durable state store connectivity and the idempotency TTL.
2. **Cancellation fail-fast** — cancelling a non-terminal workflow and then
   resuming must produce `WORKFLOW_CANCELLED` before any adapter execution. If the
   adapter runs, the cancellation flag is not reaching the state store.
3. **Stale checkpoints** — resuming after a code or policy change should fail
   fast with `STALE_CHECKPOINT`. A successful resume after a change indicates a
   fingerprint regression.
4. **Redis Memory** — when Redis is unavailable, the real-service demo reports
   `facade memory: False` but should still complete successfully. A failure that
   only occurs when Redis is enabled points to the Memory provider.

## CI evidence

The demo profiles are wired into the test suite:

```bash
pytest tests/integration/test_mainflow_demo.py -q
pytest tests/integration/test_mainflow_demo_real.py -q
```

Real-service tests are skipped when the required services are absent. This is an
explicit skip, not a pass.

## Next steps

- [Composition and query lifecycle](composition-and-query-lifecycle.md) — how to
  build your own profile.
- [Production readiness](../reference/production-readiness.md) — what
  "production supported" means for this project.
- [Troubleshooting](../operations/troubleshooting.md) — deeper failure analysis.
