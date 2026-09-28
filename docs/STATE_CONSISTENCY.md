# Native state consistency and recovery limits

The native single-agent engine reloads stored state at each `run`, `stream`,
`resume`, `get_state` and `update_state` operation. Completed steps and explicit
state updates persist. Returned snapshots and stream chunks are deep copies, so
editing a result cannot mutate the engine's next step.

## Choose the store

| Store | Storage lifetime | Stale-write checks in the native single-agent engine |
| --- | --- | --- |
| No store | One engine in one process | Its per-thread lock only |
| `MemoryStateStore` | One shared store object in one process | Atomic version checks |
| `SQLiteStateStore` | Local database file, including process restarts | Atomic version checks across local processes |
| `PostgresStateStore`, `RedisStateStore`, custom three-method stores | Depends on backend configuration | Legacy overwrite behavior; require an application-enforced single writer |

```python
from agent_foundry import Agent
from agent_foundry.core.state_store import SQLiteStateStore

# Supply your configured LLM gateway as usual.
store = SQLiteStateStore("./agent-state.sqlite3")
agent = Agent("assistant", "Help with the permitted task.",
              runtime="native", state_store=store, llm=llm)
```

SQLite uses a real file, one connection per operation, and a database transaction
around each version check and write. Different processes on one host may share
it. This release does not establish network-filesystem or distributed-database
support, production throughput, or survival of every storage/power failure.
Provision database-directory permissions, backup and retention for the data stored.
The store does not authenticate callers or enforce tenant authorization.

## Version and conflict contract

`VersionedStateStore` extends `load/save/delete` with
`load_versioned(run_id) -> (state_or_none, version)` and
`compare_and_swap(run_id, state, expected_version=version) -> new_version`.
The snapshot and version are read atomically. An absent key starts at version zero.
Every save or delete advances the version; deletion retains a tombstone to reject
writes from an older generation. Tombstone compaction is not provided.

The engine uses compare-and-swap when this protocol is available. A stale write
raises `StateConflict` and preserves the newer stored value. A failed save discards
the engine's local working copy. There is no implicit retry. Legacy `save` is an
unconditional administrative write and must not be used to bypass concurrency
checks in application execution.

**A state conflict can occur after a tool has already changed an external system.**
It does not mean that the turn had no effects. Do not automatically replay a turn
on `StateConflict`; inspect the downstream outcome first. Similarly, a lost commit
response may leave the caller uncertain even when the database committed.

This implementation is optimistic concurrency for individual state records. It
does not hold an exclusive owner lease across model/tool calls, fence downstream
writers, authenticate an approval, make checkpoints and tool effects atomic, or
resume arbitrary partially completed steps after a crash. Concurrent approval
resumes and a crash between dispatch and receipt still need a durable action
journal, action identifiers, downstream idempotency and explicit reconciliation.

## Native multi-agent boundary

Specialist turns use the versioned native engine when the supplied store supports
it. Outer topology history, routing/paused markers and blackboard snapshots still
use the older persistence path. Selecting SQLite does **not** make the whole
supervisor/swarm/debate/blackboard workflow safe for concurrent workers. Those
state records and external effects need a coordinated ownership lifecycle.
LangGraph's checkpointer behavior is separate and unchanged.

## Migration and evidence

Existing `StateStore` implementations remain accepted. Native operation entry
now reads the store every time, so budget for an extra store read per operation.
`update_state` now persists immediately and cannot change its storage `thread_id`.
Memory-backed concurrent operations can now raise `StateConflict` where an older
release silently lost a write. Handle that explicitly at the application boundary.

The [I006 record](../review/IMPLEMENTATION_006_STATE_CONSISTENCY.md) retains four
failing regressions, concurrency tests, real subprocess exit/reopen checks, and
the full validation logs. Tests also demonstrate that a conflict after an external
effect leaves that effect in place; they do not assert exactly-once execution.
