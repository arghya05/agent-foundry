"""State consistency checks; these do not establish exactly-once tool effects."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import sys
import threading

import pytest

from agent_foundry import Agent, ExecutionContext
from agent_foundry.core.state_store import MemoryStateStore
from agent_foundry.llm_gateway import LLMGateway
from conftest import ScriptedProvider


def agent(store, replies):
    return Agent("bot", "chat", runtime="native", state_store=store,
                 llm=LLMGateway(provider=ScriptedProvider(replies)))


def test_alternating_workers_keep_every_completed_turn():
    store = MemoryStateStore()
    first, second = agent(store, ["one", "three"]), agent(store, ["two"])
    context = ExecutionContext(thread_id="shared")
    first.run("a", context=context)
    second.run("b", context=context)
    result = first.run("c", context=context)
    assert [m["content"] for m in result.messages] == ["a", "one", "b", "two", "c", "three"]


def test_graph_updates_survive_a_fresh_engine_without_an_extra_turn():
    store = MemoryStateStore()
    first, second = agent(store, []), agent(store, [])
    config = {"configurable": {"thread_id": "seeded"}}
    first.graph.update_state(config, {"messages": [{"role": "user", "content": "seed"}]})
    assert second.graph.get_state(config).values["messages"] == [{"role": "user", "content": "seed"}]


def test_snapshots_cannot_mutate_engine_state():
    first = agent(None, ["answer"])
    context = ExecutionContext(thread_id="snapshot")
    result = first.run("question", context=context)
    config = {"configurable": {"thread_id": "snapshot"}}
    snapshot = first.graph.get_state(config)
    snapshot.values["messages"][0]["content"] = "tampered"
    result.messages[-1]["content"] = "also tampered"
    assert [m["content"] for m in first.graph.get_state(config).values["messages"]] == ["question", "answer"]


def test_stream_chunks_cannot_modify_the_next_step_input():
    from agent_foundry.core.native_engine import NativeEngine

    engine = NativeEngine()
    config = agent(None, ["answer"]).config
    chunks = engine.stream_run(config, "question", thread_id="stream")
    first = next(chunks)
    first["messages"][0]["content"] = "injected by consumer"
    final = list(chunks)[-1]
    assert final["messages"][0]["content"] == "question"


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        return MemoryStateStore()
    from agent_foundry.core.state_store import SQLiteStateStore
    return SQLiteStateStore(tmp_path / "state.sqlite3")


def test_versioned_contract_and_deleted_state_never_reuse_versions(store):
    from agent_foundry.core.protocols import VersionedStateStore
    from agent_foundry.core.state_store import StateConflict

    assert isinstance(store, VersionedStateStore)
    state, version = store.load_versioned("r")
    assert state is None and version == 0
    v1 = store.compare_and_swap("r", {"messages": ["first"]}, expected_version=version)
    with pytest.raises(StateConflict):
        store.compare_and_swap("r", {"messages": ["stale"]}, expected_version=version)
    store.delete("r")
    state, deleted_version = store.load_versioned("r")
    assert state is None and deleted_version > v1
    store.save("r", {"messages": ["new"]})
    with pytest.raises(StateConflict):
        store.compare_and_swap("r", {"messages": ["old"]}, expected_version=v1)
    assert store.load("r") == {"messages": ["new"]}


def test_versioned_reads_and_writes_copy_nested_values(store):
    value = {"messages": [{"content": "original"}]}
    store.compare_and_swap("r", value, expected_version=0)
    value["messages"][0]["content"] = "changed"
    loaded, _ = store.load_versioned("r")
    loaded["messages"][0]["content"] = "also changed"
    assert store.load("r") == {"messages": [{"content": "original"}]}


def test_racing_engines_reject_the_losing_state_write(store):
    from agent_foundry.core.state_store import StateConflict

    barrier = threading.Barrier(2)

    def answer(messages, model):
        barrier.wait(timeout=5)
        return "answer"

    workers = [agent(store, [answer]), agent(store, [answer])]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker.run, name, context=ExecutionContext(thread_id="race"))
                   for worker, name in zip(workers, ["one", "two"])]
        errors = [future.exception(timeout=10) for future in futures]
    assert sum(error is None for error in errors) == 1
    assert sum(isinstance(error, StateConflict) for error in errors) == 1
    assert len(store.load("race")["messages"]) == 2


def test_conflict_before_tool_dispatch_does_not_execute_the_losing_call(store):
    from agent_foundry.contracts import AutonomyLevel, LLMResponse, Policy, ToolCall, ToolSpec
    from agent_foundry.core.state_store import StateConflict

    barrier = threading.Barrier(2)
    effects = []

    def action(messages, model):
        barrier.wait(timeout=5)
        return LLMResponse(text="", model=model, input_tokens=1, output_tokens=1, cost_usd=0,
                           tool_calls=[ToolCall(id="call", name="write", args={})])

    workers = [Agent("bot", "chat", runtime="native", state_store=store,
                     tools=[ToolSpec("write", "write", {}, lambda: effects.append("written") or "ok")],
                     policy=Policy(allowed_tools=frozenset({"write"}), autonomy=AutonomyLevel.L4_POLICY_BOUND),
                     llm=LLMGateway(provider=ScriptedProvider([action, "done"]))) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker.run, "act", context=ExecutionContext(thread_id="race")) for worker in workers]
        errors = [future.exception(timeout=10) for future in futures]
    assert sum(error is None for error in errors) == 1
    assert sum(isinstance(error, StateConflict) for error in errors) == 1
    assert effects == ["written"]


def test_sqlite_survives_process_exit_and_rejects_a_stale_process(tmp_path):
    from agent_foundry.core.state_store import SQLiteStateStore

    path = tmp_path / "durable.sqlite3"
    script = """
import json, os, sys
from agent_foundry.core.state_store import SQLiteStateStore, StateConflict
store = SQLiteStateStore(sys.argv[1])
if sys.argv[2] == 'write':
    store.compare_and_swap('r', {'messages': ['committed']}, expected_version=0)
    os._exit(0)
print(json.dumps(store.load_versioned('r')), flush=True)
input()
try:
    store.compare_and_swap('r', {'messages': ['stale']}, expected_version=1)
except StateConflict:
    sys.exit(23)
sys.exit(99)
"""
    subprocess.run([sys.executable, "-c", script, str(path), "write"], check=True, timeout=15)
    store = SQLiteStateStore(path)
    assert store.load("r") == {"messages": ["committed"]}
    process = subprocess.Popen([sys.executable, "-c", script, str(path), "stale"], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert json.loads(process.stdout.readline()) == [{"messages": ["committed"]}, 1]
        store.compare_and_swap("r", {"messages": ["newer"]}, expected_version=1)
        _, stderr = process.communicate("continue\n", timeout=15)
        assert process.returncode == 23, stderr
        assert store.load("r") == {"messages": ["newer"]}
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_update_state_rejects_changing_the_storage_key():
    first = agent(MemoryStateStore(), [])
    with pytest.raises(ValueError, match="thread_id"):
        first.graph.update_state({"configurable": {"thread_id": "one"}}, {"thread_id": "two"})


def test_sqlite_requires_a_file_not_an_ephemeral_connection():
    from agent_foundry.core.state_store import SQLiteStateStore

    with pytest.raises(ValueError, match="file"):
        SQLiteStateStore(":memory:")


def test_a_state_conflict_after_an_effect_is_reported_without_replaying(store):
    from agent_foundry.contracts import AutonomyLevel, LLMResponse, Policy, ToolCall, ToolSpec
    from agent_foundry.core.state_store import StateConflict

    effects = []

    def write():
        effects.append("external effect")
        # Another writer changes authoritative state while our tool is running.
        store.save("effect", {"messages": [], "thread_id": "effect", "external_owner": True})
        return "done"

    worker = Agent("bot", "chat", runtime="native", state_store=store,
                   tools=[ToolSpec("write", "write", {}, write)],
                   policy=Policy(allowed_tools=frozenset({"write"}), autonomy=AutonomyLevel.L4_POLICY_BOUND),
                   llm=LLMGateway(provider=ScriptedProvider([
                       LLMResponse(text="", model="m", input_tokens=1, output_tokens=1, cost_usd=0,
                                   tool_calls=[ToolCall(id="call", name="write", args={})]),
                   ])))
    with pytest.raises(StateConflict):
        worker.run("act", context=ExecutionContext(thread_id="effect"))
    assert effects == ["external effect"]
    assert store.load("effect")["external_owner"] is True


def test_a_committed_native_turn_survives_an_actual_process_exit(tmp_path):
    from agent_foundry.core.state_store import SQLiteStateStore

    path = tmp_path / "agent.sqlite3"
    script = """
import os, sys
from agent_foundry import Agent, ExecutionContext
from agent_foundry.contracts import LLMResponse
from agent_foundry.core.state_store import SQLiteStateStore
from agent_foundry.llm_gateway import LLMGateway
class Provider:
    def complete(self, messages, *, model, **kwargs):
        return LLMResponse(text='first answer', model=model, input_tokens=1, output_tokens=1, cost_usd=0)
agent = Agent('bot', 'chat', runtime='native', state_store=SQLiteStateStore(sys.argv[1]), llm=LLMGateway(provider=Provider()))
agent.run('first question', context=ExecutionContext(thread_id='restart'))
os._exit(0)
"""
    subprocess.run([sys.executable, "-c", script, str(path)], check=True, timeout=15)
    result = agent(SQLiteStateStore(path), ["second answer"]).run("second question", context=ExecutionContext(thread_id="restart"))
    assert [m["content"] for m in result.messages] == ["first question", "first answer", "second question", "second answer"]
