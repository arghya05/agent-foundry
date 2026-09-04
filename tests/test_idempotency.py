from agent_foundry.contracts import Identity, Policy, ToolSpec
from agent_foundry.tools_gateway import InMemoryIdempotencyStore, ToolRegistry


def test_repeated_call_with_same_key_executes_only_once():
    calls = []

    def issue_refund(order_id: str, amount_usd: float) -> str:
        calls.append((order_id, amount_usd))
        return f"refunded ${amount_usd} for {order_id}"

    registry = ToolRegistry(idempotency_store=InMemoryIdempotencyStore())
    registry.register(ToolSpec("issue_refund", "refund", {"order_id": "string", "amount_usd": "number"}, issue_refund))
    identity = Identity(id="t", tenant_id="acme")
    policy = Policy(allowed_tools=frozenset({"issue_refund"}))

    r1 = registry.invoke("issue_refund", {"order_id": "A100", "amount_usd": 20}, identity=identity, policy=policy, idempotency_key="refund-A100")
    r2 = registry.invoke("issue_refund", {"order_id": "A100", "amount_usd": 20}, identity=identity, policy=policy, idempotency_key="refund-A100")

    assert len(calls) == 1  # the side effect happened exactly once
    assert r1.output == r2.output == "refunded $20 for A100"


def test_different_keys_execute_independently():
    calls = []

    def issue_refund(order_id: str) -> str:
        calls.append(order_id)
        return f"refunded {order_id}"

    registry = ToolRegistry(idempotency_store=InMemoryIdempotencyStore())
    registry.register(ToolSpec("issue_refund", "refund", {"order_id": "string"}, issue_refund))
    identity = Identity(id="t", tenant_id="acme")
    policy = Policy(allowed_tools=frozenset({"issue_refund"}))

    registry.invoke("issue_refund", {"order_id": "A100"}, identity=identity, policy=policy, idempotency_key="refund-A100")
    registry.invoke("issue_refund", {"order_id": "A101"}, identity=identity, policy=policy, idempotency_key="refund-A101")
    assert calls == ["A100", "A101"]


def test_no_idempotency_key_executes_every_time():
    calls = []

    def issue_refund(order_id: str) -> str:
        calls.append(order_id)
        return f"refunded {order_id}"

    registry = ToolRegistry(idempotency_store=InMemoryIdempotencyStore())
    registry.register(ToolSpec("issue_refund", "refund", {"order_id": "string"}, issue_refund))
    identity = Identity(id="t", tenant_id="acme")
    policy = Policy(allowed_tools=frozenset({"issue_refund"}))

    registry.invoke("issue_refund", {"order_id": "A100"}, identity=identity, policy=policy)
    registry.invoke("issue_refund", {"order_id": "A100"}, identity=identity, policy=policy)
    assert calls == ["A100", "A100"]


def test_failed_call_is_not_cached_and_can_be_retried():
    attempts = []

    def flaky(order_id: str) -> str:
        attempts.append(order_id)
        if len(attempts) == 1:
            raise RuntimeError("transient failure")
        return f"refunded {order_id}"

    registry = ToolRegistry(idempotency_store=InMemoryIdempotencyStore())
    registry.register(ToolSpec("flaky", "flaky refund", {"order_id": "string"}, flaky))
    identity = Identity(id="t", tenant_id="acme")
    policy = Policy(allowed_tools=frozenset({"flaky"}))

    r1 = registry.invoke("flaky", {"order_id": "A100"}, identity=identity, policy=policy, idempotency_key="refund-A100")
    r2 = registry.invoke("flaky", {"order_id": "A100"}, identity=identity, policy=policy, idempotency_key="refund-A100")

    assert not r1.ok
    assert r2.ok and r2.output == "refunded A100"
    assert len(attempts) == 2  # the failed attempt wasn't cached, so the retry actually ran
