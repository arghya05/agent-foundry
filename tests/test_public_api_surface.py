"""A lightweight API-compatibility check — not a full signature-diffing
tool, just enough to catch an accidental rename/removal in review before it
reaches a release. Enumerates agent_foundry.__all__ and pins the essential
method surface of the classes most external code actually calls."""
from __future__ import annotations

import agent_foundry


def test_every_name_in_all_is_importable_and_not_none():
    for name in agent_foundry.__all__:
        assert hasattr(agent_foundry, name), f"{name!r} is in __all__ but not actually importable from agent_foundry"
        assert getattr(agent_foundry, name) is not None, f"{name!r} resolved to None"


def test_all_has_no_duplicates():
    assert len(agent_foundry.__all__) == len(set(agent_foundry.__all__))


def test_agent_essential_method_surface():
    for method in ("run", "arun", "stream", "astream", "resume", "aresume", "start", "batch", "as_tool", "serve", "on"):
        assert hasattr(agent_foundry.Agent, method), f"Agent.{method} is missing"


def test_workflow_essential_factory_surface():
    for factory in ("supervisor", "swarm", "blackboard", "debate", "fanout", "dag"):
        assert hasattr(agent_foundry.Workflow, factory), f"Workflow.{factory} is missing"


def test_agentspec_essential_method_surface():
    for method in ("from_dict", "from_json", "from_yaml"):
        assert hasattr(agent_foundry.AgentSpec, method), f"AgentSpec.{method} is missing"


def test_run_essential_method_surface():
    for method in ("run", "resume", "pause", "unpause", "cancel", "retry", "fork", "replay", "wait_for_event"):
        assert hasattr(agent_foundry.Run, method), f"Run.{method} is missing"


def test_run_status_has_every_documented_state():
    for state in ("STARTED", "RUNNING", "WAITING_TOOL", "WAITING_HUMAN", "WAITING_EVENT", "SUSPENDED", "COMPLETED", "FAILED", "CANCELLED"):
        assert hasattr(agent_foundry.RunStatus, state), f"RunStatus.{state} is missing"


def test_evalcase_and_scorecard_essential_surface():
    for field in ("input", "expected_substring", "expected_tool", "kpi", "expected_tool_sequence", "forbidden_tools", "must_request_approval"):
        assert field in agent_foundry.EvalCase.__dataclass_fields__, f"EvalCase.{field} is missing"
    for method in ("passes", "compare_to", "render"):
        assert hasattr(agent_foundry.Scorecard, method), f"Scorecard.{method} is missing"
