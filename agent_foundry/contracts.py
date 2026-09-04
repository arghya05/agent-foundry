"""Phase 0 — core contracts every layer of Agent Foundry plugs into.

See PLAN.md for the architecture these types implement.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Callable, Protocol


# ---- Identity & policy (Identity & Governance rail) ------------------------

@dataclass(frozen=True)
class Identity:
    """Who is running this call: an agent, a sub-agent, or a human operator."""
    id: str
    tenant_id: str
    roles: tuple[str, ...] = ()


class AutonomyLevel(IntEnum):
    """Graduated autonomy, L0-L5 — replaces a binary approve/deny with how much a
    tool call is allowed to happen without a human in the loop."""
    L0_ANSWER = 0          # no action at all — respond only
    L1_RECOMMEND = 1       # may suggest an action, never take it
    L2_DRAFT = 2           # may prepare a non-destructive action; destructive ones still blocked
    L3_APPROVAL = 3        # may execute, but requires_approval tools still need a human (default)
    L4_POLICY_BOUND = 4    # may execute anything Policy allows, no per-call approval
    L5_FULL_AUTONOMY = 5   # same ceiling as L4 — the distinction is operational trust, not code


class AgentRole(str, Enum):
    """A light taxonomy over AgentConfig — used by build_debate_graph to find the
    judge, and by a builder to document what a specialist is for."""
    GENERALIST = "generalist"
    SPECIALIST = "specialist"
    SUPERVISOR = "supervisor"
    WORKER = "worker"
    VERIFIER = "verifier"


@dataclass
class Policy:
    """What an identity is allowed to do, and what it's allowed to spend."""
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    max_cost_usd_per_thread: float = 1.0
    max_steps_per_thread: int = 25
    requires_approval: frozenset[str] = field(default_factory=frozenset)
    autonomy: AutonomyLevel = AutonomyLevel.L3_APPROVAL


# ---- Tools Gateway -----------------------------------------------------------

@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema
    fn: Callable[..., Any]
    destructive: bool = False  # True => goes through the action guardrail / HITL


@dataclass
class ToolResult:
    tool: str
    ok: bool
    output: Any = None
    error: str | None = None
    latency_ms: float = 0.0


# ---- LLM Gateway ---------------------------------------------------------------

@dataclass
class ToolCall:
    """One native, structured tool call the model chose to make — real
    provider-native tool-calling (Anthropic/OpenAI tool_use blocks), not a text
    convention parsed after the fact."""
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    tool_calls: list[ToolCall] = field(default_factory=list)


class Provider(Protocol):
    def complete(self, messages: list[dict], *, model: str, tools: list[dict] | None = None, **kw: Any) -> LLMResponse: ...


# ---- Guardrails ------------------------------------------------------------------

@dataclass
class GuardrailResult:
    allowed: bool
    reason: str | None = None
    stage: str = ""  # input | output | action | runtime
    escalate: bool = False  # True: hand off to a human/other agent (escalation.py) instead of a flat deny


# ---- Eval --------------------------------------------------------------------------

@dataclass
class EvalRecord:
    level: str  # atomic | component | flow | overall
    unit: str  # tool name, node name, or thread id
    metric: str
    score: float  # 0..1
    detail: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
