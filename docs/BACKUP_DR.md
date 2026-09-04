# Backup & Disaster Recovery

What "backup & DR" means for an agentic app built on this framework, what's
durable by default, what isn't, and how to make it durable — as a runbook,
not a component you import. Unlike the other cross-cutting capabilities
(feature flags, versioning, SLA management), there's no meaningful framework
*code* for this: it's about which data lives where and how it's recovered,
which is a deployment decision, not agent logic.

## What state exists, and where

| State | What it is | Durable by default? |
|---|---|---|
| LangGraph checkpoints | Conversation/thread state (messages, in-flight tool calls) | **No** — `MemorySaver`, in-process only, gone on restart |
| Semantic memory (RAG) | Uploaded documents, embeddings | Depends on the `VectorStore` — `InMemoryVectorStore` is not durable; `ChromaVectorStore(path=...)` persists to disk |
| Reference/tool data | e.g. Sukino's drug formulary SQLite table | Durable (a file on disk) as long as the volume it lives on is |
| Audit log | Every tool call/approval decision, `security.JSONLAuditLog` | Durable (append-only file) as long as the volume it lives on is |
| Cost/eval/SLA history | `CostLedger`, `EvalHarness`, `runtime.SLATracker` | **No** — in-process Python objects, gone on restart |

The one item that's *never* durable regardless of configuration: anything
kept only in a plain Python dict on a long-running process (cost ledgers,
eval harnesses, SLA trackers). These are observability, not source-of-truth
state — losing them on a restart loses a dashboard's history, not a
patient's data. Nothing below tries to persist them; if you need that,
snapshot `.completed`/`._outcomes` to a file or a real metrics backend
yourself.

## Making conversation state durable

`orchestration.py`'s `build_agent_graph` (and every other `build_*_graph`)
takes an optional `checkpointer=` — pass a real `BaseCheckpointSaver` instead
of accepting the `MemorySaver` default:

```python
from langgraph.checkpoint.sqlite import SqliteSaver

with SqliteSaver.from_conn_string("checkpoints.db") as checkpointer:
    graph = build_agent_graph(..., checkpointer=checkpointer)
```

(`pip install langgraph-checkpoint-sqlite`, or `langgraph-checkpoint-postgres`
for `PostgresSaver` in a multi-instance deployment — same drop-in shape.)
This wasn't wired in before; every graph builder hardcoded `MemorySaver()`.
Now it's swappable, the same "real default, swap for production" posture as
`InMemoryVectorStore` vs `ChromaVectorStore`.

## Backup policy

Back up whatever's mounted as durable storage for the deployment — for
Sukino specifically, that's the `sukino-data` Docker volume
(`healthcare/docker-compose.yml`), which holds:

- `data/chroma/` — the ChromaDB-backed document store (uploaded
  prescriptions/discharge summaries + embeddings)
- `data/sukino_reference.db` — the drug formulary reference table
  (reproducible from `reference_db.py`'s `seed()` — not the loss-sensitive
  data, but backing it up costs nothing)
- `data/audit.jsonl` — the compliance-relevant audit trail

None of this is a Sukino-specific concern — it's true of any deployment on
this framework that persists to a mounted volume.

**Recommended cadence** (adjust to your actual RPO/RTO requirements — these
are starting points, not a commitment this repo enforces):

- Nightly snapshot of the volume (or the underlying disk/EBS volume/PV, if
  running outside Docker Compose) to object storage.
- Retain daily snapshots for 30 days, monthly for a year — standard
  grandfather-father-son rotation, adjust for actual compliance needs (Sukino
  is a healthcare client; check what India's data-retention rules actually
  require before finalizing a retention window).
- If a durable LangGraph checkpointer is added (see above), back up its
  store the same way — it becomes part of the same volume/database.

## Restore procedure

1. Provision a fresh container/volume from the latest snapshot.
2. Point `docker compose` (or whatever orchestrator) at the restored volume
   — same mount path (`/app/healthcare/backend/data`), nothing in the app
   needs to change.
3. Start the backend; `reference_db.py`'s `seed()` runs idempotently on
   startup regardless (it's reproducible data, restoring it isn't load-bearing).
4. Verify: hit `/health`, then `/observability/summary` — a non-zero
   `completed_sessions`/audit trail confirms the restored volume's history is
   actually there, not just an empty fresh directory.

## Cross-region failover

Not built or tested here — this deployment (a single backend container + a
volume) has no cross-region replication story. For a real production SLA
that requires it: replicate the volume (or migrate to a managed durable
store — Postgres for checkpoints, a hosted vector DB, S3 for the audit log)
and run a warm/hot standby in a second region. Flag this explicitly if it's
a real requirement — it changes the deployment shape materially (see
`healthcare/README.md`'s "Deploying to any cloud" section) and hasn't been
scoped or estimated.
