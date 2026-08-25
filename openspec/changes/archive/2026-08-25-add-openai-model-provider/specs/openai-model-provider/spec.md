## ADDED Requirements

### Requirement: OpenAI provider implements the core ModelProvider contract
The OpenAI provider SHALL implement the existing asynchronous `ModelProvider` contract, consume the validated provider-neutral `ModelInstructionBundle`, and SHALL return only the core `ModelResponse` envelope or normalized `ModelInvocationError` failures. It SHALL not return vendor SDK objects or `StructuredIntent` directly.

#### Scenario: Provider is substitutable
- **WHEN** the OpenAI provider is passed to `IntentResolver` where a `ModelProvider` is accepted
- **THEN** the resolver can invoke it without importing OpenAI-specific types

#### Scenario: Provider output enters the existing validation path
- **WHEN** OpenAI returns structured content
- **THEN** the provider creates a `ModelResponse` and the `IntentResolver` remains responsible for unsafe-output and semantic validation

#### Scenario: System and user messages remain separate
- **WHEN** the provider maps an instruction bundle and user prompt to an OpenAI request
- **THEN** system/developer instructions remain separate from user content and the provider does not reconstruct or weaken core governance rules

### Requirement: Vendor dependency is isolated and client creation is lazy
The OpenAI SDK SHALL be isolated to the provider package. Importing or constructing `nl2data-core`, `nl2data`, or the provider capability object SHALL not require the SDK to be loaded or perform network I/O. Client creation SHALL occur lazily on first generation or through an explicit initialization operation. The provider SHALL depend on the core instruction contract rather than defining a provider-specific system prompt.

#### Scenario: Core works without OpenAI installed
- **WHEN** an application installs only `nl2data-core` and imports its public and provider-neutral AI APIs
- **THEN** imports and fake-provider execution succeed without loading OpenAI

#### Scenario: Capability inspection is offline
- **WHEN** an application constructs the OpenAI provider and calls `capabilities()` without credentials or network access
- **THEN** it receives configuration-derived capabilities without a network request

### Requirement: Requests and structured output are bounded
The provider SHALL enforce configured model/input/output bounds and SHALL send only the bounded prompt and authorized JSON-compatible context from `ModelInvocationRequest`. Structured output SHALL be parsed into bounded JSON content; refusal, truncation, malformed content, schema mismatch, and unsafe response shapes SHALL fail closed.

#### Scenario: Input or output bound is exceeded
- **WHEN** a request exceeds configured input or output limits
- **THEN** the provider raises a non-retryable normalized bounds/request error before or immediately after the vendor call

#### Scenario: Non-structured response is returned
- **WHEN** the vendor returns plain text, refusal, truncated JSON, or a schema-incompatible object
- **THEN** the provider raises a normalized malformed/unsafe response error and exposes no raw response payload

### Requirement: Credentials and vendor errors stay private
Credentials SHALL be supplied by the host through an injected resolver/client factory and SHALL never be stored in core models, request metadata, workflow state, telemetry, fingerprints, or error records. Vendor exceptions SHALL be mapped to safe existing error codes and bounded details.

#### Scenario: Authentication failure is safe
- **WHEN** OpenAI rejects a request because credentials are invalid
- **THEN** the provider raises a non-retryable provider error without exposing the key, authorization header, endpoint, or raw exception text

#### Scenario: Transient failure is retryable
- **WHEN** the vendor returns a timeout, connection failure, or rate-limit response
- **THEN** the provider raises the corresponding retryable normalized error for the resolver's bounded retry policy

### Requirement: Retry ownership remains in the resolver
The provider SHALL perform one bounded vendor request per `generate()` invocation and SHALL NOT implement an independent retry loop. Resolver/workflow retry budgets SHALL remain the only business-level retry policy.

#### Scenario: No multiplicative retries
- **WHEN** the resolver invokes the provider under a three-attempt policy and the provider experiences transient failures
- **THEN** the total vendor calls are bounded by the resolver policy rather than provider retries multiplied by resolver retries

### Requirement: Usage and lifecycle are explicit
The provider SHALL expose configuration-derived `ModelCapabilities`, map valid vendor usage into bounded `ModelUsage`, and implement idempotent `close()` that releases client resources without leaking native objects across the contract.

#### Scenario: Usage is normalized
- **WHEN** OpenAI returns valid prompt and completion token usage
- **THEN** the provider returns a `ModelResponse` with consistent bounded `ModelUsage` totals

#### Scenario: Close is idempotent
- **WHEN** the provider is closed more than once or generation is attempted after close
- **THEN** close completes safely and generation returns a normalized provider-unavailable error
