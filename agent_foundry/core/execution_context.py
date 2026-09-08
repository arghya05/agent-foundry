"""Core — ExecutionContext: the one object every layer (model, tool, memory,
policy, retrieval, audit, eval, events) can receive, instead of loose
thread_id/session_id/user_id/tenant_id strings threaded separately through
each layer and recomputed ad hoc at the top of every orchestration node.

Not yet threaded through orchestration.py's nodes themselves (that's internal
to the LangGraph adapter and stays untouched, see core/agent.py) — Agent.run()/
.stream()/.resume() accept one of these and derive the thread_id LangGraph's
checkpointer keys on from it, which is the seam a caller actually touches.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutionContext:
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    thread_id: str | None = None
    session_id: str | None = None
    user_id: str | None = None
    tenant_id: str | None = None
    agent_id: str | None = None
    trace_id: str | None = None
    locale: str | None = None
    channel: str | None = None
    permissions: frozenset[str] = field(default_factory=frozenset)
    budget: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def resolved_thread_id(self) -> str:
        """What LangGraph's checkpointer actually keys persisted state on —
        thread_id if set, else session_id, else this context's own run_id."""
        return self.thread_id or self.session_id or self.run_id
