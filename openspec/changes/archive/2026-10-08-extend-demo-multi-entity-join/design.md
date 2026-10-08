## Context

This change resumes an archived demo JOIN change, but its scope is materially different. The current tree already has `LogicalJoinPlan`, `CompilationContext.join_plan`, `PhysicalBinding.entity_bindings`, runtime propagation, partial JOIN SQL rendering, and a SQL guard with object/column allowlists. Those pieces are not yet a complete contract: joins and bindings need consistent validation at compilation, and the current IR/compiler cannot represent the composed operations used by the target questions.

The target corpus also exceeds the deterministic fixture's current shape. The PostgreSQL demo schema has customer channels and product stock, while the shared SQLite fixture does not; fixture order status is integer-coded and its dates do not span multiple weeks. The demo needs stable cross-profile semantics without regressing existing fixture consumers.

## Goals / Non-Goals

**Goals:**
- Complete the existing governed join path, ensuring the same validated plan and scope constrain compilation, SQL guard validation, and execution.
- Add a closed, bounded IR expression grammar and deterministic SQL lowering for only the semantics required by q2, q5, q6, and q8.
- Run those questions through both deterministic and PostgreSQL-backed profiles, with protected result checks and explicit unavailable outcomes.
- Keep prior IR payloads and fingerprints unchanged when the new optional expression/plan members are absent.

**Non-Goals:**
- Arbitrary SQL, model-authored SQL, arbitrary CTEs, arbitrary window frames, or general-purpose expression evaluation.
- Changes to semantic assembly vocabulary, relationship authoring, or the deterministic join planner's policy.
- New database adapters or changes to the MongoDB compiler.
- Treating evidence SQL as proof that the governed compiler can execute a question.

## Decisions

### 1. Extend the canonical IR with a closed expression algebra

Represent computed selections and derived aggregation stages as typed IR nodes containing semantic field references, scalar literals, bounded predicates, and a fixed operator set. The set is limited to the target cases: distinct aggregation with an optional filter, conditional expressions, `COALESCE`/`NULLIF`, bounded arithmetic and rounding, date truncation to declared buckets, and a prior-period comparison (`LAG` or an equivalent typed previous-bucket operation). q5's prior-week comparison is part of the acceptance contract, so no arbitrary window API is introduced but a bounded lag-equivalent is required. Derived stages permit only a bounded aggregate result joined or consumed by a later stage; they do not carry names or executable text supplied as SQL.

Every node is immutable, size/depth bounded, validated against the current view and adapter capability set, included in canonical IR identity, and rejected with structured validation issues when unsupported. New optional members are omitted from canonical payloads when unset, preserving fingerprints for existing single-entity IR. The existing IR version remains unchanged for this additive grammar; a consumer that does not support a required capability must reject the IR rather than ignore the node.

**Alternative considered:** Put expression JSON in an opaque extension payload. Rejected because it weakens typing, makes validation less discoverable, and complicates canonical compiler behavior.

### 2. Treat the join plan as an execution constraint, not authority

Use the planner-produced `LogicalJoinPlan` already carried by runtime compilation context. Before lowering, verify its source and root against the IR, each relationship and endpoint against the current relationship graph and authorized view, and every referenced entity/field against the view. Require unique, complete physical entity and column bindings for the plan. Never infer authorization from the physical binding itself.

Compilation emits only the plan's ordered joins. Immediately before adapter execution, the SQL guard must independently confirm every physical table and column, including those in nested derived stages, is in the configured `allowed_objects` and `allowed_columns` scope. An absent binding, stale/mismatched plan fingerprint, unauthorized relationship, or unapproved joined object fails closed before SQL execution. `CompilationEvidence` continues to record fingerprints and identities, not raw plans, SQL, or physical names.

**Alternative considered:** Expand adapter allowlists dynamically from the planner output. Rejected because model/planner output must not grant its own authority; configured guard policy remains the upper bound.

### 3. Lower only the query-corpus subset per dialect

The SQL compiler lowers typed IR deterministically for SQLite and PostgreSQL. Dialect-specific SQL may differ, but week boundaries, null behavior, distinct/filter semantics, and result aliases must match. Use the fixed evaluation clock for relative windows; define weeks as Monday-starting and the seven-day sales interval as `[anchor - 7 days, anchor)`. Advertise only the operators the dialect/compiler can enforce. Unsupported combinations fail during validation/compilation, not as driver errors.

The SQL guard remains AST-based and read-only. It validates all referenced objects/columns for supported subqueries/derived stages and continues to reject multiple statements, writes, external-resource operations, unbounded results, and out-of-scope references. No source-supplied raw SQL is added to the IR.

**Alternative considered:** Implement all SQL CTE/window syntax. Rejected as unnecessary surface area; the contract is the bounded business operation, not arbitrary SQL grammar.

### 4. Extend fixture parity additively

Keep the existing integer `orders.status` column and its values for current fixture consumers. Add a governed business-status column that maps the demo's paid/shipped/refunded terms, plus `customers.channel` and `products.stock_quantity`; seed deterministic dates across multiple Monday-based weeks and stable stock/sales cases. Bind the demo's semantic `status` to the business-status column while legacy fixture SQL continues to use the integer code. Update the fixture setup fingerprint and any version/count assertions atomically. The PostgreSQL source keeps its existing text `orders.status` and schema; its seed is aligned to the same expected outcomes.

**Alternative considered:** Replace the integer status with text. Rejected because it would break existing fixture queries and policy tests unrelated to this change.

### 5. Make the standard questions the executable acceptance contract

Build deterministic, fixed IR/intents for q2, q5, q6, and q8 and run them through the public facade and governed SQL adapter; do not inject a demo-only compiler that bypasses runtime context or evidence. Assert exact deterministic results where stable, plus the declared result shape. Run the same scenarios against PostgreSQL when configured. A missing/unavailable PostgreSQL service is reported as unavailable/skipped, never as a pass. Existing single-entity and two-/three-entity JOIN demo scenarios remain additive.

## Risks / Trade-offs

- **[Risk] The expression grammar grows into a general SQL AST.** → Keep node kinds, arities, depth, collection sizes, aggregate stages, and prior-period behavior explicitly bounded by the acceptance cases; reject unknown nodes.
- **[Risk] SQLite and PostgreSQL date/null behavior diverges.** → Pin Monday week semantics, interval boundaries, and null/division behavior with shared golden outcomes and dialect-specific compiler tests.
- **[Risk] Existing fixture consumers rely on status codes or setup fingerprints.** → Preserve the integer column, add the business-status field, and update fingerprint/count/version expectations in the same task.
- **[Risk] A plan references a valid semantic relationship but a physical table outside adapter scope.** → Require both semantic view/graph validation and independent SQL guard allowlist validation; neither check substitutes for the other.
- **[Trade-off] New computed IR forms may not be understood by older consumers.** → Capability-gate them and reject unsupported capabilities; preserve old payload/fingerprint shape when the new members are unset.

## Migration Plan

1. Add the typed IR forms and structural/fingerprint tests while preserving existing payload vectors.
2. Complete join-plan and physical-binding validation, then implement deterministic SQL lowering and guard tests for both dialects.
3. Extend fixture/schema/seed parity and add stable golden results for the four target questions.
4. Wire the standard questions through deterministic and real-service governed profiles; update runbooks and canonical demo verification.
5. Run focused compiler/guard/IR tests, the full Python test suite, lint/type checks, and OpenSpec validation. If dialect parity or governance checks fail, keep the new capability gated and do not mark the corresponding demo question as passing.

## Open Questions

- Confirm the implementation's structured error-code names for unsupported expression shapes and stale join bindings before freezing tests; the failure behavior must be stable and safe, but this proposal does not require a new public error-code family unless existing codes cannot distinguish these cases.
- Confirm the PostgreSQL seed's fixed evaluation anchor and tenant rows produce comparable q2/q5/q6/q8 result shapes; if exact values differ by profile, pin profile-specific protected expected results while preserving identical semantics.