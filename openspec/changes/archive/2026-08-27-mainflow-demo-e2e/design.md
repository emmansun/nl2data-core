## Context

The repository already verifies many isolated capabilities (facade lifecycle, workflow runtime conformance, PostgreSQL workflow store, Redis memory, metadata/catalog paths), but these proofs are distributed across docs and tests. Product consumers still need one canonical demo path to evaluate deployability and recovery behavior quickly.

The demo must remain transport-neutral, avoid new public API shapes, and rely on existing package boundaries introduced by recent refactoring.

## Goals / Non-Goals

**Goals:**
- Define one canonical demo flow from configuration through startup, execution, and durable persistence/recovery.
- Keep the flow reproducible in both local development and CI.
- Make acceptance evidence explicit (what must be true, what is optional, what is a skip).
- Reuse existing public surfaces (`NL2Data`, `CompositionProfile`, package exports) and existing service profiles.
- Ensure demo outputs are decision-relevant for end users, not only technically valid.

**Non-Goals:**
- Introduce a new transport host (HTTP/CLI framework) as part of this change.
- Add new backend implementations.
- Replace existing conformance/integration suites; this change composes them into a product-facing mainflow.

## Decisions

### 1) Canonical flow is a "single narrative" over existing contracts
- Decision: define a single user-facing narrative and acceptance criteria instead of adding another low-level protocol.
- Rationale: capability exists today; the gap is operational coherence.
- Alternatives considered:
  - Add a new runtime API dedicated to demo orchestration: rejected (unnecessary public-surface expansion).
  - Keep only scattered test evidence: rejected (insufficient product usability signal).

### 2) Dual-profile evidence model
- Decision: require two demo profiles:
  - deterministic profile (no external service required) for always-on baseline,
  - real-service profile (PostgreSQL + Redis) for durability confidence.
- Rationale: balances reliability and production realism.
- Alternatives considered:
  - Real-service only: rejected (too fragile for universal local onboarding).
  - Deterministic only: rejected (insufficient persistence/recovery confidence).

### 2.1) v1 source database is PostgreSQL
- Decision: select PostgreSQL as the v1 source database for the canonical real-service demo.
- Rationale: it best aligns with the persistence/recovery storyline and existing workflow-postgres durability contracts.
- Alternatives considered:
  - MongoDB as v1 source: deferred to v1.1 profile expansion so v1 remains focused on durability and recovery confidence.

### 3) Recovery semantics are first-class acceptance gates
- Decision: make resume/replay/cancel semantics mandatory demo checkpoints.
- Rationale: product readiness depends on failure handling, not only success path.
- Alternatives considered:
  - Success-only demo: rejected (does not validate operational safety).

### 4) Documentation is part of the deliverable, not an afterthought
- Decision: require an operator/developer runbook tied to explicit expected outputs.
- Rationale: "demo that only experts can run" is not product-ready.

### 5) Canonical demo documentation location
- Decision: place the canonical demo runbook in `docs/guides/mainflow-demo.md` with a mirrored Chinese translation in `docs/guides/mainflow-demo.zh-CN.md`.
- Rationale: this content is a guided execution and interpretation flow, which fits guides rather than getting-started or reference sections.
- Alternatives considered:
  - `docs/getting-started/`: rejected because the quickstart must stay minimal and backend-agnostic.
  - `docs/operations/`: rejected because this demo is for both developers and operators.

### 6) Root-level executable asset directory
- Decision: place executable demo assets under the repository root `demo/` directory.
- Scope under `demo/`:
  - `demo/schema/` for reference source schema DDL.
  - `demo/seed/` for seed generation and fixture loading assets.
  - `demo/questions/` for standard question definitions and SQL evidence set.
  - `demo/run/` for runnable demo entry scripts and profile launchers.
- Rationale: a root-level directory gives a single discoverable entry point for end users and avoids scattering executable assets across internal package directories.

## Reference Source Dataset (v1)

### Domain
Order fulfillment analytics with cross-cutting business signals (sales, payment, shipping, refund, and inventory pressure).

### Core tables (PostgreSQL)
1. customers
2. orders
3. order_items
4. products
5. payments
6. shipments

### Data characteristics
- Time horizon: 6 to 12 months of events.
- Scale targets:
  - orders: 50,000 to 200,000 rows
  - order_items: 2x to 6x of orders
  - payments: 1.0x to 1.3x of orders
  - shipments: 0.9x to 1.1x of orders
  - customers: 10,000 to 80,000 rows
  - products: 1,000 to 20,000 rows
- Multi-tenant: at least two tenant_id partitions with different data distributions.
- Include realistic anomalies:
  - cancelled orders
  - partial shipments
  - refunds
  - duplicate payment attempts
  - null or delayed operational fields

### Required fields for capability coverage
- Common keys: tenant_id, order_id, customer_id, product_id
- Time fields: created_at, paid_at, shipped_at, refunded_at
- Segmentation fields: region, channel, category
- Governance-sensitive fields: at least one restricted field (for example customer_email) to prove policy denial behavior

### Demo question suite (must be reproducible)
1. GMV top regions in the previous month.
2. Refund-rate outliers by product category.
3. Orders not shipped within 48 hours.
4. Paid but unshipped orders.
5. Weekly order growth (WoW).
6. New-customer first-order conversion by channel.
7. AOV quantiles (P50/P90).
8. Fast-growing products with low stock signal.
9. Tenant-scoped order and amount summary.
10. Clarification branch for ambiguous business term (for example revenue vs paid amount).

### End-user value rubric (acceptance lens)
For each standard demo question, the runbook MUST provide:
- why this question matters to a business/operator role,
- what decision it enables (for example expedite shipment, investigate refund spike),
- what action threshold is suggested (for example alert when refund rate exceeds a bound),
- what caveat applies (for example delayed shipment updates or partial refund lag).

This keeps the demo from becoming a pure technical showcase and ensures practical reference value.

### Question-to-value template (v1 baseline)
1. GMV top regions in the previous month
  - Role: regional sales manager
  - Decision: rebalance promotion budget across regions
  - Suggested threshold: investigate regions with >15% MoM drop
  - Caveat: late-order ingestion can shift month-end totals
2. Refund-rate outliers by product category
  - Role: product operations lead
  - Decision: trigger quality investigation for affected categories
  - Suggested threshold: alert when refund rate >2x category baseline
  - Caveat: refund posting lag may understate current-day ratio
3. Orders not shipped within 48 hours
  - Role: fulfillment operations manager
  - Decision: expedite stuck queues and staffing adjustments
  - Suggested threshold: alert when delayed ratio >8%
  - Caveat: carrier sync delays can temporarily overcount delays
4. Paid but unshipped orders
  - Role: customer support lead
  - Decision: proactive outreach before complaint spikes
  - Suggested threshold: escalate when backlog grows for 3 consecutive days
  - Caveat: partial shipments may appear as unshipped depending on rule
5. Weekly order growth (WoW)
  - Role: business analyst
  - Decision: validate campaign impact and demand forecast updates
  - Suggested threshold: investigate any +/-20% deviation from 8-week trend
  - Caveat: holiday effects can dominate normal weekly seasonality
6. New-customer first-order conversion by channel
  - Role: growth marketing manager
  - Decision: shift acquisition spend by channel efficiency
  - Suggested threshold: pause channels below 60% of median conversion
  - Caveat: attribution windows can reclassify conversions later
7. AOV quantiles (P50/P90)
  - Role: pricing strategy owner
  - Decision: adjust bundle/discount strategy by spend tier
  - Suggested threshold: review when P90/P50 ratio shifts >25%
  - Caveat: extreme enterprise orders can skew upper quantiles
8. Fast-growing products with low stock signal
  - Role: inventory planner
  - Decision: prioritize replenishment and allocation
  - Suggested threshold: restock when projected cover <10 days
  - Caveat: stock snapshots may be stale across warehouses
9. Tenant-scoped order and amount summary
  - Role: tenant account owner
  - Decision: verify isolated business health without cross-tenant bleed
  - Suggested threshold: investigate sudden zero-activity or abnormal spikes
  - Caveat: scope misconfiguration should fail closed and return no leakage
10. Clarification branch for ambiguous term (revenue vs paid amount)
  - Role: analytics consumer
  - Decision: select correct metric semantics before action
  - Suggested threshold: require explicit clarification whenever metric mapping confidence is low
  - Caveat: differing accounting definitions can produce intentional discrepancies

### SQL evidence set (for runbook and verification)
The implementation MUST provide one stable SQL evidence query per demo question, with deterministic result-shape assertions. The SQL set is for evidence and troubleshooting, not a bypass of the governed runtime.

## Risks / Trade-offs

- [Risk] Service-dependent profile can be flaky in contributor environments -> Mitigation: preserve deterministic baseline and classify unavailable services as explicit skip, never pass.
- [Risk] Demo drift from real runtime behavior over time -> Mitigation: bind demo acceptance to integration/conformance checks in CI.
- [Risk] Overly broad scope slows delivery -> Mitigation: constrain initial demo to one canonical request and bounded recovery scenarios.
- [Risk] Synthetic dataset looks unrealistic and fails to demonstrate value -> Mitigation: enforce domain-consistent ratios, anomalies, and tenant distribution checks.

## Migration Plan

1. Define the new `mainflow-demo` capability spec and acceptance scenarios.
2. Add/align demo runbook documentation with exact prerequisites, steps, and expected outcomes.
3. Wire demo verification to existing deterministic and real-service test profiles.
4. Add reference source schema + seed profile + SQL evidence pack aligned with the runbook.
5. Run OpenSpec validation and publish readiness checklist for implementation.

Rollback strategy: if real-service profile stability regresses, keep deterministic profile as required baseline and temporarily mark real-service demo as gated/known issue until fixed.

## Open Questions

- Should OpenAI live-provider evaluation be part of mainflow v1 or explicitly deferred to v2?
- Do we require separate Chinese/English runbook files for the demo in the initial implementation task set?
