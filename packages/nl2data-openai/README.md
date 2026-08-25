# nl2data-openai

An optional OpenAI structured-output provider for
[nl2data-core](https://github.com/nl2data/nl2data-core). It implements the
provider-neutral asynchronous `ModelProvider` contract: bounded
`ModelInvocationRequest` in, typed `ModelResponse`/normalized
`ModelInvocationError` out, with the OpenAI SDK isolated to this package.

See the main repository README for installation, credential injection,
model selection, limits, retry ownership, live-test setup, and rollback
guidance.
