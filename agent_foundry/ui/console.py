"""Layer 01 (UI/UX) — minimal CLI conversational surface + HITL console.

Enough to drive any graph built by orchestration.build_agent_graph from a
terminal, including approving or denying interrupted (destructive) tool
calls. A production UI swaps this module for a web app talking to the same
compiled graph and the same Command(resume=...) contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class _ResumePayload:
    """langgraph-free stand-in for langgraph.types.Command(resume=...), for
    when `graph` is a native_engine._NativeGraph — see core.agent's own
    _ResumePayload (same shim, duplicated rather than imported: this module
    only ever takes a bare compiled `graph`, no dependency on core.agent)."""

    resume: dict[str, Any]


def handle_interrupt(graph: Any, config: dict, state: dict) -> dict:
    """If the graph paused for approval, prompt an operator and resume it."""
    for i in state.get("__interrupt__", []):
        print(f"\n[approval needed] tool={i.value['tool']} args={i.value['args']}")
        print(f"reason: {i.value['reason']}")
        approved = input("approve? [y/N] ").strip().lower() == "y"
        if hasattr(graph, "ainvoke"):  # a real LangGraph compiled graph — needs a real Command
            from langgraph.types import Command
            command: Any = Command(resume={"approved": approved})
        else:
            command = _ResumePayload(resume={"approved": approved})
        state = graph.invoke(command, config)
    return state


def chat_loop(graph: Any, *, thread_id: str) -> None:
    """A bare REPL: type messages, see replies, approve/deny tool calls inline."""
    config = {"configurable": {"thread_id": thread_id}}
    print(f"agent ready — thread {thread_id!r}. Ctrl-C to exit.")
    while True:
        try:
            text = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        state = graph.invoke({"messages": [{"role": "user", "content": text}]}, config)
        state = handle_interrupt(graph, config, state)
        print(f"agent> {state['messages'][-1]['content']}")
