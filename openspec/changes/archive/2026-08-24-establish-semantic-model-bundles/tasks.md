## 1. Semantic Model Bundle Contract

- [x] 1.1 Define immutable versioned `SemanticModelBundle` and safe provenance/quality/compatibility models.
- [x] 1.2 Reuse existing descriptor/entity/field/relationship primitives inside the bundle without duplicating validation logic.
- [x] 1.3 Add bounded semantic measure, grain, source/catalog reference, dependency, authored/inferred/approved trust, and compatibility metadata.
- [x] 1.4 Implement canonical serialization, stable SHA-256 fingerprinting, schema-version checks, and safe payload validation.

## 2. Bundle Validation and Catalog

- [x] 2.1 Implement structural bundle validation for identifiers, types, aggregations, relationships, grain, dependencies, bounds, and unsafe content.
- [x] 2.2 Define replaceable bundle catalog/loader protocols for publish, lookup, active snapshot, activation, rollback, and version listing.
- [x] 2.3 Implement a bounded in-process catalog with immutable publication records and atomic active-pointer changes.
- [x] 2.4 Reject invalid, stale, incompatible, or incomplete bundles before publication or activation.
- [x] 2.5 Preserve prior active bundle and support rollback without mutating published artifacts.

## 3. Semantic View Integration

- [x] 3.1 Add bundle snapshot identity/version/fingerprint to View resolution inputs and safe provenance.
- [x] 3.2 Make bundle-backed View Registry resolution consume a complete active validated bundle snapshot.
- [x] 3.3 Keep descriptor-only resolution as an explicit compatibility adapter with one conversion path and no duplicated rules.
- [x] 3.4 Ensure bundle activation/rollback invalidates old resolved-view, IR, workflow, and Memory evidence through fingerprint checks.
- [x] 3.5 Preserve tenant, principal, purpose, policy, capability, feature-flag, and fail-closed resolution behavior.

## 4. Verification and Documentation

- [x] 4.1 Add unit tests for bundle bounds, safe content, fingerprints, versioning, provenance, trust metadata, and immutability.
- [x] 4.2 Add catalog contract tests for publish, lookup, activation, active snapshots, duplicate versions, and rollback.
- [x] 4.3 Add security tests proving inferred metadata cannot grant access and physical/credential content never reaches View or provider context.
- [x] 4.4 Add integration tests for bundle-to-View-to-IR binding and stale evidence rejection after activation/rollback.
- [x] 4.5 Update README and active specifications with bundle lifecycle and compatibility behavior; run pytest, Ruff, and Mypy.
