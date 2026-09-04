"""Redis-backed implementations of the runtime/tools_gateway/observability
`*Like` Protocols — the real "any cloud, at scale" story, not just an
interface. RunBudget/RateLimiter/ToolCache/SLATracker/CostLedger are
correct and fast for one process; across N replicas behind a load
balancer, each replica's in-process copy is independent, so a "budget of
$0.50/thread" or "10 req/s" is actually enforced N times over. Every class
below is keyed on the exact same state (a thread id, a tool name, a tenant
id) but stored in Redis instead of a local dict, so every replica reads
and writes the same counters — swap one of these in via the constructor
kwarg that already takes the in-process version (`AgentConfig.budget`,
`ToolRegistry.rate_limiter`, `ToolRegistry.cache`, `AgentConfig.sla_tracker`,
`AgentConfig.cost_ledger`) and no other code changes, because these satisfy
the exact same Protocol.

Requires `pip install redis` (lazily imported per class, not a base
dependency of agent_foundry — nothing else in this package needs it).
"""
from __future__ import annotations

import json
import time
from typing import Any

from .contracts import Policy
from .runtime import BudgetExceeded

_DEFAULT_THREAD = "__default__"

# Atomic token-bucket check-and-consume — must be a single script, not
# separate GET/SET calls, or two replicas racing on the same key can both
# read "1 token left" and both consume it (the exact bug this class exists
# to close). Returns 1 (allowed) or 0 (denied).
_TOKEN_BUCKET_LUA = """
local tokens_key = KEYS[1]
local ts_key = KEYS[2]
local rate = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

local tokens = tonumber(redis.call('GET', tokens_key))
local last = tonumber(redis.call('GET', ts_key))
if tokens == nil then tokens = burst end
if last == nil then last = now end

tokens = math.min(burst, tokens + (now - last) * rate)
if tokens < 1 then
    redis.call('SET', tokens_key, tokens, 'EX', 3600)
    redis.call('SET', ts_key, now, 'EX', 3600)
    return 0
end

tokens = tokens - 1
redis.call('SET', tokens_key, tokens, 'EX', 3600)
redis.call('SET', ts_key, now, 'EX', 3600)
return 1
"""


class RedisRunBudget:
    """RunBudgetLike, shared across every replica via Redis hashes keyed
    by thread_id. Same fail-closed contract as the in-process RunBudget:
    spend()/step() raise BudgetExceeded the instant any replica's combined
    view of a thread crosses its policy limit — not just the replica that
    happened to handle this request."""

    def __init__(self, policy: Policy, *, redis_url: str = "redis://localhost:6379/0", key_prefix: str = "agent_foundry:budget"):
        import redis as redis_lib

        self.policy = policy
        self._prefix = key_prefix
        self._r = redis_lib.Redis.from_url(redis_url, decode_responses=True)

    def spend(self, amount: float, *, thread_id: str = _DEFAULT_THREAD) -> None:
        total = self._r.hincrbyfloat(f"{self._prefix}:spent", thread_id, amount)
        if total > self.policy.max_cost_usd_per_thread:
            raise BudgetExceeded(f"spent ${total:.4f} > budget ${self.policy.max_cost_usd_per_thread}")

    def step(self, *, thread_id: str = _DEFAULT_THREAD) -> None:
        count = self._r.hincrby(f"{self._prefix}:steps", thread_id, 1)
        if count > self.policy.max_steps_per_thread:
            raise BudgetExceeded(f"{count} steps > max {self.policy.max_steps_per_thread}")

    def cost_usd_for(self, thread_id: str = _DEFAULT_THREAD) -> float:
        val = self._r.hget(f"{self._prefix}:spent", thread_id)
        return float(val) if val is not None else 0.0

    def steps_for(self, thread_id: str = _DEFAULT_THREAD) -> int:
        val = self._r.hget(f"{self._prefix}:steps", thread_id)
        return int(val) if val is not None else 0


class RedisRateLimiter:
    """RateLimiterLike — a real token bucket shared across every replica
    via an atomic Lua script (EVAL), so N replicas enforce ONE ceiling of
    rate_per_s, not N independent ones."""

    def __init__(self, rate_per_s: float, burst: int, *, redis_url: str = "redis://localhost:6379/0", key_prefix: str = "agent_foundry:ratelimit"):
        import redis as redis_lib

        self.rate_per_s = rate_per_s
        self.burst = burst
        self._prefix = key_prefix
        self._r = redis_lib.Redis.from_url(redis_url, decode_responses=True)
        self._script = self._r.register_script(_TOKEN_BUCKET_LUA)

    def allow(self, key: str) -> bool:
        result = self._script(
            keys=[f"{self._prefix}:{key}:tokens", f"{self._prefix}:{key}:ts"],
            args=[self.rate_per_s, self.burst, time.time()],
        )
        return bool(result)


class RedisToolCache:
    """ToolCacheLike — shared result cache, so a repeated identical tool
    call is served from cache on ANY replica that already made it, not
    just the one that happened to. Tool results must be JSON-serializable
    (a real constraint the in-process ToolCache doesn't have, since it
    just holds a Python reference) — document this on any tool you expect
    to be cached across a fleet."""

    def __init__(self, ttl_s: float = 60.0, *, redis_url: str = "redis://localhost:6379/0", key_prefix: str = "agent_foundry:toolcache"):
        import redis as redis_lib

        self.ttl_s = ttl_s
        self._prefix = key_prefix
        self._r = redis_lib.Redis.from_url(redis_url, decode_responses=True)

    def _key(self, name: str, args: dict) -> str:
        return f"{self._prefix}:{name}:{json.dumps(args, sort_keys=True)}"

    def get(self, name: str, args: dict) -> Any:
        raw = self._r.get(self._key(name, args))
        return None if raw is None else json.loads(raw)

    def set(self, name: str, args: dict, result: Any) -> None:
        self._r.set(self._key(name, args), json.dumps(result), ex=int(self.ttl_s) or 1)


class RedisSLATracker:
    """SLATrackerLike — a fleet-wide rolling window (a Redis list, capped
    at `window`), so uptime/p95 latency reflect every replica's traffic,
    not just whichever one happened to serve a given request."""

    def __init__(self, target_success_rate: float = 0.999, target_p95_latency_ms: float = 2000.0, window: int = 1000,
                 *, redis_url: str = "redis://localhost:6379/0", key_prefix: str = "agent_foundry:sla"):
        import redis as redis_lib

        self.target_success_rate = target_success_rate
        self.target_p95_latency_ms = target_p95_latency_ms
        self.window = window
        self._key = f"{key_prefix}:outcomes"
        self._r = redis_lib.Redis.from_url(redis_url, decode_responses=True)

    def record(self, *, ok: bool, latency_ms: float) -> None:
        self._r.lpush(self._key, json.dumps({"ok": ok, "latency_ms": latency_ms}))
        self._r.ltrim(self._key, 0, self.window - 1)

    def _outcomes(self) -> list[dict]:
        return [json.loads(x) for x in self._r.lrange(self._key, 0, -1)]

    def success_rate(self) -> float:
        outcomes = self._outcomes()
        if not outcomes:
            return 1.0
        return sum(1 for o in outcomes if o["ok"]) / len(outcomes)

    def p95_latency_ms(self) -> float:
        vals = sorted(o["latency_ms"] for o in self._outcomes())
        if not vals:
            return 0.0
        idx = min(len(vals) - 1, int(len(vals) * 0.95))
        return vals[idx]

    def error_budget_remaining(self) -> float:
        allowed = 1 - self.target_success_rate
        actual = 1 - self.success_rate()
        if allowed == 0:
            return 1.0 if actual == 0 else 0.0
        return 1 - (actual / allowed)

    def breaches(self) -> list[str]:
        out = []
        if self.success_rate() < self.target_success_rate:
            out.append(f"success rate {self.success_rate():.3%} below SLA target {self.target_success_rate:.3%}")
        if self.p95_latency_ms() > self.target_p95_latency_ms:
            out.append(f"p95 latency {self.p95_latency_ms():.0f}ms exceeds SLA target {self.target_p95_latency_ms:.0f}ms")
        return out


class RedisCostLedger:
    """CostLedgerLike — completed/incomplete task records shared across
    every replica, with O(1) running totals (a Redis hash, HINCRBYFLOAT)
    instead of re-summing a growing list on every read the way a naive
    port of the in-process CostLedger would."""

    def __init__(self, *, redis_url: str = "redis://localhost:6379/0", key_prefix: str = "agent_foundry:costledger"):
        import redis as redis_lib

        self._prefix = key_prefix
        self._r = redis_lib.Redis.from_url(redis_url, decode_responses=True)

    def close_task(self, *, thread_id: str, tenant_id: str, cost_usd: float, steps: int, outcome: str) -> None:
        self._r.rpush(f"{self._prefix}:completed", json.dumps({
            "thread_id": thread_id, "tenant_id": tenant_id, "cost_usd": cost_usd, "steps": steps, "outcome": outcome, "ts": time.time(),
        }))
        self._r.incrbyfloat(f"{self._prefix}:total_cost", cost_usd)
        self._r.incr(f"{self._prefix}:completed_count")
        self._r.hincrbyfloat(f"{self._prefix}:by_tenant", tenant_id, cost_usd)

    def close_incomplete(self, *, thread_id: str, tenant_id: str, cost_usd: float, reason: str) -> None:
        self._r.rpush(f"{self._prefix}:incomplete", json.dumps({
            "thread_id": thread_id, "tenant_id": tenant_id, "cost_usd": cost_usd, "reason": reason, "ts": time.time(),
        }))
        self._r.incrbyfloat(f"{self._prefix}:total_incomplete_cost", cost_usd)
        self._r.incr(f"{self._prefix}:incomplete_count")

    def total_cost_usd(self) -> float:
        val = self._r.get(f"{self._prefix}:total_cost")
        return float(val) if val is not None else 0.0

    def cost_per_task(self) -> float:
        count = int(self._r.get(f"{self._prefix}:completed_count") or 0)
        return self.total_cost_usd() / count if count else 0.0

    def total_incomplete_cost_usd(self) -> float:
        val = self._r.get(f"{self._prefix}:total_incomplete_cost")
        return float(val) if val is not None else 0.0

    def cost_per_incomplete_task(self) -> float:
        count = int(self._r.get(f"{self._prefix}:incomplete_count") or 0)
        return self.total_incomplete_cost_usd() / count if count else 0.0

    def by_tenant(self) -> dict[str, float]:
        raw = self._r.hgetall(f"{self._prefix}:by_tenant")
        return {k: float(v) for k, v in raw.items()}
