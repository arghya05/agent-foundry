"""core.protocols.StateStore + core.state_store.MemoryStateStore — a
standalone, real durable-state seam (not yet wired into NativeEngine's own
in-process storage; see both modules' docstrings for the scope boundary and
why). These tests prove the Protocol is a real, satisfiable seam and that
the reference implementation behaves correctly on its own, the same way
test_workflow_engine_protocol.py proved WorkflowEngine was real before
Agent ever routed through it."""
from __future__ import annotations

from agent_foundry.core.protocols import StateStore
from agent_foundry.core.state_store import MemoryStateStore


def test_memory_state_store_satisfies_the_protocol():
    assert isinstance(MemoryStateStore(), StateStore)


def test_load_on_an_unknown_run_id_returns_none():
    store = MemoryStateStore()
    assert store.load("no-such-run") is None


def test_save_then_load_round_trips():
    store = MemoryStateStore()
    store.save("run-1", {"messages": [{"role": "user", "content": "hi"}]})
    assert store.load("run-1") == {"messages": [{"role": "user", "content": "hi"}]}


def test_save_overwrites_a_previous_save_for_the_same_run_id():
    store = MemoryStateStore()
    store.save("run-1", {"messages": ["first"]})
    store.save("run-1", {"messages": ["second"]})
    assert store.load("run-1") == {"messages": ["second"]}


def test_delete_removes_the_state():
    store = MemoryStateStore()
    store.save("run-1", {"messages": []})
    store.delete("run-1")
    assert store.load("run-1") is None


def test_delete_on_an_unknown_run_id_is_a_no_op_not_an_error():
    MemoryStateStore().delete("no-such-run")  # must not raise


def test_load_returns_a_copy_not_a_live_reference():
    """A caller mutating what load() returned must not corrupt the store's
    own state — the same "load then mutate freely" guarantee a real
    Redis/Postgres-backed store would have for free (deserializing gives you
    a new object every time), which this in-process reference implementation
    has to provide deliberately via a copy."""
    store = MemoryStateStore()
    store.save("run-1", {"messages": ["original"]})

    loaded = store.load("run-1")
    assert loaded is not None
    loaded["messages"].append("mutated by caller")

    assert store.load("run-1") == {"messages": ["original"]}


def test_save_copies_its_input_not_a_live_reference():
    """The other half of the copy guarantee: mutating the dict AFTER passing
    it to save() must not reach into the store either."""
    store = MemoryStateStore()
    state = {"messages": ["original"]}

    store.save("run-1", state)
    state["messages"].append("mutated by caller after save")

    assert store.load("run-1") == {"messages": ["original"]}


def test_different_run_ids_are_independent():
    store = MemoryStateStore()
    store.save("run-1", {"messages": ["a"]})
    store.save("run-2", {"messages": ["b"]})
    assert store.load("run-1") == {"messages": ["a"]}
    assert store.load("run-2") == {"messages": ["b"]}
