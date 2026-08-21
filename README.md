# nl2data-core

A governed and extensible Python framework for natural-language access to
heterogeneous enterprise data.

## Status

P0 foundation: public models and structured errors, configuration, query
adapter contract, workflow state foundation, plugin manifest and registry,
telemetry and audit ports, and the `NL2DataEngine` lifecycle skeleton. The
core never imports optional database, LLM, HTTP, or telemetry backend
dependencies.

## Packaging

| Aspect       | Value            |
| ------------ | ---------------- |
| Distribution | `nl2data-core`   |
| Public API   | `import nl2data` |
| Internal API | `import nl2data_core` |

- `nl2data` is the documented, stable public surface (models, errors, engine).
- `nl2data_core` holds narrow internal ports and foundations and is **not**
  a public API; it exists so optional providers can be plugged in without
  leaking into the public surface.
- Requires Python 3.11+.

## Install

```bash
pip install nl2data-core
```

For development (editable install with test/type/lint tooling):

```bash
pip install -e ".[dev]"
```

## Minimal usage

```python
import asyncio

from nl2data import ErrorCode, NL2DataEngine, OutcomeStatus, QueryRequest
from nl2data_core.config.loader import load_config  # P0: internal config loader


async def main() -> None:
    config = load_config(
        {
            "schema_version": 1,
            "service": {"name": "example"},
        }
    )
    engine = NL2DataEngine(config=config)
    await engine.initialize()

    outcome = await engine.query(
        QueryRequest(
            request_id="req-1",
            prompt="How many orders shipped yesterday?",
        )
    )

    # P0: no workflow is configured yet, so the engine returns an explicit
    # not-configured outcome instead of fabricating a result.
    assert outcome.status == OutcomeStatus.NOT_CONFIGURED
    assert outcome.error is not None and outcome.error.code == ErrorCode.NOT_CONFIGURED

    await engine.close()


asyncio.run(main())
```

### P0 not-configured behavior

Without a registered workflow, `query()` never invokes native database, LLM,
or provider executors. It returns a stable `NOT_CONFIGURED` outcome carrying a
safe, redacted `ErrorRecord`. Queries submitted before initialization or
during drain/close are rejected with structured `LifecycleError`s, and the
engine lifecycle is explicit: `created → initializing → ready → draining →
closed`.

## PostgreSQL conformance profile

The P1 query-execution foundation ships an optional PostgreSQL conformance
profile that reuses the SQLite fixture's logical schema, seed data, policy
cases, and protected result assertions.

- Install the optional driver: `pip install -e ".[postgres]"` (psycopg 3).
- Point the profile at a developer-managed service with the
  `NL2DATA_POSTGRES_DSN` environment variable (default:
  `postgresql://localhost:5432/nl2data_test`).
- The profile never requires the service: when the driver or service is
  unavailable, conformance tests are skipped and evaluation outcomes are
  reported as `skipped`/`unavailable` - never as a pass.
- Relevant suites: `tests/integration/test_fixtures.py` and
  `tests/conformance/test_postgres_conformance.py`.

## Development

```bash
python -m pytest        # full unit/contract/integration/security suite
python -m mypy src      # static type checking
python -m ruff check src tests  # lint
```
