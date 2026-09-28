"""Offline AgentDojo evaluation of Foundry's tool-output injection detectors.

No model is called. For every user task in the AgentDojo v1.2.2 suites, the
task's ground-truth tool calls are replayed (AgentDojo's GroundTruthPipeline)
in (i) the clean default environment, giving negative tool outputs, and (ii)
an environment injected by each registered baseline attack for each
injection task, giving positive outputs (tool outputs that contain the
injected text). Detectors are applied to every tool output:
  marker   - guardrails.looks_like_injection (Foundry default)
  nb       - injection_classifier trained ONLY on deepset/prompt-injections
             train (out-of-domain for AgentDojo)
  combined - marker OR nb
This measures detection of injected content, not attack success or task
utility, which require a model in the loop.

Run with a Python that has agentdojo and pyarrow installed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement  # noqa: E402
from agentdojo.agent_pipeline.ground_truth_pipeline import GroundTruthPipeline  # noqa: E402
from agentdojo.attacks.attack_registry import load_attack  # noqa: E402
from agentdojo.functions_runtime import FunctionsRuntime  # noqa: E402
from agentdojo.task_suite.load_suites import get_suites  # noqa: E402
from agentdojo.types import get_text_content_as_str  # noqa: E402

from agent_foundry.guardrails import looks_like_injection  # noqa: E402
from agent_foundry.injection_classifier import InjectionClassifier  # noqa: E402
from security_hallucination import binary_metrics, fetch  # noqa: E402

VERSION = "v1.2.2"
ATTACKS = ["direct", "ignore_previous", "system_message", "injecagent", "important_instructions_no_names"]


class _Target(BasePipelineElement):
    name = "local"  # AgentDojo model key; renders as "Local model" in attack text

    def query(self, *a, **k):  # pragma: no cover - attacks only read .name
        raise NotImplementedError


def tool_outputs(suite, user_task, injections: dict[str, str]) -> list[str]:
    env = suite.load_and_inject_default_environment(injections)
    runtime = FunctionsRuntime(suite.tools)
    _, _, _, messages, _ = GroundTruthPipeline(user_task).query(user_task.PROMPT, runtime, env)
    return [get_text_content_as_str(m["content"]) or "" for m in messages if m["role"] == "tool"]


def main() -> None:
    train, _ = fetch("deepset/prompt-injections", "default", "train")
    nb = InjectionClassifier().fit([r["text"] for r in train], [int(r["label"]) for r in train])
    detectors = {"marker": looks_like_injection, "nb": nb,
                 "combined": lambda t: looks_like_injection(t) or nb(t)}
    labels: list[int] = []
    preds: dict[str, list[int]] = {k: [] for k in detectors}
    per_attack: dict[str, dict] = {}
    per_suite: dict[str, dict] = {}
    seen_clean: set[tuple] = set()
    counts = {"user_tasks": 0, "attack_cases": 0}
    for suite_name, suite in get_suites(VERSION).items():
        target = _Target()
        s_labels, s_preds = [], {k: [] for k in detectors}
        for ut_id, user_task in suite.user_tasks.items():
            counts["user_tasks"] += 1
            clean = tool_outputs(suite, user_task, {})
            for text in clean:
                key = (suite_name, ut_id, hash(text))
                if key in seen_clean:
                    continue
                seen_clean.add(key)
                labels.append(0)
                s_labels.append(0)
                for k, d in detectors.items():
                    preds[k].append(int(d(text)))
                    s_preds[k].append(preds[k][-1])
            for attack_name in ATTACKS:
                attack = load_attack(attack_name, suite, target)
                a = per_attack.setdefault(attack_name, {"labels": [], **{k: [] for k in detectors}})
                for it_id, injection_task in suite.injection_tasks.items():
                    injections = attack.attack(user_task, injection_task)
                    if not injections:
                        continue
                    counts["attack_cases"] += 1
                    # Tool outputs are re-serialized (YAML), so match on a whitespace-
                    # normalized prefix of the attacker goal rather than raw payload bytes.
                    goal = " ".join(injection_task.GOAL.split())[:60]
                    for text in tool_outputs(suite, user_task, injections):
                        if goal not in " ".join(text.split()):
                            continue  # output unaffected by the injection; negatives come from clean runs
                        labels.append(1)
                        s_labels.append(1)
                        a["labels"].append(1)
                        for k, d in detectors.items():
                            v = int(d(text))
                            preds[k].append(v)
                            s_preds[k].append(v)
                            a[k].append(v)
        per_suite[suite_name] = {k: binary_metrics(s_labels, s_preds[k]) for k in detectors}
    out = {"agentdojo_version": VERSION, "attacks": ATTACKS, **counts,
           "overall": {k: binary_metrics(labels, preds[k]) for k in detectors},
           "per_suite": per_suite,
           "recall_per_attack": {n: {k: sum(v[k]) / len(v[k]) if v[k] else None for k in detectors}
                                 for n, v in per_attack.items()}}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
