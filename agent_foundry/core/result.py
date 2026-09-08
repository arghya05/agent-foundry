"""Core — RunResult: what Agent.run()/.resume() return, so a caller never has
to know LangGraph's own result shape (result["messages"][-1]["content"],
result.get("__interrupt__")) the way serve.py and ui/console.py currently do.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunResult:
    content: str
    messages: list[dict] = field(default_factory=list)
    awaiting_approval: bool = False
    thread_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def result_from_graph_output(raw: dict[str, Any], *, thread_id: str) -> RunResult:
    messages = raw.get("messages", [])
    content = messages[-1]["content"] if messages else ""
    return RunResult(
        content=content,
        messages=messages,
        awaiting_approval=bool(raw.get("__interrupt__")),
        thread_id=thread_id,
        raw=raw,
    )
