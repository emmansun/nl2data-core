## 1. Fixture Extension — Schema, Seed, and Expected Counts

- [x] 1.1 Add `products` table DDL to `SCHEMA` in `src/nl2data_core/fixtures/data.py` (columns: `product_id INT, category TEXT, unit_price REAL`).
- [x] 1.2 Add `order_items` table DDL to `SCHEMA` in `src/nl2data_core/fixtures/data.py` (columns: `item_id INT, order_id INT, product_id INT, quantity INT, unit_price REAL`).
- [x] 1.3 Add deterministic `products` seed rows to `_build_seed()` (at least 4 products with varied categories).
- [x] 1.4 Add deterministic `order_items` seed rows to `_build_seed()` linking existing orders to products with quantity and unit_price.
- [x] 1.5 Add `TableCount` entries for `products` and `order_items` to `EXPECTED_COUNTS`.
- [x] 1.6 Add at least one cross-table JOIN `ResultAssertion` (e.g., orders JOIN customers on customer_id) to `RESULT_ASSERTIONS`.

## 2. Fixture Test Alignment

- [x] 2.1 Run existing conformance and contract tests to identify expected-count or fingerprint failures caused by the schema/seed changes.
- [x] 2.2 Update any hard-coded expected counts or setup fingerprints in conformance tests (`tests/conformance/`) to match the new fixture spec.
- [x] 2.3 Update any hard-coded expected counts or schema references in integration tests (`tests/integration/`) that depend on the fixture.
- [x] 2.4 Verify `FIXTURE_SCHEMA_VERSION` is bumped if required by the fixture versioning convention.

## 3. Deterministic Demo — Multi-Entity JOIN

- [x] 3.1 Define multi-entity constants in `demo/run/demo_deterministic.py`: `JOIN_FIELDS` (fields spanning orders + customers), `JOIN_BINDING` (PhysicalBinding with entity_bindings and entity_id-qualified ColumnBindings), and `JOIN_VIEW` (AuthorizedView with both root_entity_ids).
- [x] 3.2 Define the 2-entity `JOIN_IR` (SemanticQueryIR with fields from order and customer entities) and `JOIN_PLAN` (LogicalJoinPlan with one JoinStep: orders → customers on customer_id).
- [x] 3.3 Define the 3-entity constants: `COMPOUND_FIELDS`, `COMPOUND_BINDING`, `COMPOUND_VIEW`, `COMPOUND_IR` (fields from orders, order_items, products), and `COMPOUND_PLAN` (LogicalJoinPlan with two JoinSteps: orders → order_items → products).
- [x] 3.4 Build a custom `ir_compiler` function that accepts an IR, selects the appropriate join plan (by IR id or root entity), and calls `compile_sql` with a `CompilationContext` carrying the binding and join plan.
- [x] 3.5 Add a `run_join_demo()` async function to `demo_deterministic.py` that constructs a `QueryExecutionRunner` (or facade profile) with the multi-entity binding, view, custom compiler, and executes the 2-entity JOIN query; assert `SUCCEEDED` and print result rows.
- [x] 3.6 Add the 3-entity JOIN scenario to `run_join_demo()` with the compound binding and plan; assert `SUCCEEDED` and print result rows.
- [x] 3.7 Wire `run_join_demo()` into `run_demo()` so it executes after the existing single-entity scenario and before the durable-recovery checkpoints.

## 4. Real-Service Demo — Multi-Entity JOIN

- [x] 4.1 Define multi-entity constants in `demo/run/demo_real_service.py`: `JOIN_FIELDS`, `JOIN_BINDING` (with PostgresQueryAdapter entity bindings), `JOIN_VIEW`, `JOIN_IR`, and `JOIN_PLAN`.
- [x] 4.2 Build a custom `ir_compiler` for the real-service demo that wraps `compile_sql` with the PostgreSQL-specific binding and join plan.
- [x] 4.3 Add a `run_join_demo()` async function to `demo_real_service.py` that constructs the facade with multi-entity components and executes the JOIN query; assert `SUCCEEDED` and print result rows.
- [x] 4.4 Wire `run_join_demo()` into `run_demo()` so it executes after the existing single-entity scenario.

## 5. Demo Documentation

- [x] 5.1 Update `demo/README.md` with a "Multi-Entity JOIN Scenarios" section describing the 2-entity and 3-entity JOIN demos, expected columns, and row-count ranges.
- [x] 5.2 Document the custom compiler injection approach and how the `LogicalJoinPlan` flows through compilation.

## 6. Integration Tests

- [x] 6.1 Add multi-entity JOIN test cases to `tests/integration/test_mainflow_demo.py` that exercise the deterministic JOIN scenarios through the facade.
- [x] 6.2 Add multi-entity JOIN test cases to `tests/integration/test_mainflow_demo_real.py` that exercise the real-service JOIN scenarios (with skip-not-pass when PostgreSQL is unavailable).

## 7. Quality Gates

- [x] 7.1 Run `pytest` — all tests pass including new JOIN test cases.
- [x] 7.2 Run `ruff check` — demo and fixture files are lint-clean.
- [x] 7.3 Run `mypy` — zero type-check issues.
- [x] 7.4 Run `openspec validate --specs` — all specs validate.
