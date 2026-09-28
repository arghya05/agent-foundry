"""Benchmark-only adapters. Foundry application source is not modified."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import json
import threading
import time
from unittest.mock import patch

from common import system_prompt


def benchmark_tools():
    from src.evals.inference import get_toolkits
    return get_toolkits(["email", "calendar", "analytics", "project_management", "customer_relationship_manager"])


class Sandbox:
    """Explicitly bind one task's upstream state, including on worker threads."""

    def __init__(self, on_action=None):
        from src.tools import state
        self.state = state._pristine_state().copy()
        self.lock = threading.RLock()
        self.actions = []
        self.on_action = on_action or (lambda actions: None)

    def wrap(self, tool):
        def invoke(**kwargs):
            from src.evals.actions import convert_intermediate_step_to_function_call
            from src.tools import state
            # Upstream structured loop coerces arguments to strings before execution.
            arguments = {k: str(v) for k, v in kwargs.items()}
            with self.lock:
                previous = getattr(state._local, "tool_state", None)
                state._local.tool_state = self.state
                try:
                    self.actions.append(convert_intermediate_step_to_function_call(tool.name, arguments))
                    self.on_action(list(self.actions))
                    return tool(**arguments)
                finally:
                    if previous is None:
                        del state._local.tool_state
                    else:
                        state._local.tool_state = previous
        return replace(tool, func=invoke)


@contextmanager
def serial_foundry_dispatch():
    """Match upstream's ordered action semantics for shared mutable CSV state.

    This is a disclosed experiment setting, not a measurement of Foundry's
    default parallel dispatcher. All policy/validation/invocation gates remain.
    """
    import agent_foundry.orchestration as orchestration
    import agent_foundry.core.native_engine as native
    original = orchestration._dispatch_tool_calls

    def sequential(config, cleared, **kwargs):
        results = []
        for call in cleared:
            results.extend(original(config, [call], **kwargs))
        return results

    with patch.object(orchestration, "_dispatch_tool_calls", sequential), \
            patch.object(native, "_dispatch_tool_calls", sequential):
        yield


class FoundryProvider:
    def __init__(self, transport, *, on_response=None):
        self.transport = transport
        self.on_response = on_response

    def complete(self, messages, *, model, tools=None, **kwargs):
        from agent_foundry.contracts import LLMResponse, ToolCall
        if model != self.transport.model:
            raise ValueError("Unexpected model fallback")
        turns = []
        system = []
        for message in messages:
            role = message["role"]
            if role == "system":
                system.append(message["content"])
            elif role == "assistant" and message.get("tool_calls"):
                turns.append({"role": role, "content": message.get("content") or "", "tool_calls": [
                    {"id": call["id"], "type": "function", "function": {
                        "name": call["name"], "arguments": json.dumps(call["args"])}}
                    for call in message["tool_calls"]]})
            elif role == "tool":
                turns.append({"role": role, "tool_call_id": message["tool_call_id"],
                              "content": str(message["content"])})
            else:
                turns.append({"role": role, "content": str(message.get("content", ""))})
        response = self.transport.call("\n".join(system), turns,
                                       [{"type": "function", "function": t} for t in (tools or [])])
        if self.on_response is not None:
            self.on_response(response)
        return LLMResponse(text=response["content"], model=model,
                           input_tokens=response.get("input_tokens", 0),
                           output_tokens=response.get("output_tokens", 0),
                           cost_usd=response.get("cost_usd_upper", 0),
                           tool_calls=[ToolCall(id=t["id"], name=t["name"], args=t["arguments"])
                                       for t in response["tool_calls"]])


def run_task(arm, prompt, transport, *, max_calls=20, timeout_s=600, on_action=None, instruction_profile="baseline"):
    from src.evals import agent as upstream
    sandbox = Sandbox(on_action)
    tools = [sandbox.wrap(tool) for tool in benchmark_tools()]
    # Match the published all-tools structured runs. Synthetic benchmark tasks
    # have no human answering confirmation questions; runtime policy gates remain.
    prefix = system_prompt()
    if instruction_profile == "grounded":
        from agent_foundry.prompts import with_execution_guidance
        prefix = with_execution_guidance(prefix)
    elif instruction_profile != "baseline":
        raise ValueError("Unknown instruction profile")
    instructions = prefix + " " + upstream.ACT_WITHOUT_CONFIRMATION_SUFFIX.strip()
    allowed_names = {upstream._sanitize_tool_name(t.name) for t in tools}
    invalid_tool_calls = []

    def observe_response(response):
        # Unknown tools never reach Sandbox.wrap, but upstream intermediate_steps
        # retains them and the official scorer rejects that trajectory. Preserve
        # that penalty for every arm without replaying denied proposals as effects.
        for call in response["tool_calls"]:
            if upstream._sanitize_tool_name(call["name"]) not in allowed_names:
                invalid_tool_calls.append({"id": call["id"], "name": call["name"]})

    started = time.monotonic()
    output, error, messages = "", "", []
    try:
        if arm == "reference":
            # Keep the upstream agent loop; replace only routing and network transport
            # so every arm has the same model settings, accounting, and retry policy.
            def call(route, system, turns, schemas, temperature):
                response = transport.call(system, turns, schemas)
                observe_response(response)
                return response
            with patch.object(upstream, "resolve_route", return_value=None), \
                    patch.object(upstream, "_call_llm_structured_with_retry", call):
                result = upstream.run_agent_structured(transport.model, tools, prompt, prefix,
                                                       max_iterations=max_calls, max_execution_time=timeout_s,
                                                       act_without_confirmation=True)
            output = result.output
            if output == upstream.AGENT_STOPPED_MESSAGE:
                error = "IterationOrTimeLimit"
        else:
            from agent_foundry.contracts import AutonomyLevel, Policy, ToolSpec
            from agent_foundry.core.agent import Agent
            from agent_foundry.llm_gateway import LLMGateway
            from src.tools.toolkits import tools_with_side_effects
            side_effects = {t.name for t in tools_with_side_effects}
            schemas, _ = upstream._sanitized_tool_schemas(tools)
            specs = [ToolSpec(name=s["function"]["name"], description=t.description,
                              parameters=s["function"]["parameters"], fn=t.func,
                              destructive=t.name in side_effects, cacheable=False, max_retries=0)
                     for t, s in zip(tools, schemas)]
            policy = Policy(allowed_tools=frozenset(s.name for s in specs),
                            autonomy=AutonomyLevel.L4_POLICY_BOUND,
                            max_steps_per_thread=10000, max_cost_usd_per_thread=float("inf"))
            # Monetary and model-call budgets are enforced by the common transport.
            runtime = {"foundry_native_serial": "native", "foundry_langgraph_serial": "langgraph"}[arm]
            gateway = LLMGateway(FoundryProvider(transport, on_response=observe_response),
                                 routes={"default": [transport.model]})
            agent = Agent(name="workbench", instructions=instructions, tools=specs,
                          policy=policy, llm=gateway, runtime=runtime)
            with serial_foundry_dispatch():
                raw = agent.graph.invoke({"messages": [{"role": "user", "content": prompt}],
                                          "thread_id": "isolated-task"},
                                         {"configurable": {"thread_id": "isolated-task"},
                                          "recursion_limit": max_calls * 3 + 10})
            messages = raw.get("messages", [])
            output = str(messages[-1].get("content", "")) if messages else ""
            if raw.get("__interrupt__"):
                error = "UnexpectedApprovalInterrupt"
        if getattr(transport, "limit_error", None):
            error = transport.limit_error
        if invalid_tool_calls and not error:
            error = "UnknownToolCall"
    except Exception as exc:
        # Never serialize arbitrary exception messages: SDK errors can contain headers.
        cause = exc
        while cause.__cause__ is not None:
            cause = cause.__cause__
        error = type(cause).__name__
    return {"function_calls": list(sandbox.actions), "full_response": output, "error": error,
            "invalid_tool_calls": invalid_tool_calls,
            "messages": messages, "latency_s": time.monotonic() - started}
