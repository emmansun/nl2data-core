# Compatibility

> **Reader**: integrators planning upgrades. **Prerequisites**:
> [Capabilities and support](capabilities.md).

## Versioning

| Artifact | Versioning | Notes |
| --- | --- | --- |
| `nl2data-core` | 0.1.0 (Alpha) | Public API is stable within the documented surface; `Development Status :: 3 - Alpha` classifier |
| `nl2data-openai` | 0.1.0 (Alpha) | Depends on `nl2data-core>=0.1.0`; `openai>=1.40,<3` |
| `nl2data-semantic-catalog-postgres` | 0.1.0 (Alpha) | Depends on `nl2data-core>=0.1.0`; `psycopg[binary,pool]>=3.1,<4` |
| `nl2data-mongodb` | 0.1.0 (Alpha) | Depends on `nl2data-core>=0.1.0`; `pymongo>=4.6,<5` |
| Configuration schema | `schema_version: 1` (literal) | Unsupported versions fail closed — never silently downgraded |
| Workflow state snapshots | explicit `schema_version` | Additive migrations only; newer-than-runtime schema rejected |
| Instruction contract | versioned `ModelInstructionBundle` | Unsupported bundle versions fail closed (`INSTRUCTION_VERSION_INCOMPATIBLE`) |

## What is compatible

- **Public API stability**: applications importing only `nl2data`
  documented symbols follow the facade contract. `NL2DataEngine` remains
  available for source compatibility; new code should use
  `NL2Data`/`create_facade`.
- **Deterministic identities**: equivalent inputs in different key
  orders produce identical fingerprints, so canonical artifacts remain
  comparable across versions and processes.
- **Backward-compatible IR execution**: when no view registry is
  configured, existing unbound IR keeps executing exactly as before —
  no view identity is fabricated.
- **Manual fallback paths**: manually authored descriptors/bundles and
  adapters without snapshot bindings keep working unchanged
  (`expected_snapshot_fingerprint=None`).

## What is not compatible (by design)

- **Direct `nl2data_core` imports are deprecated** and may change
  without notice. Migrate through the facade:
  - Replace `NL2DataEngine` usage with `NL2Data`/`create_facade` where
    possible.
  - Move composition inputs into `CompositionProfile`.
  - Configuration still loads through public `load_config`; the typed
    configuration model remains internal until a public configuration
    API ships.
- **MongoDB adapter moved to `nl2data-mongodb`**: the in-core
  `nl2data_core.adapters.mongodb` module remains as a temporary
  self-contained compatibility path with equivalent behavior,
  deprecated and emitting `DeprecationWarning`. New code should import
  from `nl2data_mongodb`; the legacy path will be removed in a future
  major release.
- **Raw payloads never become part of any contract**: SQL/MQL, prompts,
  results, and credentials have no serialized form anywhere — nothing
  depends on them, so nothing can be "compatible" with them.

## Migration policy

| Change | Policy |
| --- | --- |
| View/Bundle activation or rollback | Explicit: configure a registry, resolve views from trusted context; new IR-producing paths must carry a view reference once a registry is configured. Rollback is symmetric |
| Schema/database version | Downgrading is a deployment decision, never an automatic rollback; old runtimes fail closed against new deployments |
| Fingerprint version/rotation | A new version is a new payload with a new fingerprint; old evidence is invalidated, stale checkpoints are rejected before any adapter execution |
| Optional backend activation | Only after passing the mandatory conformance suite (e.g. `tests/conformance/test_workflow_runtime_conformance.py`, `tests/contract/test_backend_conformance.py`) |

## Environment compatibility

| Component | Compatible versions | Notes |
| --- | --- | --- |
| Python | 3.11, 3.12, 3.13 | CI matrix; `requires-python >=3.11` |
| pydantic | `>=2.0,<3` | Core dependency |
| PyYAML | `>=6.0` | Core dependency |
| sqlglot | `>=25.0,<30` | `sql` extra |
| psycopg | `>=3.1,<4` (binary + pool) | `postgres` extra |
| pymongo | `>=4.6,<5` | `mongodb` extra |
| redis | `>=5.0,<7` | `redis` extra |
| openai | `>=1.40,<3` | `nl2data-openai` |

## Next steps

- [Production readiness](production-readiness.md)
- [兼容性 (简体中文)](compatibility.zh-CN.md)
