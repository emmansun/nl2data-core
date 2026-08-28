## Why

The current demo scripts (`demo_deterministic.py` and `demo_real_service.py`) exercise only single-entity queries against the `orders` table. The SQL compiler already supports multi-entity JOIN compilation through `LogicalJoinPlan`/`JoinStep`, and the semantic layer already carries multi-entity views, relationship vocabulary, and `allowed_relationships` — but no demo proves the complete governed path end-to-end. Extending the demo to span multiple entities with JOIN compilation closes the gap between existing infrastructure capability and visible proof.

## What Changes

- Extend the shared controlled-SQL fixture schema (`fixtures/data.py`) to include `products` and `order_items` tables alongside the existing `customers` and `orders`, with deterministic seed rows and updated expected counts.
- Update `demo_deterministic.py` to construct a multi-entity `PhysicalBinding` (with `entity_bindings` and `entity_id`-qualified `ColumnBinding` entries), a multi-entity `AuthorizedView`, and a `LogicalJoinPlan` with at least one `JoinStep`, proving that a JOIN query compiles and executes through the full governed path.
- Update `demo_real_service.py` to demonstrate the same multi-entity JOIN scenario against the real PostgreSQL source using the existing 6-table `demo/schema/schema.sql`.
- Add at least one new JOIN-focused question/prompt and corresponding static intent/IR artifacts to each demo script, showing cross-entity selection (e.g., orders joined with customers or order_items joined with products).
- Update `demo/README.md` to document the new multi-entity JOIN demo scenarios and expected outcomes.

## Capabilities

### New Capabilities
- `demo-multi-entity-join`: End-to-end demo scripts proving multi-entity JOIN queries through the governed path — from multi-entity physical binding, authorized view, and logical join plan through IR compilation, guard validation, and adapter execution to protected result rows.

### Modified Capabilities
- `controlled-sql-fixtures`: Add `products` and `order_items` tables to the shared deterministic fixture schema and seed, with updated expected counts and new result assertions that exercise cross-table JOIN queries.
- `mainflow-demo`: Add mandatory multi-entity JOIN demonstration scenarios to the canonical mainflow demo contract, proving the governed path spans multiple entities with relationship-aware compilation.

## Impact

- **Fixture data** (`src/nl2data_core/fixtures/data.py`): schema, seed, expected counts, result assertions — additive; existing single-entity tests remain valid.
- **Demo scripts** (`demo/run/demo_deterministic.py`, `demo/run/demo_real_service.py`): multi-entity bindings, views, join plans, and new query scenarios added alongside existing single-entity scenarios.
- **Demo documentation** (`demo/README.md`): updated to cover multi-entity JOIN scenarios and expected outcomes.
- **Tests**: conformance and contract tests that depend on fixture counts or schema may need expected-count updates; no core behavior changes.
- **No breaking changes**: existing single-entity demo paths and test paths remain functional.
