## 1. Instruction Contract Models

- [x] 1.1 Define immutable versioned `ModelInstructionBundle` and bounded typed instruction sections.
- [x] 1.2 Define output schema/version, safety constraints, authorized context references, Semantic View/model bundle references, and policy/tenant provenance fingerprints.
- [x] 1.3 Implement canonical serialization/fingerprinting and strict rejection of credentials, raw SQL/MQL, executable text, native objects, raw identity claims, and hidden policy material.
- [x] 1.4 Define normalized incompatible-version, bounds, and unsafe-content errors.

## 2. Core Invocation Integration

- [x] 2.1 Extend model invocation composition to carry a validated instruction bundle or safe identity while keeping the user prompt separate.
- [x] 2.2 Build the default instruction bundle from authorized model context, Semantic View, policy, output contract, and bounded safety rules.
- [x] 2.3 Ensure instruction bundle and output schema fingerprints participate in model invocation, workflow, and evaluation evidence.
- [x] 2.4 Preserve FakeModelProvider behavior and existing resolver retry, timeout, output validation, IR, governance, and authorization gates.

## 3. Verification and Documentation

- [x] 3.1 Add unit/contract tests for immutability, bounds, serialization, fingerprint stability, version compatibility, and prompt/context separation.
- [x] 3.2 Add security tests for prompt injection, raw identity/policy leakage, credentials, physical query material, and unsafe context extras.
- [x] 3.3 Add integration tests proving provider-neutral instruction identity survives model/workflow/evaluation evidence without raw prompt or instruction text.
- [x] 3.4 Document that core owns instruction semantics while vendor packages own system/developer/user message mapping; run pytest, Ruff, and Mypy.
