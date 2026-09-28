"""Live AgentDojo benchmark: a model with and without Foundry's injection gate.

    python benchmarks/agentdojo_live.py --provider openai --model gpt-5.4-mini \
        --defense none|foundry --attack important_instructions --max-calls 4000 \
        --out review/evidence/agentdojo-live-<date>/<run-name>

Keys are read from the environment or from agent-foundry/.env (git-ignored):
OPENAI_API_KEY, ANTHROPIC_API_KEY (and ANTHROPIC_WORKSPACE_ID if the key
requires the anthropic-workspace-id header). The "foundry" defense inserts a
PromptInjectionDetector in AgentDojo's standard detector position (after tool
execution, before the next model call), using Foundry's marker list OR the
naive-Bayes gate trained only on deepset/prompt-injections train. Utility
(clean), utility under attack and targeted attack success come from
AgentDojo's own task checks; nothing is rescored here. A hard cap on model
calls aborts the run; completed tasks remain in AgentDojo's per-task logs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "benchmarks")]


def load_env() -> None:
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["openai", "anthropic"], required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--defense", choices=["none", "foundry"], default="none")
    ap.add_argument("--attack", default="important_instructions")
    ap.add_argument("--suites", default="workspace,travel,banking,slack")
    ap.add_argument("--max-calls", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    load_env()
    if args.out.exists():
        raise SystemExit(f"{args.out} exists; choose a new directory")
    args.out.mkdir(parents=True)

    import anthropic
    import openai
    from agentdojo.agent_pipeline import (AgentPipeline, InitQuery, PromptInjectionDetector, SystemMessage,
                                          ToolsExecutionLoop, ToolsExecutor)
    from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
    from agentdojo.agent_pipeline.llms.anthropic_llm import AnthropicLLM
    from agentdojo.agent_pipeline.llms.openai_llm import OpenAILLM
    from agentdojo.attacks.attack_registry import load_attack
    from agentdojo.benchmark import benchmark_suite_with_injections, benchmark_suite_without_injections
    from agentdojo.task_suite.load_suites import get_suites
    from agent_foundry.guardrails import looks_like_injection
    from agent_foundry.injection_classifier import InjectionClassifier
    from security_hallucination import fetch

    if args.provider == "openai":
        llm = OpenAILLM(openai.OpenAI(), args.model)
        persona = "openai-compatible"
    else:
        headers = {"anthropic-workspace-id": os.environ["ANTHROPIC_WORKSPACE_ID"]} \
            if os.environ.get("ANTHROPIC_WORKSPACE_ID") else None
        llm = AnthropicLLM(anthropic.Anthropic(default_headers=headers), args.model)
        persona = "claude-3-5-sonnet-20241022"  # only selects the attack's model-name wording ("Claude")

    class CallCap(BasePipelineElement):
        """Counts model calls; aborts the run at the cap (budget guard)."""
        calls = 0

        def __init__(self, inner):
            self.inner, self.name = inner, getattr(inner, "name", None)

        def query(self, *a, **k):
            CallCap.calls += 1
            if CallCap.calls > args.max_calls:
                raise SystemExit(f"model-call cap {args.max_calls} reached")
            return self.inner.query(*a, **k)

    capped = CallCap(llm)
    stages = [ToolsExecutor()]
    if args.defense == "foundry":
        train, _ = fetch("deepset/prompt-injections", "default", "train")
        nb = InjectionClassifier().fit([r["text"] for r in train], [int(r["label"]) for r in train])

        class FoundryDetector(PromptInjectionDetector):
            def detect(self, tool_output: str):
                return looks_like_injection(tool_output) or nb(tool_output)

        stages.append(FoundryDetector(mode="message", raise_on_injection=False))
    from agentdojo.agent_pipeline.agent_pipeline import load_system_message
    pipeline = AgentPipeline([SystemMessage(load_system_message(None)), InitQuery(), capped,
                              ToolsExecutionLoop([*stages, capped])])
    pipeline.name = persona
    results = {}
    for name, suite in get_suites("v1.2.2").items():
        if name not in args.suites.split(","):
            continue
        clean = benchmark_suite_without_injections(pipeline, suite, logdir=args.out / "logs", force_rerun=False,
                                                   benchmark_version="v1.2.2")
        attacked = benchmark_suite_with_injections(pipeline, suite, load_attack(args.attack, suite, pipeline),
                                                   logdir=args.out / "logs", force_rerun=False,
                                                   benchmark_version="v1.2.2")
        u = clean["utility_results"]
        ua = attacked["utility_results"]
        sa = attacked["security_results"]
        results[name] = {"utility": sum(u.values()) / len(u), "n_clean": len(u),
                         "utility_under_attack": sum(ua.values()) / len(ua),
                         "attack_success_rate": sum(sa.values()) / len(sa), "n_attack": len(sa)}
        (args.out / "results.json").write_text(json.dumps({"args": {k: str(v) for k, v in vars(args).items()},
                                                           "model_calls": CallCap.calls, "suites": results}, indent=1))
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
