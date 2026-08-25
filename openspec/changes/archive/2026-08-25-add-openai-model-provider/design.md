## Context

`nl2data-core` owns a provider-neutral `ModelProvider` protocol, bounded `ModelInvocationRequest`, typed `ModelResponse`, `ModelUsage`, normalized `ModelInvocationError`, a provider-neutral model-instruction bundle, and resolver-level retry/timeout logic. The core intentionally has no vendor SDK. The first real integration should validate this boundary against one provider without making OpenAI-specific assumptions part of the core or bypassing the existing IntentResolver and governed workflow.

## Goals / Non-Goals

**Goals:**

- Ship an independent `nl2data-openai` distribution implementing `ModelProvider`.
- Map bounded requests and the validated provider-neutral instruction bundle to OpenAI structured output and validate the response envelope.
- Normalize provider usage and safe error categories/codes.
- Keep construction lazy, resource lifecycle explicit, and credentials outside core models/evidence.
- Support injected client/factory seams for deterministic tests and an optional live profile.
- Declare capabilities from configured model metadata without network calls.

**Non-Goals:**

- Add OpenAI SDK or HTTP dependencies to `nl2data-core`.
- Return `StructuredIntent` directly from the provider.
- Implement prompt orchestration, retry loops, agent tools, governance, authorization, or result protection.
- Support every OpenAI endpoint, streaming, embeddings, image/audio models, or tool calling in the first provider.
- Expose the provider as a required import from the public `nl2data` package.

## Decisions

### Separate distribution with a narrow dependency

Use a sibling `nl2data-openai` package depending on a compatible `nl2data-core` version, the model-instruction contract, and the OpenAI SDK. A core optional extra is rejected for the first provider because it weakens the import boundary and makes every core installation carry vendor packaging metadata.

### Structured output only

The provider maps the instruction bundle to the OpenAI system/developer channel, keeps the user prompt separate, and requests a strict JSON schema matching the bounded provider response contract. It extracts only structured content and constructs `ModelResponse`; all semantic validation remains in `IntentResolver`, which rejects unsafe or out-of-view output before IR building.

### Host-owned credentials and lazy client

Accept an injected API-key resolver or client factory. Never place keys in `ModelConfig`, invocation metadata, errors, usage, or evidence. Client creation occurs on first `generate`, not at import, construction, or `capabilities`, allowing offline composition and deterministic import-boundary tests.

### Single call per provider invocation

The provider makes one bounded SDK call. Resolver/workflow owns retry count and timeout. This prevents multiplicative retries and keeps provider behavior consistent with FakeModelProvider and other future providers.

### Conservative error mapping

Map authentication/configuration errors to non-retryable provider errors, rate limits and transient service failures to retryable availability errors, request/schema/refusal errors to non-retryable response/request errors, and SDK timeouts to model timeout. Messages contain stable safe text; provider exception text and request payloads never cross the boundary.

### Usage and capability mapping

Map OpenAI usage fields into `ModelUsage`, calculating total tokens only when consistent and otherwise using safe bounded values/error handling. Capabilities are configuration-derived: provider name, model, structured-output support, configured input/output bounds, usage accounting, and feature identifiers. No capability call performs network I/O.

## Risks / Trade-offs

- [Vendor SDK response shape changes] → Pin a compatible SDK range, isolate extraction code, and use contract fixtures for response variants.
- [Provider accepts but violates requested schema] → Require structured response parsing plus existing resolver validation; malformed output fails closed.
- [Rate-limit retry storm] → Resolver owns a bounded attempt budget; provider performs no internal retry.
- [Credential leakage] → Inject credentials only into client construction and sanitize all mapped errors; add secret-scanning tests.
- [Live tests become flaky/costly] → Use injected fake clients for normal tests and an opt-in credential/service profile that skips clearly.
- [Model capability differs by model/version] → Require explicit configured capability metadata and reject unsupported structured output before request.

## Migration Plan

1. Complete and publish the core model-instruction contract, then create the sibling package with core protocol/model/error dependencies and lazy OpenAI import.
2. Implement client factory, capability declaration, structured response extraction, usage mapping, error mapping, and idempotent close.
3. Add fake-client contract/security tests and verify core import boundaries with the provider absent.
4. Add an opt-in live integration profile using host-provided credentials and a configured model.
5. Document installation and composition without changing core defaults; rollback by uninstalling/omitting the optional package and using FakeModelProvider or another provider.

## Open Questions

- Whether the sibling package should live in this repository as a separate distribution or in a dedicated repository.
- Which OpenAI model is the first verified reference model and what minimum structured-output schema it supports.
- Whether live provider evaluation should be a separate CI/manual workflow due to cost and credential handling.
