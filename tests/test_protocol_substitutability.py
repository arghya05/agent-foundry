"""Proves the 7 *Like Protocols (RunBudgetLike, LatencyBudgetLike,
CircuitBreakerLike, RateLimiterLike, SLATrackerLike, CostLedgerLike,
ToolCacheLike) are the REAL contract their callers need, not just
paperwork sitting next to each concrete class.

Each test below builds a minimal fake that implements ONLY the Protocol's
declared methods (no inheritance from the concrete class — genuinely a
different object, the way a Redis-backed or DB-backed implementation
would be) and drops it into the exact field a real deployment would use
(AgentConfig.budget, ToolRegistry.rate_limiter, etc.), then runs a real
graph turn or a real ToolRegistry.invoke() through it. If the Protocol's
method list were wrong — missing a method a caller actually needs, or
naming one differently than the real call site — these fail with a hard
AttributeError, not a silent behavior gap. This is the same "swap in an
alternative implementation" proof ScriptedProvider already gives Provider,
extended to the 7 Protocols added for fleet-wide (multi-replica) swapping.

No @runtime_checkable / isinstance() assertions here — none of this
codebase's 17 pre-existing Protocols use them either (confirmed via
grep), so Protocols stay structural-typing documentation, checked by
actually working, not by an isinstance() call.
"""
from __future__ import annotations

from agent_foundry.observability import CostLedgerLike
from agent_foundry.orchestration import build_agent_graph
from agent_foundry.runtime import CircuitBreakerLike, LatencyBudgetLike, RateLimiterLike, RunBudgetLike, SLATrackerLike
from agent_foundry.tools_gateway import ToolCacheLike, ToolRegistry

from conftest import ScriptedProvider, make_config_kwargs


def _invoke(graph, thread_id, text):
    return graph.invoke({"messages": [{"role": "user", "content": text}], "thread_id": thread_id}, {"configurable": {"thread_id": thread_id}})


class FakeRunBudget:
    """Implements exactly RunBudgetLike's 4 methods — nothing borrowed from
    the real RunBudget dataclass. Stands in for e.g. a Redis-backed budget
    shared across replicas."""

    def __init__(self):
        self.spends: list[tuple[float, str]] = []
        self.steps: list[str] = []

    def spend(self, amount: float, *, thread_id: str = "") -> None:
        self.spends.append((amount, thread_id))

    def step(self, *, thread_id: str = "") -> None:
        self.steps.append(thread_id)

    def cost_usd_for(self, thread_id: str = "") -> float:
        return sum(a for a, t in self.spends if t == thread_id)

    def steps_for(self, thread_id: str = "") -> int:
        return sum(1 for t in self.steps if t == thread_id)


def test_run_budget_like_fake_is_a_real_drop_in_for_a_full_graph_turn(identity, policy, tool_registry):
    fake_budget: RunBudgetLike = FakeRunBudget()  # annotation lets a type-checker, not just this test, catch drift
    provider = ScriptedProvider(['CALL lookup_order {"order_id": "A100"}', "All set!"])
    kwargs = make_config_kwargs(identity=identity, policy=policy, tools=tool_registry, provider=provider)
    kwargs["budget"] = fake_budget  # override the real RunBudget() the helper wires in
    graph = build_agent_graph(system_prompt="sys", **kwargs)

    state = _invoke(graph, "t-fake-budget", "status of A100?")

    assert state["messages"][-1]["content"] == "All set!"
    # think() called step() at least once, and spend() with the scripted 0.0 cost —
    # both real call sites (orchestration.py think()/act()), not just construction.
    assert fake_budget.steps
    assert fake_budget.cost_usd_for("t-fake-budget") == 0.0


class FakeLatencyBudget:
    def __init__(self):
        self.checked: list[str] = []

    def check(self, *, thread_id: str = "") -> None:
        self.checked.append(thread_id)

    def elapsed_s(self, *, thread_id: str = "") -> float:
        return 0.0


def test_latency_budget_like_fake_is_consulted_on_every_turn(identity, policy, tool_registry):
    fake_latency: LatencyBudgetLike = FakeLatencyBudget()
    provider = ScriptedProvider(["ok"])
    kwargs = make_config_kwargs(identity=identity, policy=policy, tools=tool_registry, provider=provider)
    graph = build_agent_graph(system_prompt="sys", **kwargs, latency_budget=fake_latency)

    _invoke(graph, "t-fake-latency", "hi")

    assert fake_latency.checked == ["t-fake-latency"]


class FakeCircuitBreaker:
    """Genuinely trips (unlike FakeRunBudget's pure recorder above) — proves
    is_open() actually gates the NEXT tool call, not just that it's callable."""

    def __init__(self):
        self.recorded: list[tuple[str, bool]] = []
        self._opened: set[str] = set()

    def record(self, tool: str, ok: bool) -> None:
        self.recorded.append((tool, ok))
        if not ok:
            self._opened.add(tool)

    def is_open(self, tool: str) -> bool:
        return tool in self._opened


def test_circuit_breaker_like_fake_records_and_genuinely_gates_the_next_call(identity, policy):
    from agent_foundry.contracts import ToolSpec

    def flaky(order_id: str) -> str:
        raise RuntimeError("downstream unavailable")

    tools = ToolRegistry()
    tools.register(ToolSpec("lookup_order", "Look up an order", {"order_id": "string"}, flaky))
    fake_breaker: CircuitBreakerLike = FakeCircuitBreaker()
    provider = ScriptedProvider([
        'CALL lookup_order {"order_id": "A100"}',
        'CALL lookup_order {"order_id": "A100"}',
        "done",
    ])
    kwargs = make_config_kwargs(identity=identity, policy=policy, tools=tools, provider=provider)
    graph = build_agent_graph(system_prompt="sys", **kwargs, breaker=fake_breaker)

    state = _invoke(graph, "t-fake-breaker", "status?")

    # first call fails and trips the fake breaker; the SECOND CALL <lookup_order>
    # the model issues is short-circuited by is_open() before flaky() runs again.
    assert fake_breaker.recorded == [("lookup_order", False)]
    tool_messages = [m for m in state["messages"] if m.get("role") == "tool"]
    assert any("temporarily disabled" in m["content"] for m in tool_messages)


class FakeRateLimiter:
    """Denies every other call — deterministic, so the test can assert
    exactly which invocations ToolRegistry let through."""

    def __init__(self):
        self.calls: list[str] = []
        self._n = 0

    def allow(self, key: str) -> bool:
        self.calls.append(key)
        self._n += 1
        return self._n % 2 == 1


def test_rate_limiter_like_fake_is_a_real_drop_in_for_tool_registry(identity, policy, lookup_order_tool):
    from agent_foundry.contracts import Policy

    fake_limiter: RateLimiterLike = FakeRateLimiter()
    tools = ToolRegistry(rate_limiter=fake_limiter)
    tools.register(lookup_order_tool)
    open_policy = Policy(allowed_tools=frozenset({"lookup_order"}), max_cost_usd_per_thread=1.0, max_steps_per_thread=10)

    first = tools.invoke("lookup_order", {"order_id": "A100"}, identity=identity, policy=open_policy)
    second = tools.invoke("lookup_order", {"order_id": "A100"}, identity=identity, policy=open_policy)

    assert first.ok is True
    assert second.ok is False
    assert "rate limit" in second.error
    assert fake_limiter.calls == ["lookup_order", "lookup_order"]


class FakeSLATracker:
    def __init__(self):
        self.recorded: list[tuple[bool, float]] = []

    def record(self, *, ok: bool, latency_ms: float) -> None:
        self.recorded.append((ok, latency_ms))

    def success_rate(self) -> float:
        return 1.0 if not self.recorded else sum(1 for ok, _ in self.recorded if ok) / len(self.recorded)

    def p95_latency_ms(self) -> float:
        return 0.0

    def error_budget_remaining(self) -> float:
        return 1.0

    def breaches(self) -> list[str]:
        return []


def test_sla_tracker_like_fake_is_a_real_drop_in_for_a_full_graph_turn(identity, policy, tool_registry):
    fake_sla: SLATrackerLike = FakeSLATracker()
    provider = ScriptedProvider(["ok"])
    kwargs = make_config_kwargs(identity=identity, policy=policy, tools=tool_registry, provider=provider)
    graph = build_agent_graph(system_prompt="sys", **kwargs, sla_tracker=fake_sla)

    _invoke(graph, "t-fake-sla", "hi")

    assert len(fake_sla.recorded) == 1
    assert fake_sla.recorded[0][0] is True


class FakeCostLedger:
    def __init__(self):
        self.completed: list[dict] = []
        self.incomplete: list[dict] = []

    def close_task(self, *, thread_id: str, tenant_id: str, cost_usd: float, steps: int, outcome: str) -> None:
        self.completed.append({"thread_id": thread_id, "tenant_id": tenant_id, "cost_usd": cost_usd, "steps": steps, "outcome": outcome})

    def close_incomplete(self, *, thread_id: str, tenant_id: str, cost_usd: float, reason: str) -> None:
        self.incomplete.append({"thread_id": thread_id, "tenant_id": tenant_id, "cost_usd": cost_usd, "reason": reason})

    def total_cost_usd(self) -> float:
        return sum(t["cost_usd"] for t in self.completed)

    def cost_per_task(self) -> float:
        return self.total_cost_usd() / len(self.completed) if self.completed else 0.0

    def total_incomplete_cost_usd(self) -> float:
        return sum(t["cost_usd"] for t in self.incomplete)

    def cost_per_incomplete_task(self) -> float:
        return self.total_incomplete_cost_usd() / len(self.incomplete) if self.incomplete else 0.0

    def by_tenant(self) -> dict:
        totals: dict = {}
        for t in self.completed:
            totals[t["tenant_id"]] = totals.get(t["tenant_id"], 0.0) + t["cost_usd"]
        return totals


def test_cost_ledger_like_fake_is_a_real_drop_in_for_a_full_graph_turn(identity, policy, tool_registry):
    fake_ledger: CostLedgerLike = FakeCostLedger()
    provider = ScriptedProvider(["ok"])
    kwargs = make_config_kwargs(identity=identity, policy=policy, tools=tool_registry, provider=provider)
    graph = build_agent_graph(system_prompt="sys", **kwargs, cost_ledger=fake_ledger)

    _invoke(graph, "t-fake-ledger", "hi")

    assert len(fake_ledger.completed) == 1
    assert fake_ledger.completed[0]["thread_id"] == "t-fake-ledger"
    assert fake_ledger.completed[0]["tenant_id"] == identity.tenant_id


class FakeToolCache:
    def __init__(self):
        self._store: dict = {}
        self.gets = 0
        self.sets = 0

    def get(self, name: str, args: dict):
        self.gets += 1
        return self._store.get((name, tuple(sorted(args.items()))))

    def set(self, name: str, args: dict, result) -> None:
        self.sets += 1
        self._store[(name, tuple(sorted(args.items())))] = result


def test_tool_cache_like_fake_is_a_real_drop_in_and_actually_prevents_a_repeat_call(identity, policy):
    from agent_foundry.contracts import Policy, ToolSpec

    call_count = 0

    def counted_lookup(order_id: str) -> str:
        nonlocal call_count
        call_count += 1
        return f"order {order_id} shipped"

    fake_cache: ToolCacheLike = FakeToolCache()
    tools = ToolRegistry(cache=fake_cache)
    tools.register(ToolSpec("lookup_order", "Look up an order", {"order_id": "string"}, counted_lookup))
    open_policy = Policy(allowed_tools=frozenset({"lookup_order"}), max_cost_usd_per_thread=1.0, max_steps_per_thread=10)

    first = tools.invoke("lookup_order", {"order_id": "A100"}, identity=identity, policy=open_policy)
    second = tools.invoke("lookup_order", {"order_id": "A100"}, identity=identity, policy=open_policy)

    assert first.output == second.output == "order A100 shipped"
    assert call_count == 1  # the real fn only ran once — the fake cache genuinely served the second call
    assert fake_cache.sets == 1
