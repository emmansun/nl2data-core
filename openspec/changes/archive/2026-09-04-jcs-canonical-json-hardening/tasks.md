## 1. Discovery and Characterization

- [x] 1.1 Add characterization tests for current `canonical_json` and `sha256_fingerprint` behavior, including key ordering, NFC normalization, sets, datetimes, floats, unknown objects, and existing golden IR fixtures.
- [x] 1.2 Inventory every fingerprint producer and classify it as identity-critical, persisted-envelope, size-estimation, or presentation-only.
- [x] 1.3 Add failing golden-vector tests for the strict JCS-compatible profile covering object ordering, array order, Unicode strings, escaping, integer/number rendering, unsafe values, and UTF-8 bytes.
- [x] 1.4 Add regression tests proving existing safe IR, Bundle, Verification Suite evidence, audit evidence, catalog envelope, and workflow snapshot payloads can be prepared as JSON-safe values.

## 2. Canonicalization Core

- [x] 2.1 Define canonicalization profile constants for strict JCS-compatible identity and legacy deterministic JSON compatibility.
- [x] 2.2 Implement strict JSON-safe value validation that accepts only objects with string keys, arrays, strings, integers, finite numbers, booleans, and null.
- [x] 2.3 Implement or integrate a JCS-compatible encoder for canonical UTF-8 bytes, deterministic object member ordering, minimal whitespace, string escaping, and number formatting.
- [x] 2.4 Update fingerprint helpers to compute `sha256:<lowercase hex>` from strict canonical UTF-8 bytes and expose safe structured errors for unsupported values.
- [x] 2.5 Preserve compatibility wrappers for non-fingerprint or legacy callers until all identity-critical domains migrate.

## 3. Fingerprint Domain Migration

- [x] 3.1 Move Python-native normalization for datetimes, enums, sets, tuples, and constrained scalars into domain model `canonical_payload` or safe-dump methods before canonicalization.
- [x] 3.2 Migrate Semantic Query IR canonical serialization and fingerprints to the strict shared profile while keeping existing safe golden vectors pinned or intentionally versioned.
- [x] 3.3 Migrate semantic Bundle payload and accepted-assertion manifest fingerprints to the strict shared profile without including lifecycle metadata.
- [x] 3.4 Migrate Verification Suite plans and evidence fingerprints to the strict shared profile while preserving duration/scheduling exclusions.
- [x] 3.5 Migrate assembly audit-evidence entries and publication aggregate bindings to the strict shared profile.
- [x] 3.6 Migrate workflow checkpoint/state fingerprints and optional package envelope fingerprints where they are identity-critical.

## 4. Durable Envelope Compatibility

- [x] 4.1 Add canonicalization profile metadata to fingerprint-critical catalog envelopes, evidence records, workflow-compatible persisted records, and optional package envelopes that require reload validation.
- [x] 4.2 Implement legacy profile classification for existing persisted records that lack strict profile metadata.
- [x] 4.3 Reject unknown or unsupported canonicalization profiles on reload with safe incompatible-profile errors.
- [x] 4.4 Add tests for legacy record readability, strict-profile validation, tampered profile rejection, and no silent fingerprint rewriting.

## 5. Architecture Enforcement and Documentation

- [x] 5.1 Extend the semantic control-plane architecture manifest/tests to identify the single canonicalization owner and prohibit duplicate identity-critical serializers.
- [x] 5.2 Add allowlisted presentation-only serializer cases for authoring export, debug output, or transport formatting that do not produce fingerprints.
- [x] 5.3 Document the strict canonical JSON profile, forbidden values, model preparation responsibilities, canonicalization profile metadata, golden-vector policy, and legacy compatibility rules.
- [x] 5.4 Update evidence-and-fingerprints documentation and any package README sections that describe canonical JSON or fingerprint behavior.

## 6. Validation

- [x] 6.1 Run focused canonicalization and golden-vector tests.
- [x] 6.2 Run focused IR, Bundle, Verification Suite, audit evidence, catalog envelope, workflow, and optional package fingerprint tests.
- [x] 6.3 Run architecture conformance tests for canonical owner and duplicate serializer detection.
- [x] 6.4 Run `ruff check` for touched source and tests.
- [x] 6.5 Run the relevant type-check command for touched core and optional package modules.
- [x] 6.6 Run `openspec validate jcs-canonical-json-hardening --strict` and resolve any spec/task issues.