## Why

Core fingerprints currently rely on an internal deterministic JSON helper, but the project has not specified a precise cross-version canonicalization contract. As semantic assembly, audit evidence, verification evidence, and durable catalog envelopes become externally inspectable, the fingerprint foundation needs explicit JCS-compatible rules, golden vectors, and a safe migration story.

## What Changes

- Define a shared JCS-compatible canonical JSON capability for all fingerprint-critical payloads.
- Replace ad hoc fallback serialization in fingerprint domains with fail-closed JSON-safe value validation.
- Specify Unicode normalization, object key handling, number handling, byte encoding, forbidden value classes, and stable `sha256:<hex>` fingerprint construction.
- Add canonicalization version metadata and golden vectors for IR, Bundle semantic payloads, Verification Suite evidence, audit evidence, catalog envelopes, workflow snapshots, and optional package envelopes.
- Preserve existing published artifacts and historical evidence through explicit legacy compatibility classification or additive backfill rather than silently changing identities.
- Update documentation so contributors know which payloads are fingerprint-critical and which metadata must remain outside canonical identity.

## Capabilities

### New Capabilities

- `canonical-json-fingerprints`: Shared JCS-compatible canonicalization and fingerprint contract for safe, bounded, fingerprint-critical payloads.

### Modified Capabilities

- `canonical-semantic-query-ir`: Bind IR serialization/fingerprints to the shared canonical JSON contract and require identity stability tests.
- `semantic-assembly-lifecycle`: Bind Bundle semantic payload and lifecycle-excluded metadata rules to the shared canonical JSON contract.
- `semantic-bundle-verification-suite`: Bind Verification Suite plan/evidence fingerprints to the shared canonical JSON contract.
- `durable-semantic-catalog`: Bind persisted envelopes and reload validation to canonicalization version metadata and legacy compatibility rules.
- `semantic-control-plane-boundaries`: Ensure one canonical owner for canonicalization helpers and prohibit duplicate/ad hoc fingerprint serialization.

## Impact

- Affected code: `nl2data_core.canonical`, all fingerprint producers/consumers, golden fixtures, catalog envelope code, verification evidence, audit evidence, workflow snapshot and optional package envelope tests.
- Affected APIs: canonicalization helpers may gain explicit strict/JCS entry points and version metadata; existing public fingerprint strings remain `sha256:<lowercase hex>`.
- Dependencies: no new runtime dependency expected unless a small vetted JCS implementation is chosen; if added, it must remain core-safe and optional-free.
- Systems: published artifacts, persisted catalog records, workflow checkpoints, and evidence stores need explicit compatibility handling so historical identities remain explainable.