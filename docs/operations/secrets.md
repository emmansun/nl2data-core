# Secrets, Live Testing, and Rollback

> **Reader**: operators and developers running live-service or
> live-provider profiles. **Prerequisites**:
> [Service configuration](services.md).

## Rules for secrets

- **Never commit**: no example, test, or documentation file in this
  repository contains a real token, DSN, credential, raw prompt, or raw
  result. CI scans for these patterns.
- **Never persist**: credentials never enter core models, configuration
  fingerprints, request metadata, workflow state, telemetry, errors, or
  evidence. Configuration serialization preserves only references or
  redacted markers (`<redacted>`).
- **Never log**: error records redact secret-bearing details at
  construction time (`redact_key_value`), and unknown exception types
  become `INTERNAL_ERROR` records with a redacted message.

## Environment injection (ephemeral, host-owned)

| Service | Variables | Read by real-service profiles when |
| --- | --- | --- |
| PostgreSQL | `NL2DATA_POSTGRES_DSN` | Pool first built |
| Redis | `NL2DATA_REDIS_URL` | Client first built |
| MongoDB | `NL2DATA_MONGO_URI`, `NL2DATA_MONGO_DATABASE` | Client first built |
| OpenAI | `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`, `OPENAI_TIMEOUT_SECONDS`, `OPENAI_LIVE_CASES` | Client first built (key) |

The OpenAI provider also accepts an `api_key_resolver` callable or a
`client_factory` at provider construction for host secret injection
(secret managers, vaults, Kubernetes secrets). API keys are read only
when the client is first built.

## Live AI tests

The opt-in live evaluation profile
(`run_live_openai_evaluation` in `nl2data_openai.live_evaluation`) runs
the deterministic AI dataset against the real provider and classifies
every case as `verified`, `unavailable`, or `skipped`:

```powershell
$env:OPENAI_API_KEY = "..."          # ephemeral; remove after the run
$env:OPENAI_BASE_URL = "https://api.openai.com/v1"
$env:OPENAI_MODEL = "gpt-4o-mini"
$env:OPENAI_TIMEOUT_SECONDS = "60"
$env:OPENAI_LIVE_CASES = "normal-intent"
python scripts/run_openai_live.py
```

- Exit code is 0 only when every selected case is `verified`.
- Without injected credentials/factory or `OPENAI_API_KEY`, every case
  is `skipped` — default CI needs no credentials and makes no network
  access.
- Evidence carries only protected fingerprints and normalized codes;
  `unavailable` and `skipped` are reported explicitly, never as passes.

## Service integration profiles

| Profile | Services needed | Verification semantics |
| --- | --- | --- |
| Deterministic unit/contract/security | None | Passes always (fake clients, no network) |
| Real-service integration | PostgreSQL / Redis / MongoDB reachable | Skips explicitly when driver or service is unavailable — never a pass |
| Live provider evaluation | OpenAI-compatible endpoint + credentials | `verified` / `unavailable` / `skipped` per case |

Run real-service tests with every skip reason visible:

```powershell
$env:NL2DATA_POSTGRES_DSN = "postgresql://localhost:5432/nl2data_test"
$env:NL2DATA_REDIS_URL = "redis://localhost:6379"
$env:NL2DATA_MONGO_URI = "mongodb://localhost:27017"
$env:NL2DATA_MONGO_DATABASE = "nl2data_mongo_test"
python -m pytest -rs tests/integration/test_postgres_shared_real.py
```

## Cleanup

- **Environment variables**: remove credentials from the shell session
  after the run (`Remove-Item Env:OPENAI_API_KEY` in PowerShell,
  `unset OPENAI_API_KEY` in POSIX shells).
- **State**: `cleanup()` on state stores removes only bounded batches of
  terminal snapshots, expired idempotency records, and expired leases —
  running workflows and valid leases always survive. Retention is
  host-owned: pass explicit cutoffs.
- **Metadata**: `cleanup_expired` drops snapshot/ledger records past
  their retention window (default 30 days), including an expired active
  snapshot. A failed or unauthorized run never replaces the active
  snapshot.
- **Local artifacts**: `.venv`, `dist/`, and service containers are
  disposable; no credentials are stored in any of them by this project.

## Rollback

| What | How |
| --- | --- |
| OpenAI provider | Swap back to the core's deterministic `FakeModelProvider` at composition time — removes the SDK dependency and network access while keeping the same resolver, governance, and evaluation gates |
| Bundle/snapshot activation | Activate a previous registered snapshot under the same policy, or re-register and re-activate the prior discovery snapshot |
| Schema/database | Downgrading is a deployment decision, never an automatic rollback; old runtimes fail closed against new deployments (`UNSUPPORTED_SCHEMA_VERSION`) |
| Documentation-only change | Restore the previous README and remove docs-only CI checks; no runtime migration involved |

Rollback never rewrites evidence: activating an older artifact
invalidates evidence produced under the newer one, and stale checkpoints
are rejected before any adapter execution.

## Incident hygiene

If a credential is accidentally exposed:

1. Rotate it immediately at the provider (it is treated as compromised).
2. Remove it from shell history and any local file; never "fix" a commit
   by amending it in place — rotate and rewrite history only with
   explicit authorization.
3. Run `python scripts/check_docs.py` to re-scan documentation for
   secret patterns before the next commit.

## Next steps

- [Troubleshooting](troubleshooting.md)
- [密钥与实时测试 (简体中文)](secrets.zh-CN.md)
