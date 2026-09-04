import os
import subprocess
import sys
import tempfile

from agent_foundry.contracts import Identity, LLMResponse, Policy, ToolSpec
from agent_foundry.eval import EvalHarness
from agent_foundry.guardrails import GuardrailEngine
from agent_foundry.llm_gateway import LLMGateway
from agent_foundry.observability import Tracer
from agent_foundry.orchestration import build_agent_graph
from agent_foundry.runtime import RunBudget
from agent_foundry.tools_gateway import ToolRegistry
from agent_foundry.benchmark import BenchmarkCase, run_benchmark


def test_run_benchmark_differentiates_pass_and_fail():
    orders = {"A100": "shipped", "A101": "processing"}

    class Provider:
        def complete(self, messages, *, model, tools=None, **kw):
            last = messages[-1]
            if last["role"] == "user":
                for oid in orders:
                    if oid in str(last["content"]):
                        return LLMResponse(text=f'CALL lookup_order {{"order_id": "{oid}"}}', model=model, input_tokens=1, output_tokens=1, cost_usd=0.0)
            return LLMResponse(text=f"Status: {last['content']}", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0)

    identity = Identity(id="t", tenant_id="acme")
    policy = Policy(allowed_tools=frozenset({"lookup_order"}))
    tools = ToolRegistry()
    tools.register(ToolSpec("lookup_order", "x", {"order_id": "string"}, lambda order_id: orders.get(order_id, "unknown")))
    graph = build_agent_graph(system_prompt="sys", llm=LLMGateway(provider=Provider()), tools=tools,
        guardrails=GuardrailEngine(policy), eval_harness=EvalHarness(), identity=identity, policy=policy,
        budget=RunBudget(policy), tracer=Tracer("bench-test"))

    cases = [
        BenchmarkCase("shipped", "status of A100?", check=lambda r: "shipped" in r.lower()),
        BenchmarkCase("wrong_expectation", "status of A100?", check=lambda r: "delivered" in r.lower()),
    ]
    report = run_benchmark(graph, cases)
    assert report.results[0].passed and not report.results[1].passed
    assert abs(report.pass_rate - 0.5) < 1e-9


def test_scaffold_generates_valid_runnable_files():
    from agent_foundry.scaffold import create_agent

    with tempfile.TemporaryDirectory() as tmp:
        paths = create_agent("sales_agent", directory=tmp, tools=["lookup_lead", "send_email"])
        assert os.path.exists(paths["prompt"]) and os.path.exists(paths["agent"])

        prompt_text = open(paths["prompt"]).read()
        assert "lookup_lead" in prompt_text and "send_email" in prompt_text

        # the generated file must actually compile as valid Python
        result = subprocess.run([sys.executable, "-m", "py_compile", paths["agent"]], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

        # and it must not reference LangGraph/LangChain internals anywhere
        agent_source = open(paths["agent"]).read()
        for forbidden in ("langgraph", "langchain", "StateGraph", "Command("):
            assert forbidden not in agent_source, f"scaffold leaked {forbidden!r} into developer-facing code"
