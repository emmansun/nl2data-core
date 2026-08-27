## 1. Demo Scope and Acceptance Definition

- [x] 1.1 Confirm the canonical demo path and profile boundaries (deterministic baseline + real-service durability profile).
- [x] 1.2 Map each acceptance checkpoint to existing public APIs and existing test evidence sources.
- [x] 1.3 Freeze the v1 pass/fail contract for configuration, startup, execution, and persistence/recovery semantics.

## 2. Demo Assets and Verification Wiring

- [x] 2.1 Add or align demo-facing test/profile entrypoints to execute the canonical path end to end.
- [x] 2.2 Ensure durable workflow checks cover replay/resume semantics and cancellation fail-fast behavior.
- [x] 2.3 Ensure Redis memory and workflow-postgres participation is explicit in the real-service profile.

## 3. Reference Source Dataset and Evidence Pack

- [x] 3.0 Create root-level `demo/` directory layout (`demo/schema`, `demo/seed`, `demo/questions`, `demo/run`) and document ownership.
- [x] 3.1 Define PostgreSQL reference schema for the order-fulfillment domain (`customers`, `orders`, `order_items`, `products`, `payments`, `shipments`) with required join/time/segment fields.
- [x] 3.2 Add seed strategy with realistic row-scale targets, tenant partitions, and anomaly samples (cancelled, partial shipment, refund, duplicate payment, delayed/null fields).
- [x] 3.3 Add a standard 10-question demo suite aligned to capability coverage (join, aggregation, time-window, tenant isolation, clarification).
- [x] 3.4 Add one SQL evidence query per standard question with deterministic result-shape checks for troubleshooting and runbook verification.

## 4. Documentation and Operational Runbook

- [x] 4.1 Add a developer/operator runbook describing prerequisites, setup, run sequence, and expected outputs.
- [x] 4.2 Document failure interpretation (service unavailable vs verified failure) and recovery troubleshooting pointers.
- [x] 4.3 Align installation/composition references to the minimal package set required for the demo.
- [x] 4.4 Document expected outputs for each standard demo question and map each to its SQL evidence query.
- [x] 4.5 For each standard demo question, add end-user context: target role, decision intent, suggested action threshold, and interpretation caveat.
- [x] 4.6 Publish the canonical runbook at `docs/guides/mainflow-demo.md` and mirrored translation at `docs/guides/mainflow-demo.zh-CN.md`.
- [x] 4.7 Add a "question-to-value" matrix section covering all 10 standard questions in both guide files.

## 5. Quality Gates and Readiness

- [x] 5.1 Run OpenSpec validation for `mainflow-demo-e2e` and resolve all schema or scenario formatting issues.
- [x] 5.2 Verify deterministic and real-service CI paths surface unambiguous evidence for demo pass/fail.
- [x] 5.3 Publish a release-readiness checklist entry confirming the canonical mainflow demo is runnable and repeatable.
- [x] 5.4 Validate the runbook against an end-user value checklist (not only technical correctness).
