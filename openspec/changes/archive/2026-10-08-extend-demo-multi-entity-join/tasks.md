## 1. Canonical IR and Expression Contract

- [x] 1.1 Add immutable typed IR nodes for computed selections, filtered distinct aggregates, conditional expressions, null handling, arithmetic/rounding, date buckets, derived aggregate stages, and bounded prior-period comparison.
- [x] 1.2 Validate expression depth/size, semantic field references, authorized-view membership, required capabilities, legal bucket/order semantics, and unsupported operators before compilation.
- [x] 1.3 Preserve canonical payloads and fingerprints for existing IRs with no new members; add golden fingerprint and rejection tests for new and malformed expression nodes.

## 2. Governed Join Compilation and SQL Guard

- [x] 2.1 Validate join-plan source/root/fingerprint against the IR and current view; require every step relationship, endpoint entity, and join field to be authorized before SQL lowering.
- [x] 2.2 Validate physical entity and column bindings for uniqueness and completeness; reject missing, ambiguous, or unrelated bindings without allowing the binding to expand authority.
- [x] 2.3 Lower validated join plans and computed-query IR deterministically for SQLite and PostgreSQL, preserving Monday-based week buckets, the fixed seven-day interval, null behavior, aliases, and bounded result limits.
- [x] 2.4 Extend compiler tests for q2/q5/q6/q8 operator combinations, dialect-specific SQL, prior-period results, unsupported capabilities, and stable evidence fingerprints.
- [x] 2.5 Verify the SQL guard checks every base and nested derived-stage table/column against configured allowlists; add denial tests for unauthorized joined objects, nested references, and out-of-scope columns before adapter execution.

## 3. Deterministic Fixture Parity

- [x] 3.1 Add customer channel, product stock quantity, and a business-status field to the shared fixture while retaining the existing integer `orders.status` values and legacy query behavior.
- [x] 3.2 Seed stable business statuses and orders across multiple Monday-based weeks, with customer channels, product stock, and order-item quantities sufficient for all four target questions.
- [x] 3.3 Update fixture counts, setup fingerprint/version metadata, and any dependent expectations atomically; pin legacy status-code behavior with regression tests.
- [x] 3.4 Add protected golden results for q2/q5/q6/q8 and verify the logical fixture produces equivalent outcomes under SQLite and PostgreSQL profiles.

## 4. Governed Demo Execution

- [x] 4.1 Build fixed governed intents/IRs for q2/q5/q6/q8 from `demo/questions/questions.yml`, using the normal planner and context-aware compiler path rather than demo-only compiler injection.
- [x] 4.2 Wire all four cases into the deterministic public-facade demo and assert successful outcomes, exact stable values where applicable, and each question's declared result shape.
- [x] 4.3 Wire the same governed cases into the PostgreSQL real-service profile; report missing/unavailable PostgreSQL as unavailable/skipped, never as a pass.
- [x] 4.4 Preserve existing single-entity and two-/three-entity JOIN demo scenarios; verify compilation evidence, current join-plan identity, and SQL guard scope for each new joined case.

## 5. Runbook and Acceptance Gates

- [x] 5.1 Update the canonical mainflow runbooks with q2/q5/q6/q8 expected shapes, semantic time boundaries, governance/compiler path, and PostgreSQL unavailable interpretation.
- [x] 5.2 Run focused IR/compiler/guard/fixture/demo tests, then the complete test suite, lint/type checks, and OpenSpec validation; resolve failures introduced by this change before marking the milestone complete.