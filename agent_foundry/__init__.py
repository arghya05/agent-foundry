"""Agent Foundry — modular components for building any LLM agent.

Layers: llm_gateway, tools_gateway, orchestration, runtime, context,
guardrails, eval, observability, contracts. See PLAN.md for the
architecture this package implements, and examples/support_agent.py for
a complete agent built from these pieces.

`Agent`/`Workflow`/`ExecutionContext`/`RunResult` (agent_foundry.core) are the
top-level entry point — a facade over orchestration.py's build_*_graph
functions that never surfaces LangGraph itself. Every submodule above stays
directly importable and unchanged for callers that want the lower-level path.

`tool`, `PromptRegistry`/`PolicyRegistry`/`EvalRegistry`, and
`ModelRouter`/`ModelRequest`/`ModelCapabilities`/`apply_route` are the
control-plane additions on top of that seam — named lookup for prompts/
policies/evaluators, a decorator for governed tools with real timeout/cache
enforcement, and capability-based model selection over llm_gateway.py's
existing tier-routing/failover.

`Agent.start(message)` returns a `Run` — a formal lifecycle
(STARTED/RUNNING/WAITING_HUMAN/WAITING_EVENT/SUSPENDED/COMPLETED/FAILED/
CANCELLED) with `.pause()/.unpause()/.cancel()/.retry()/.fork()/.replay()/
.wait_for_event()` on top of `Agent.run()`'s plain `RunResult` (unchanged).
`run_eval(agent, cases)` scores an `Agent` against a list of `EvalCase`s and
returns a `Scorecard` with a `.passes(thresholds)` regression gate — the
same scoring the `foundry eval` CLI subcommand (`cli.py`) runs.

`AgentSpec`/`build_agent` (`agent_spec.py`) are the declarative alternative
to constructing an `Agent` by hand — a YAML/JSON/dict spec instead of Python
kwargs. `Agent.arun`/`.astream`/`.aresume` are non-blocking counterparts to
`.run`/`.stream`/`.resume`; `ToolRegistry.ainvoke` (`tools_gateway.py`) and
`@tool`-decorated `async def` functions (`tool_decorator.py`) are the async
tool-side counterpart.

`Agent(runtime=...)` picks a default execution backend — `"native"` (the
default: a from-scratch, zero-LangGraph-dependency implementation of the
think/act/critique loop, so `pip install agent-foundry` with no extras is
a complete, working agent) or `"langgraph"` (opts into LangGraph's
StateGraph for its persistence/streaming/HITL machinery, required for the
multi-agent Workflow topologies); `agent.run(msg, runtime=...)` overrides
it for one call. Both dispatch through `core.protocols.WorkflowEngine` +
the `RUNTIMES` registry (`core/engines.py`) — the seam a new backend
(Temporal, say) plugs into by implementing `WorkflowEngine` and adding one
registry entry, not by touching `Agent` itself. `Model` (`contracts.py`) is
the same `Provider` Protocol `AnthropicProvider`/`OpenAIProvider`/
`MultiProvider` already implement, under the name most readers reach for
first; `Message` documents the `{"role": ..., "content": ...}` shape every
message dict here already has.
"""
from .agent_spec import AgentSpec, build_agent
from .contracts import Message, Model
from .core.agent import Agent, Workflow
from .core.evalgate import CaseResult, EvalCase, Scorecard, run_eval
from .core.execution_context import ExecutionContext
from .core.model_router import ModelCapabilities, ModelRequest, ModelRouter, apply_route
from .core.registries import EvalRegistry, PolicyRegistry, PromptRegistry
from .core.result import RunResult
from .core.run import Run, RunStatus
from .core.tool_decorator import tool

__version__ = "0.1.0"

__all__ = [
    "Agent", "Workflow", "ExecutionContext", "RunResult", "tool",
    "AgentSpec", "build_agent",
    "Model", "Message",
    "PromptRegistry", "PolicyRegistry", "EvalRegistry",
    "ModelRouter", "ModelRequest", "ModelCapabilities", "apply_route",
    "Run", "RunStatus",
    "run_eval", "EvalCase", "Scorecard", "CaseResult",
    "__version__",
]
