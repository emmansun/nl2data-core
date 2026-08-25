## 1. Provider Package Boundary

- [x] 1.1 Complete `establish-model-instruction-contract` before implementing provider message mapping.
- [x] 1.2 Create the sibling `nl2data-openai` package structure with a compatible `nl2data-core` dependency and isolated OpenAI SDK dependency.
- [x] 1.3 Define provider configuration and credential/client-factory injection without storing API keys in core models, metadata, state, telemetry, or errors.
- [x] 1.4 Implement lazy client construction and configuration-derived capabilities with no import-time or capability-time network access.

## 2. OpenAI Invocation

- [x] 2.1 Map the validated instruction bundle to OpenAI system/developer messages and keep the user prompt separate.
- [x] 2.2 Map bounded `ModelInvocationRequest` context/output settings to one OpenAI structured-output request.
- [x] 2.3 Implement strict structured-response extraction into the core `ModelResponse` content envelope.
- [x] 2.4 Reject refusal, truncation, malformed JSON, schema mismatch, unsafe content shape, and output-bound violations without exposing raw responses.
- [x] 2.5 Map valid OpenAI usage fields into consistent bounded `ModelUsage` metadata.

## 3. Error and Lifecycle Boundary

- [x] 3.1 Map authentication/configuration errors to safe non-retryable `ModelInvocationError` values.
- [x] 3.2 Map timeout, connection, rate-limit, and transient service errors to the existing retryable/non-retryable error taxonomy.
- [x] 3.3 Ensure the provider performs one vendor request per `generate()` call and leaves retry/attempt policy to `IntentResolver`.
- [x] 3.4 Implement idempotent close and prevent generation after close without leaking native clients or provider exceptions.

## 4. Verification and Integration

- [x] 4.1 Add injected fake-client contract tests for request mapping, structured output, usage, capabilities, close, and error classification.
- [x] 4.2 Add security tests proving API keys, authorization headers, endpoint details, raw responses, and vendor exceptions never cross the core boundary.
- [x] 4.3 Add resolver integration tests proving OpenAI output follows the existing unsafe-output, view membership, IR, governance, and retry gates.
- [x] 4.4 Add import-boundary tests proving `nl2data-core` and `nl2data` work without the OpenAI SDK and do not load it implicitly.
- [x] 4.5 Add an opt-in live OpenAI evaluation profile with explicit `skipped`/`unavailable`/`verified` classification and no credential requirement for default CI.
- [x] 4.6 Document installation, credential injection, model selection, limits, retry ownership, live-test setup, and rollback to FakeModelProvider; run package and core test suites.
