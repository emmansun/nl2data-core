## 1. Contracts and Configuration

- [x] 1.1 Define immutable model invocation request, provider response, usage metadata, structured intent, clarification, and normalized model error contracts.
- [x] 1.2 Define the asynchronous `ModelProvider` protocol and capability declaration without importing a vendor SDK or network framework.
- [x] 1.3 Add bounded model configuration for input/output size, timeout, attempts, and usage metadata while preserving safe secret handling.
- [x] 1.4 Add stable fingerprints and safe serialization helpers for invocation configuration, intent, clarification, and provider errors.

## 2. Deterministic Model Provider

- [x] 2.1 Implement a deterministic fake model provider with fixed structured responses, timeout simulation, malformed-output simulation, and usage accounting.
- [x] 2.2 Implement provider error normalization for timeout, malformed response, unavailable provider, output limit, and retry exhaustion cases.
- [x] 2.3 Add contract tests for async lifecycle, bounds, immutable responses, stable fingerprints, and credential/error redaction.

## 3. Structured Intent Resolution

- [x] 3.1 Define the authorized model-context input containing only bounded request data and policy-pruned semantic references.
- [x] 3.2 Implement context assembly that excludes credentials, native clients, raw result sets, unrestricted schema metadata, and hidden policy state.
- [x] 3.3 Implement intent resolution from provider output to validated structured intent, clarification-required result, or safe rejection.
- [x] 3.4 Reject raw SQL/MQL/shell/AST/driver-shaped output and semantic references outside the authorized view before plan building.
- [x] 3.5 Add the plan-builder handoff for the current P1 Semantic Query Plan shape without changing adapter or governance contracts.
- [x] 3.6 Add resolver tests for valid intent, ambiguity, malformed output, unauthorized references, executable output, and sensitive context exclusion.

## 4. AI Evaluation Foundation

- [x] 4.1 Define deterministic AI evaluation case, fake response, protected evidence, assertion, and report models.
- [x] 4.2 Implement evaluation cases for normal intent, clarification, malformed provider output, timeout, output bounds, and prompt-injection attempts.
- [x] 4.3 Enforce mandatory safety assertions for no adapter invocation after unsafe output, no raw provider payload in evidence, and bounded retry behavior.
- [x] 4.4 Add repeatability tests proving equal inputs and fake responses produce equal protected intent results and report fingerprints.

## 5. Workflow Integration and Quality Gates

- [x] 5.1 Add an opt-in AI workflow port that preserves the existing P1 structured-plan and not-configured fallback paths.
- [x] 5.2 Add integration tests from public `QueryRequest` through fake provider, intent validation, Semantic Query Plan handoff, and existing governed execution boundary.
- [x] 5.3 Verify the core package imports without provider SDKs, credentials, or network access and document optional provider integration boundaries.
- [x] 5.4 Run the complete P0/P1 suite plus AI contract, security, evaluation, Ruff, Mypy, and package-install checks.