# Verification Suite

> **Reader**: architects, security reviewers, and control-plane integrators.
> **Prerequisites**: [Semantic layer](semantic-layer.md) and
> [Evidence and fingerprints](evidence-and-fingerprints.md).
>
> **Language**: English is normative. See the
> [Chinese translation](verification-suite.zh-CN.md).

The Verification Suite is the release gate between an approved semantic
assembly and an immutable published Bundle. A frozen `VerificationPlan` binds
the approved draft revision to bounded smoke and semantic contract cases. The
plan is lifecycle content: changing it invalidates approval, but it never enters
the Bundle canonical payload or semantic fingerprint.

## Layers and policy

Layers run in fixed order. Layer 1 is core-owned and validates draft/review/plan
bindings, Bundle structure and identity, manifest derivation and equivalence,
scope, compatibility, and calculated-field invariants before external work.
Layer 2 executes governed canonical Semantic IR against controlled fixtures.
Layer 3 independently evaluates a closed semantic contract DSL over the same
protected observation when execution inputs match.

`compatibility-v1` requires Layer 1 and labels Layer 2/3 as not required.
`production-v1` requires all three layers, at least one enabled Layer 2 case and
one enabled Layer 3 case, and every required case to pass. Policies never
downgrade because a fixture, secret resolver, capability, or service is missing.

## Execution trust boundary

`VerificationExecutor` is replaceable and identified by a capability
fingerprint. The SQLite reference executor composes existing IR validation,
compilation, guard, governance, authorization, adapter execution, result
protection, and fixture lifecycle boundaries. Case, layer, and suite deadlines
are bounded; cancellation propagates; reset and disposal are always attempted.

Executors return a bounded transient `VerificationObservation`. It may contain
protected scalar rows needed to evaluate assertions, but it is reduced
immediately and released. Observation values, queries, physical names,
deployment references, credentials, and backend exceptions cannot enter
persisted evidence or Admin DTOs.

## Evidence identity

Case, layer, and suite evidence contains statuses, counts, bounded issue codes,
versions, and fingerprints. Suite evidence binds the plan, policy, draft
revision, Bundle, accepted manifest, tenant/source scope, core runner, and any
external executor. Durations are operational metadata and are excluded from
evidence fingerprints. One-sided, stale, or drifted identities fail closed.

Publication stores Bundle, manifest, evidence, audit, version, and supersession
metadata atomically. Existing publications without evidence are
`legacy_unverified`; they remain readable but cannot satisfy `production-v1`.

## Next steps

- [Verification operations](../operations/verification-suite.md)
- [Semantic Assembly YAML authoring](../guides/semantic-assembly-authoring.md)
- [Compatibility](../reference/compatibility.md)
