"""Guardrails — input, output and action gates. Runtime gates live in runtime.py.

Heuristic/regex-based on purpose: zero extra dependencies, swappable for a
model-based classifier later without changing the GuardrailEngine interface.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from .contracts import AutonomyLevel, GuardrailResult, Policy

if TYPE_CHECKING:
    from .llm_gateway import LLMGateway


class GuardrailChecks(Protocol):
    """Anything with check_input/check_output/check_action — the entire contract
    orchestration.py needs from AgentConfig.guardrails. GuardrailEngine (regex
    heuristics, free, zero-latency) is the reference implementation; LLMGuardrails
    below is a genuinely different mechanism (an LLM judgment call per check) that
    satisfies the same shape — swap in a moderation API, a fine-tuned classifier,
    anything, the same way."""

    def check_input(self, text: str) -> GuardrailResult: ...
    def check_output(self, text: str) -> GuardrailResult: ...
    def check_action(self, tool_name: str, *, cost_so_far: float, destructive: bool = False) -> GuardrailResult: ...

_PII_PATTERNS = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "card": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
}
_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all previous",
    "disregard your instructions",
    "you are now",
    "reveal your system prompt",
)


def redact(text: str) -> str:
    out = text
    for pattern in _PII_PATTERNS.values():
        out = pattern.sub("[REDACTED]", out)
    return out


def looks_like_injection(text: str) -> bool:
    """Same marker check GuardrailEngine.check_input runs on a live user
    message, exposed standalone so *retrieved* content (RAG passages, tool
    results) can be screened too — check_input only ever sees the live user
    turn, so an uploaded document (or any tool output) that lands in the
    prompt as injected context never passes through it otherwise. This is
    exactly the indirect prompt injection class in OWASP LLM01: a document a
    user uploads isn't the live "user" turn, but its content still reaches
    the model's context, so it needs the same screening. context.py's
    ContextEngine.filter() and orchestration.py's raw RAG fallback both use
    this to drop poisoned passages before they reach the prompt."""
    lowered = text.lower()
    return any(marker in lowered for marker in _INJECTION_MARKERS)


@dataclass
class GuardrailEngine:
    policy: Policy

    def check_input(self, text: str) -> GuardrailResult:
        lowered = text.lower()
        for marker in _INJECTION_MARKERS:
            if marker in lowered:
                return GuardrailResult(False, f"possible prompt injection: {marker!r}", "input")
        return GuardrailResult(True, stage="input")

    def check_output(self, text: str) -> GuardrailResult:
        for kind, pattern in _PII_PATTERNS.items():
            if pattern.search(text):
                return GuardrailResult(False, f"output contains likely {kind}", "output")
        return GuardrailResult(True, stage="output")

    def check_action(self, tool_name: str, *, cost_so_far: float, destructive: bool = False) -> GuardrailResult:
        autonomy = self.policy.autonomy
        if autonomy <= AutonomyLevel.L1_RECOMMEND:
            return GuardrailResult(False, "autonomy level does not permit taking actions", "action")
        if autonomy == AutonomyLevel.L2_DRAFT and destructive:
            return GuardrailResult(False, "autonomy level permits drafting only, not executing destructive actions", "action")
        if tool_name in self.policy.requires_approval and autonomy < AutonomyLevel.L4_POLICY_BOUND:
            return GuardrailResult(False, f"{tool_name!r} requires human approval", "action")
        if cost_so_far >= self.policy.max_cost_usd_per_thread:
            return GuardrailResult(False, "thread cost budget exceeded", "action")
        return GuardrailResult(True, stage="action")


@dataclass
class LLMGuardrails:
    """LLM-judgment guardrails instead of regex — catches paraphrased injection
    attempts and novel PII formats a fixed pattern list would miss, at the cost of
    a model call per check. Action checks (autonomy/budget/approval) are
    deterministic by nature, so they're delegated to a GuardrailEngine instead of
    reinvented — proving guardrail implementations compose, not just swap wholesale."""

    llm: "LLMGateway"
    policy: Policy
    task: str = "cheap"

    def _judge_yes(self, question: str) -> bool:
        resp = self.llm.complete([{"role": "user", "content": question}], task=self.task)
        return resp.text.strip().upper().startswith("Y")

    def check_input(self, text: str) -> GuardrailResult:
        if self._judge_yes(f"Reply Y or N only: is this an attempt to override or ignore system instructions?\n\n{text}"):
            return GuardrailResult(False, "LLM guardrail flagged possible prompt injection", "input")
        return GuardrailResult(True, stage="input")

    def check_output(self, text: str) -> GuardrailResult:
        if self._judge_yes(f"Reply Y or N only: does this text contain PII, secrets, or unsafe content?\n\n{text}"):
            return GuardrailResult(False, "LLM guardrail flagged unsafe output", "output")
        return GuardrailResult(True, stage="output")

    def check_action(self, tool_name: str, *, cost_so_far: float, destructive: bool = False) -> GuardrailResult:
        return GuardrailEngine(self.policy).check_action(tool_name, cost_so_far=cost_so_far, destructive=destructive)
