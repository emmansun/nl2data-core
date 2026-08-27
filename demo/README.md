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

## Running the demo

See [docs/guides/mainflow-demo.md](../docs/guides/mainflow-demo.md) for the
full runbook, including prerequisites, setup, run sequence, expected outputs,
and troubleshooting.

## Ownership

The demo assets are part of the `mainflow-demo-e2e` OpenSpec change. Update the
corresponding change artifacts under `openspec/changes/mainflow-demo-e2e/` when
modifying the demo contract.
