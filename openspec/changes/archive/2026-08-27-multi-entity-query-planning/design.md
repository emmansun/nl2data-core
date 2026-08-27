## Context

The project already enforces a governed runtime with explicit stage gates, immutable IR, resolved-view binding, and authorization evidence checks. The main gap is multi-entity query flexibility: join path decisions are not yet a first-class deterministic planning artifact.

This change adds deterministic join planning as a bounded intermediate layer rather than expanding direct model SQL generation. The design preserves transport neutrality, fail-closed behavior, and adapter/provider boundaries.

## Goals / Non-Goals

**Goals:**
- Support multi-entity semantic query requests without delegating join decisions to probabilistic model output.
- Introduce explicit `RelationshipGraph` and `JoinPlanner` contracts as deterministic planning inputs.
- Keep all planning constrained by authorized semantic view membership and governance context.
- Stage rollout to preserve backward compatibility and measurable quality gates.

**Non-Goals:**
- Introduce direct text-to-SQL execution from model output.
- Bypass existing artifact guard/govern/authorize workflow gates.
- Ship automatic relationship discovery as trusted runtime behavior in v1.

## Decisions

### 1) Choose semantic-layer compilation as primary strategy
- Decision: implement deterministic planning with a semantic intermediate representation and join graph resolution.
- Rationale: aligns with current immutable IR + fail-closed architecture and maximizes reproducibility.
- Alternatives considered:
  - Agent runtime planning as primary: rejected due to lower determinism and weaker auditability.
  - RAG example-driven join selection: rejected as main strategy due to poor unseen-schema robustness.

### 2) Add a dedicated internal JoinPlanner port
- Decision: add a planner boundary that maps `MultiEntityIntent + RelationshipGraph + AuthorizedView` to a deterministic `LogicalJoinPlan`.
- Rationale: keeps join policy explicit, testable, and backend-neutral.
- Alternatives considered:
  - Embed join logic directly in compiler: rejected to avoid coupling policy to physical dialect compilation.

### 3) RelationshipGraph is governance-owned data
- Decision: treat relationship edges as governed artifacts (explicit config first, inferred candidates optional later with approval).
- Rationale: prevents silent drift and preserves audit trails.

### 4) Fail-closed path resolution
- Decision: unresolved path, ambiguous path, or unauthorized edge always returns structured rejection/clarification before adapter invocation.
- Rationale: preserves existing safety guarantees and avoids silent degraded correctness.

### 5) Phased delivery
- Decision: deliver in three phases:
  - P1: Multi-entity intent schema and validation.
  - P2: RelationshipGraph + deterministic join planner.
  - P3: LogicalJoinPlan compilation integration + conformance evidence.
- Rationale: reduces risk and creates clear acceptance gates per phase.

## Risks / Trade-offs

- [Risk] RelationshipGraph curation overhead slows onboarding -> Mitigation: provide explicit templates and scoped defaults per source domain.
- [Risk] Ambiguity rejection increases clarification frequency early -> Mitigation: add guided error metadata and operator docs for edge registration.
- [Risk] Compiler/planner split adds complexity -> Mitigation: keep strict interface contracts and conformance fixtures.
- [Risk] Overfitting to SQL backends -> Mitigation: keep logical join plan backend-neutral and compile per adapter capability.

## Migration Plan

1. Add capability specs and phased tasks (P1/P2/P3).
2. Implement P1 schema and validation in non-breaking mode (single-entity path remains supported).
3. Implement P2 planner with explicit relationship graph input and deterministic path selection.
4. Integrate P3 compiler path and add conformance/integration suites.
5. Update docs and troubleshooting with multi-entity rejection/clarification guidance.

Rollback strategy:
- Feature-flag deterministic multi-entity planning path.
- If instability is detected, keep single-entity and existing deterministic flows active while planner path is disabled.

## Open Questions

- Should v1 path ranking allow weighted edge preferences, or only strict deterministic shortest-path with ambiguity rejection?
- Which structured error codes should be public for path failures (`JOIN_PATH_NOT_FOUND`, `JOIN_PATH_AMBIGUOUS`, `JOIN_EDGE_UNAUTHORIZED`)?
- Should inferred relationship candidates be stored in semantic catalog extensions or a separate governance artifact store?
