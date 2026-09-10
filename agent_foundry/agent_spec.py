"""AgentSpec — declarative agent configuration: the "define an agent in
YAML/JSON instead of Python" entry point. Every field maps straight onto an
existing core.agent.Agent(...) kwarg — this is a pure composition layer over
Agent.__init__, not a second agent-construction path with its own semantics.

`policy.max_cost_usd_per_thread`/`max_steps_per_thread` are how a spec sets
its budget — Agent.__init__'s own `budget=` kwarg (core/agent.py) just
defaults to `RunBudget(policy)`, so there is no independent "budget" shape to
expose here without duplicating what policy already carries.

Memory here is deliberately shallow: `memory: {"enabled": true}` gets a
default, in-process, non-persistent `MemoryStore()` (see agent_foundry/
context.py) — working/episodic/semantic/procedural/knowledge_graph all start
empty. Seeding real documents/facts into semantic memory or knowledge_graph
is a Python-level task (see examples/support_agent.py), not something this
declarative shape tries to cover.
"""
from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Callable

from .contracts import AutonomyLevel, Identity, Policy
from .context import MemoryStore
from .core.agent import Agent
from .llm_gateway import AnthropicProvider, LLMGateway, OpenAIProvider

_PROVIDERS: dict[str, Callable[[], Any]] = {"anthropic": AnthropicProvider, "openai": OpenAIProvider}


def resolve_tool(ref: str) -> Callable[..., Any]:
    """"pkg.module:function_name" -> the actual callable — the same
    uvicorn/gunicorn-style convention for naming Python code from outside
    Python. Security note: this imports and executes `ref`'s module at
    resolve time, same trust boundary `foundry run <script>` already has
    (it also execs an arbitrary .py file) — not a new attack surface, just
    worth knowing a spec's `tools:` list is not sandboxed."""
    if ":" not in ref:
        raise ValueError(f"tool ref {ref!r} must be 'module.path:function_name'")
    module_name, _, attr = ref.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        raise ImportError(f"tool ref {ref!r}: no such module {module_name!r}") from e
    try:
        return getattr(module, attr)
    except AttributeError as e:
        raise AttributeError(f"tool ref {ref!r}: module {module_name!r} has no attribute {attr!r}") from e


def _frozenset_fields(d: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    out = dict(d)
    for name in names:
        if name in out and not isinstance(out[name], frozenset):
            out[name] = frozenset(out[name])
    return out


def _policy_from_dict(d: dict[str, Any]) -> Policy:
    d = _frozenset_fields(d, ("allowed_tools", "requires_approval"))
    if "autonomy" in d and not isinstance(d["autonomy"], AutonomyLevel):
        raw = d["autonomy"]
        d["autonomy"] = AutonomyLevel[raw] if isinstance(raw, str) else AutonomyLevel(raw)
    return Policy(**d)


def _identity_from_dict(d: dict[str, Any]) -> Identity:
    d = dict(d)
    if "roles" in d and not isinstance(d["roles"], tuple):
        d["roles"] = tuple(d["roles"])
    return Identity(**d)


@dataclass
class AgentSpec:
    """The declarative shape of one Agent(...) call. `model` is Agent's own
    `model=` kwarg — AgentConfig actually stores it as `task`, LLMGateway's
    routing key (see orchestration.AgentConfig), not a literal model name."""

    name: str
    instructions: str
    model: str = "default"
    provider: str | None = None  # "anthropic" | "openai" -> LLMGateway(provider=...); None keeps Agent's own default
    tools: list[str] = field(default_factory=list)  # "module:function" refs, see resolve_tool
    policy: dict[str, Any] | None = None
    identity: dict[str, Any] | None = None
    memory: dict[str, Any] | None = None  # {"enabled": true} -> MemoryStore(); see module docstring
    runtime: str = "langgraph"
    workflow: str = "react"
    eval: dict[str, Any] | None = None  # {"dataset": "path", "thresholds": {...}} — read by callers (e.g. `foundry eval`), not by build_agent
    serve: dict[str, Any] | None = None  # {"host": ..., "port": ...} — same, read by callers that serve this spec

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentSpec":
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"AgentSpec: unknown field(s) {sorted(unknown)} — known fields are {sorted(known)}")
        return cls(**data)

    @classmethod
    def from_json(cls, path: str | Path) -> "AgentSpec":
        return cls.from_dict(json.loads(Path(path).read_text()))

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AgentSpec":
        try:
            import yaml
        except ImportError as e:
            raise ImportError("AgentSpec.from_yaml needs PyYAML — install with `pip install agent-foundry[spec]`") from e
        return cls.from_dict(yaml.safe_load(Path(path).read_text()))


def build_agent(spec: AgentSpec, *, llm: LLMGateway | None = None) -> Agent:
    """The declarative-config equivalent of hand-calling Agent(...) — every
    AgentSpec field maps onto the matching Agent.__init__ kwarg (core/
    agent.py), reusing its own tool/memory coercion and defaulting as-is.
    No new agent-construction logic lives here.

    `llm=` is the one escape hatch to real Python objects a JSON/YAML spec
    can't itself express — an already-configured LLMGateway (custom cache,
    rate limiter, model registry) or, in tests, a scripted provider. When
    given, it wins outright and `spec.provider` is ignored."""
    if llm is None and spec.provider is not None:
        if spec.provider not in _PROVIDERS:
            raise ValueError(f"AgentSpec.provider {spec.provider!r} must be one of {sorted(_PROVIDERS)}")
        llm = LLMGateway(provider=_PROVIDERS[spec.provider]())

    return Agent(
        spec.name,
        spec.instructions,
        model=spec.model,
        tools=[resolve_tool(ref) for ref in spec.tools] or None,
        memory=MemoryStore() if spec.memory and spec.memory.get("enabled") else None,
        policy=_policy_from_dict(spec.policy) if spec.policy else None,
        identity=_identity_from_dict(spec.identity) if spec.identity else None,
        workflow=spec.workflow,
        runtime=spec.runtime,
        llm=llm,
    )
