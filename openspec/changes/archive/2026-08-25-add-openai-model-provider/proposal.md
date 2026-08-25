## Why

The core now has a provider-neutral asynchronous ModelProvider contract and deterministic FakeModelProvider, but no real model integration for production use. An isolated OpenAI implementation can provide the first real structured-output provider while preserving the core's dependency boundary, governance pipeline, and vendor-neutral contracts.

## What Changes

- Add a separate `nl2data-openai` provider package implementing the existing `ModelProvider` protocol.
- Construct the vendor client lazily and keep API credentials host-injected and outside core models, state, telemetry, and errors.
- Map bounded `ModelInvocationRequest` plus the validated provider-neutral instruction bundle to OpenAI system/user structured-output requests and normalize responses into `ModelResponse` and `ModelUsage`.
- Map timeout, connection, authentication, rate-limit, refusal, malformed, and provider errors to existing `ModelInvocationError` semantics.
- Declare provider capabilities and model limits without network side effects during construction or capability inspection.
- Preserve resolver-owned retry and timeout policy; the provider performs one bounded vendor call per invocation.
- Add optional live integration tests that skip clearly without credentials/service access, plus fake-client contract and security tests.
- Keep OpenAI SDK imports isolated from `nl2data-core` and the public `nl2data` package.
- Depend on the core model-instruction contract; do not define a provider-specific system prompt or duplicate core safety policy.
- Depend on the core model-instruction contract; do not define a provider-specific system prompt or duplicate core safety policy.

## Capabilities

### New Capabilities

- `openai-model-provider`: A real OpenAI structured-output implementation of the core ModelProvider contract.

### Modified Capabilities

- `model-provider-boundary`: Real provider implementations SHALL preserve the provider-neutral request/response/error contract and dependency isolation.

The provider implementation depends on `establish-model-instruction-contract`, which owns system-instruction semantics and versioning.
- `model-instruction-contract`: OpenAI message mapping SHALL consume the validated provider-neutral instruction bundle and preserve system/user separation.
- `ai-evaluation-foundation`: Provider evaluation SHALL distinguish deterministic/fake, unavailable, skipped, and live verified profiles.

## Impact

This change introduces a separate optional distribution and OpenAI SDK dependency, likely using the repository's current provider package layout or a sibling package repository. It affects provider conformance/security/evaluation documentation and tests, but does not add OpenAI dependencies to `nl2data-core`, alter SemanticQueryIR, or move retry, intent validation, governance, authorization, or result protection into the provider.
