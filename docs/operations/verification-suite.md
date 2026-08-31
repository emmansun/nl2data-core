# Verification Suite Operations

> **Reader**: operators and platform engineers. **Prerequisites**:
> [Verification Suite architecture](../architecture/verification-suite.md).
>
> **Language**: English is normative. See the
> [Chinese translation](verification-suite.zh-CN.md).

## Production gate

Select `production-v1` explicitly and attach the exact plan before approval.
Use deterministic fixtures with fixed schema, seed, clock, timezone, reset
behavior, and setup fingerprint. Required Layer 2/3 cases must be enabled and
must pass. `failed`, `skipped`, `unavailable`, `timed_out`, `not_run`, missing
executor capability, or missing secret resolution blocks publication and
production activation.

Never treat a test-process exit code with all real-service cases skipped as
verification. PostgreSQL and MongoDB profiles are environment-gated; inspect
the skip reason and evidence before promoting a release.

## Secrets and executors

Plans contain unresolved fixture/deployment profile identifiers, never DSNs,
tokens, passwords, physical names, SQL, or MQL. Hosts resolve connection
material ephemerally inside the executor boundary. Resolved values must not be
logged, returned in exceptions, cached in evidence, or passed to Admin DTOs.

Record executor identity and capability fingerprint. Changing runner, executor,
capabilities, policy, plan, candidate, manifest, draft revision, or scope makes
old evidence stale and requires verification again.

## Audit and inspection

Admin `verify_draft` is side-effect-free and requires `ASSEMBLY_VERIFY`.
`get_verification_evidence` and publish-audit inspection require
`ASSEMBLY_AUDIT`. Results contain only bounded statuses, counts, issue codes,
identities, and fingerprints. Durable PostgreSQL evidence is checksummed and
revalidated against the publication on every load.

## Troubleshooting

| Status/code | Action |
| --- | --- |
| `capability_mismatch` | Configure an executor whose declared capability fingerprint satisfies the plan; do not remove the requirement silently |
| `fixture_unavailable` / `unavailable` | Restore the fixture/service/secret resolver and rerun; unavailable is never a pass |
| `timed_out` / `layer_deadline_exhausted` | Diagnose the fixture or adapter, then adjust only bounded reviewed deadlines |
| `candidate_drift` / `candidate_fingerprint_drift` | Rebuild the plan IR against the current approved candidate and reapprove |
| `verification_evidence_mismatch` | Discard stale evidence and verify the exact current revision, plan, policy, runner, executor, manifest, Bundle, and scope |
| `legacy_unverified` | Republish under an explicit verification policy; do not fabricate evidence for an old publication |

## Validation commands

```powershell
python -m pytest tests/unit/test_verification_models.py tests/unit/test_verification_execution.py
python -m pytest tests/contract/test_postgres_catalog_contract.py
python scripts/check_docs.py
```
