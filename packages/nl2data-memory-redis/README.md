# nl2data-memory-redis

An optional Redis backend for the shared, bounded, tenant-scoped Memory
capability in [nl2data-core](https://github.com/emmansun/nl2data-core).
It implements the core `MemoryProvider` contract across processes and
replicas, with safe versioned serialization, namespaced keys, TTL,
compare-and-set, and deterministic recall budgets.

`redis-py` is an optional dependency and is loaded lazily at first use.
The core import boundary never loads the Redis driver.

## Install

```bash
pip install nl2data-memory-redis[redis]
```

Requires Python 3.11+, `nl2data-core>=0.1.0`, and `redis>=5.0,<7`.

From a source checkout:

```bash
pip install -e packages/nl2data-memory-redis[redis]
```

## Public surface

```python
from nl2data_memory_redis import RedisMemoryConfig, RedisMemoryProvider

config = RedisMemoryConfig(namespace="my-app")
provider = RedisMemoryProvider(config, url="redis://127.0.0.1:6379")
```

- `RedisMemoryConfig` — bounded configuration for namespace, record/candidate/batch
  limits, TTL/retention, and connection/command timeouts.
- `RedisMemoryProvider` — core `MemoryProvider` implementation persisting safe
  `MemoryRecord` values in Redis.

## Configuration

Configuration carries only behavior bounds; connection URLs or credentials are
injected by the host through the constructor and never become part of the
serialized configuration.

```python
from nl2data_memory_redis import RedisMemoryConfig

config = RedisMemoryConfig(
    namespace="my-app",
    max_records=10_000,
    max_candidates=1_000,
    max_ttl_seconds=3600,
    connect_timeout_seconds=2.0,
    command_timeout_seconds=2.0,
)
```

## Host-injected client

A host can inject an already-configured Redis client (for example, a pool or
connection from a framework). The provider never closes an injected client;
only clients it builds from a URL are closed by the provider.

```python
from redis import Redis
from nl2data_memory_redis import RedisMemoryConfig, RedisMemoryProvider

client = Redis.from_url("redis://127.0.0.1:6379")
provider = RedisMemoryProvider(RedisMemoryConfig(namespace="my-app"), client=client)
```

## Scope isolation and budgets

Records are namespaced by provider namespace, tenant scope fingerprint (or an
explicit global marker), and session scope. Every recalled record is revalidated
in application code before it is returned. Recall honors record, character, and
token budgets, and returns a deterministic `(created_at, record_id)` ordering.

## Failure handling

Redis connection, timeout, serialization, capacity, conflict, and unavailable
failures are normalized into the core `MemoryInvocationError` codes. No URL,
password, or raw driver exception is ever exposed.

## Compatibility

The in-core `nl2data_core.memory.redis_*` modules and their lazy
`RedisMemoryProvider` / `RedisMemoryConfig` exports are removed from the core
distribution. Import all Redis memory symbols from this package directly.

## More documentation

- [Documentation index](../../docs/README.md)
- [Memory and operations](../../docs/operations/services.md)
- [Capabilities and support](../../docs/reference/capabilities.md)
