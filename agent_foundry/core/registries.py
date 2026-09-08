"""Core — control-plane registries: named lookup for prompts, policies, and
evaluators, the layer ToolRegistry already had (register/get/names by string
key) but nothing else did. Each registry hands back a plain value already
accepted by Agent's constructor (a prompt string for `instructions=`, a real
`contracts.Policy` for `policy=`, an `Evaluator` for `eval_harness=`) — no
changes to Agent itself were needed to wire these in.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..contracts import Policy
from ..eval import Evaluator
from ..versioning import VersionStore


@dataclass
class InMemoryVersionStore:
    """Zero-dependency default VersionStore — same posture as
    context.InMemoryVectorStore / events.InMemoryEventBus: works with no
    setup; swap in versioning.FileVersionStore for on-disk persistence,
    same interface either way."""

    _versions: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)  # name -> [(version, content, label)]
    _current: dict[str, str] = field(default_factory=dict)

    def publish(self, name: str, content: str, *, label: str = "") -> str:
        version = str(time.time_ns())
        self._versions.setdefault(name, []).append((version, content, label))
        self._current[name] = version
        return version

    def get(self, name: str, *, version: str | None = None) -> str:
        target = version or self._current.get(name)
        if target is None:
            raise FileNotFoundError(f"no versions published for {name!r}")
        for v, content, _label in self._versions.get(name, []):
            if v == target:
                return content
        raise ValueError(f"no such version {target!r} for {name!r}")

    def rollback(self, name: str, *, version: str) -> None:
        if not any(v == version for v, _content, _label in self._versions.get(name, [])):
            raise ValueError(f"no such version {version!r} for {name!r}")
        self._current[name] = version

    def history(self, name: str) -> list[dict[str, str]]:
        current = self._current.get(name)
        return [{"version": v, "label": label, "current": str(v == current)} for v, _content, label in self._versions.get(name, [])]


@dataclass
class PromptRegistry:
    """Named, versioned prompts. `.get(name)` is a plain string — directly
    usable as Agent(instructions=registry.get("fashion_advisor"))."""

    store: VersionStore = field(default_factory=InMemoryVersionStore)

    def register(self, name: str, content: str, *, label: str = "") -> str:
        return self.store.publish(name, content, label=label)

    def get(self, name: str, *, version: str | None = None) -> str:
        return self.store.get(name, version=version)

    def rollback(self, name: str, *, version: str) -> None:
        self.store.rollback(name, version=version)

    def history(self, name: str) -> list[dict[str, str]]:
        return self.store.history(name)


@dataclass
class PolicyRegistry:
    """Named Policy lookup. `.get(name)` is a real contracts.Policy —
    directly usable as Agent(policy=registry.get("retail_policy"))."""

    _policies: dict[str, Policy] = field(default_factory=dict)

    def register(self, name: str, policy: Policy) -> None:
        self._policies[name] = policy

    def get(self, name: str) -> Policy:
        return self._policies[name]

    def names(self) -> list[str]:
        return list(self._policies)


@dataclass
class EvalRegistry:
    """Named Evaluator lookup, so "recommendation_relevance"/"groundedness"/
    "tool_accuracy" are shared, named scorers instead of every caller
    constructing/wiring its own EvalHarness."""

    _evaluators: dict[str, Evaluator] = field(default_factory=dict)

    def register(self, name: str, evaluator: Evaluator) -> None:
        self._evaluators[name] = evaluator

    def get(self, name: str) -> Evaluator:
        return self._evaluators[name]

    def names(self) -> list[str]:
        return list(self._evaluators)
