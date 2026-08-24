## 1. Canonical IR Models

- [x] 1.1 Define immutable versioned `SemanticQueryIR` and backend-neutral logical models for selections, filters, grouping, ordering, limits, time context, result shape, provenance, and capability requirements.
- [x] 1.2 Define explicit scalar, collection, null/missing, extension, and boundedness rules without accepting physical query syntax or native objects.
- [x] 1.3 Implement canonical JSON serialization and stable SHA-256 IR fingerprinting with golden fixtures.
- [x] 1.4 Implement structural IR validation and structured issues for invalid types, operators, aggregation/grouping, bounds, versions, provenance, and unsupported extensions.

## 2. Compatibility Bridge

- [x] 2.1 Add deterministic `SemanticQueryPlan` to `SemanticQueryIR` translation and document the supported compatibility mapping.
- [x] 2.2 Add IR-to-legacy-plan translation only for compiler compatibility, keeping physical bindings outside the canonical IR payload.
- [x] 2.3 Update plan builder, static plan resolver, and evaluation fixtures to normalize legacy plans at the planning boundary.
- [x] 2.4 Add compatibility tests proving existing plan callers preserve governed validation, fingerprints, and adapter behavior.

## 3. Compiler and Evidence Boundary

- [x] 3.1 Define compiler input/context and safe artifact evidence linking IR version/fingerprint, compiler identity/version, adapter capabilities, and artifact fingerprint.
- [x] 3.2 Adapt SQL compilation to accept validated IR through the compatibility boundary while preserving current SQL safety and execution behavior.
- [x] 3.3 Adapt MongoDB compilation to accept validated IR through the compatibility boundary while preserving structured MQL safety and execution behavior.
- [x] 3.4 Ensure physical bindings, SQL/MQL, credentials, and native values remain outside IR serialization and workflow evidence.

## 4. Workflow and Runtime Integration

- [x] 4.1 Record canonical IR version/fingerprint in workflow compatibility evidence and safe stage metadata.
- [x] 4.2 Reject incompatible or stale IR checkpoints during workflow resume before adapter execution.
- [x] 4.3 Preserve P1/P2 fallback, clarification, tenant, governance, authorization, protection, and idempotency behavior through IR normalization.

## 5. Verification and Migration

- [x] 5.1 Add contract tests for IR schema, serialization round trips, fingerprint stability, unsupported features, and physical-payload rejection.
- [x] 5.2 Add SQL/MongoDB golden compilation and cross-backend logical identity tests.
- [x] 5.3 Add security tests proving model/provider output cannot bypass IR validation or directly execute physical artifacts.
- [x] 5.4 Add import-boundary tests confirming the IR layer has no database, LLM, HTTP, or vendor dependency.
- [x] 5.5 Run the full pytest suite, Ruff, and Mypy; document the legacy-plan compatibility window and migration notes.
