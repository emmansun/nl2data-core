## Context

The current demo scripts exercise only single-entity queries: a flat `PhysicalBinding` against the `orders` table, a single-entity `AuthorizedView`, and a `StaticPlanResolver` returning a single-entity IR. The SQL compiler (`adapters/sql/compile.py`) already supports `LogicalJoinPlan` with `JoinStep` — producing table-aliased, column-qualified JOIN SQL — and the semantic layer already carries multi-entity views (`root_entity_ids` is a set), `allowed_relationships`, and `RelationshipGraph`. The gap is that no demo wires these pieces together end-to-end.

Key infrastructure components already in place:
- `PhysicalBinding.entity_bindings` / `ColumnBinding.entity_id` for multi-entity physical mapping.
- `LogicalJoinPlan` / `JoinStep` with deterministic fingerprints.
- `compile_sql` (context-based compiler) accepts `join_plan` in `CompilationContext`.
- `QueryExecutionRunner._ir_compiler` is a pluggable `Callable[[SemanticQueryIR], str]` — a custom compiler that wraps `compile_sql` with a join plan can be injected without runtime changes.
- SQL guard `allowed_objects` already supports multiple tables.
- Governance `_facts_from_ir` derives `resource_ids` from `binding.object_id` (the root table) — joined tables don't enter governance facts, only the SQL guard scope.

The shared SQLite fixture (`fixtures/data.py`) currently has `customers` and `orders` only. The demo's PostgreSQL reference schema (`demo/schema/schema.sql`) already has all 6 tables.

## Goals / Non-Goals

**Goals:**
- Prove the complete governed multi-entity JOIN path end-to-end: multi-entity binding → multi-entity view → logical join plan → IR compilation → SQL guard → adapter execution → protected result.
- Extend the shared SQLite fixture to include `products` and `order_items` so the deterministic demo can exercise JOIN queries without external services.
- Add at least one JOIN scenario to each demo script (deterministic and real-service) alongside the existing single-entity scenario.
- Update demo documentation to describe the new scenarios and expected outcomes.

**Non-Goals:**
- Implementing or modifying the `JoinPlanner` — the demo uses a static, hand-crafted `LogicalJoinPlan` injected through the custom compiler, not the dynamic planner.
- Multi-entity intent resolution through the AI provider — the demo uses `StaticPlanResolver` / `FakeModelProvider` with pre-built IR.
- Adding new SQL compiler capabilities (CTEs, window functions, DISTINCT, expressions) — only the existing JOIN compilation is exercised.
- Changing the core `QueryExecutionRunner` to natively thread join plans — the demo uses the existing `_ir_compiler` injection point.

## Decisions

### Decision 1: Extend the shared fixture rather than creating a demo-specific one

**Choice**: Add `products` and `order_items` tables to `fixtures/data.py` alongside the existing `customers` and `orders`.

**Rationale**: The fixture is already shared across SQLite, PostgreSQL conformance, evaluation, and demo profiles. Adding tables here gives the evaluation suite and future tests access to multi-table JOIN data without duplicating schema. The existing `customers` and `orders` data remain unchanged, so all current tests pass.

**Alternatives considered**:
- *Create a separate `demo/fixtures/` module*: rejected because it duplicates the schema and diverges from the conformance suite.
- *Use only the PostgreSQL reference schema for JOIN demos*: rejected because the deterministic demo (SQLite, no services) would lose JOIN coverage.

### Decision 2: Inject the join plan through a custom `ir_compiler` in the demo

**Choice**: The demo constructs a `CompilationContext`-based compiler lambda that calls `compile_sql` with the `LogicalJoinPlan` and passes it as `ir_compiler` to `QueryExecutionRunner`.

**Rationale**: `QueryExecutionRunner.__init__` already accepts `ir_compiler: Callable[[SemanticQueryIR], str]` — no runtime changes needed. The join plan is static for the demo, so the lambda captures it at construction time. This keeps the core runtime unchanged and proves that the existing injection point supports multi-entity compilation.

**Alternatives considered**:
- *Extend `StaticPlanResolver` to carry a join plan*: rejected because it couples the plan resolver protocol to join planning, which is a separate concern.
- *Add `join_plan` parameter to `QueryExecutionRunner.__init__`*: rejected as unnecessary — the existing `_ir_compiler` injection is sufficient for demo purposes, and the full runtime (`runtime.py`) already threads join plans through the `CompilationContext`.

### Decision 3: Governance scope covers root entity; SQL guard covers all joined tables

**Choice**: `policy_scope.resource_ids` includes the root entity's physical table name (e.g., `frozenset({"orders"})`). The adapter's `allowed_objects` includes all joined tables (e.g., `frozenset({"orders", "customers"})`). The `allowed_columns` set includes fields from all entities.

**Rationale**: This follows the existing governance design — `_facts_from_ir` derives `resource_ids` from `binding.object_id` (the root table), while the SQL guard validates all tables in the compiled SQL. The separation is intentional: governance authorizes the *intent* (root entity), the guard validates the *artifact* (all referenced tables).

### Decision 4: Two JOIN scenarios — simple (2-entity) and compound (3-entity)

**Choice**: The demo includes:
1. A 2-entity JOIN: orders → customers (e.g., "top orders with customer name").
2. A 3-entity JOIN: orders → order_items → products (e.g., "order amounts by product category").

**Rationale**: The 2-entity case proves the basic JOIN compilation path. The 3-entity case proves multi-step `LogicalJoinPlan` with two `JoinStep` entries, exercising the compiler's entity-introduction ordering check. Both use the existing inner join type.

## Risks / Trade-offs

- **[Risk] Fixture fingerprint change**: Adding tables changes `FIXTURE_SETUP_FINGERPRINT`, which invalidates all previously provisioned fixture databases. → **Mitigation**: The fingerprint is recomputed deterministically from `SCHEMA` + `SEED`; existing CI and test provisioning creates fresh databases per run, so no stale caches exist.
- **[Risk] Expected-count changes break conformance tests**: Adding `products` and `order_items` requires new `TableCount` entries. → **Mitigation**: Update `EXPECTED_COUNTS` atomically with the schema/seed changes; conformance tests read from the shared spec, so one update covers all profiles.
- **[Trade-off] Demo uses static join plan, not dynamic planner**: The demo doesn't exercise the `JoinPlanner` protocol. → Accepted because the planner is a separate concern (`multi-entity-semantic-planning` spec) and the demo's goal is to prove the *compilation and execution* path.
- **[Risk] Custom `ir_compiler` bypasses the compilation-context evidence chain**: The lambda-based compiler uses the legacy `Callable[[SemanticQueryIR], str]` signature, so the runtime wraps it with generic evidence. → **Mitigation**: Acceptable for a demo; the full evidence chain is exercised by the governed runtime path, not the demo.
