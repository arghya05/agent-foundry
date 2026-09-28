"""A common, metered API transport for the reference and Foundry arms."""
from __future__ import annotations

from decimal import Decimal
import json
import os
from pathlib import Path
import re
import time
import uuid

import httpx

from common import digest, write_json


class BudgetExceeded(RuntimeError):
    pass


class TaskLimitExceeded(RuntimeError):
    pass


class IncompleteResponse(RuntimeError):
    pass


def safe_provider_error(response):
    """Record diagnostic fields without headers or credential echoes."""
    try:
        error = response.json().get("error", {})
    except (ValueError, AttributeError):
        return {"type": "unstructured_provider_error"}
    if not isinstance(error, dict):
        return {"type": "unstructured_provider_error"}
    out = {}
    for field in ("type", "code", "message", "param"):
        value = error.get(field)
        if not isinstance(value, str):
            continue
        for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            secret = os.environ.get(name)
            if secret:
                value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"sk-[A-Za-z0-9_*.-]+", "[REDACTED]", value)
        out[field] = value[:500]
    return out


class Budget:
    """Persistent reservations; caller holds a process lock for the whole run.

    Unknown outcomes retain their full reservation. Amounts are upper estimates
    at configured published prices, not a replacement for the provider bill.
    """

    def __init__(self, path, cap):
        self.path = Path(path)
        self.cap = Decimal(str(cap))
        if not self.cap.is_finite() or self.cap <= 0:
            raise ValueError("A positive finite total spending limit is required")
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {
            "cap_usd": str(self.cap), "requests": []}
        if Decimal(self.data["cap_usd"]) != self.cap:
            raise ValueError("Ledger spending limit differs; do not silently reset the budget")

    @property
    def used(self):
        return sum((Decimal(row.get("cost_usd_upper", row["reserved_usd"]))
                    for row in self.data["requests"]), Decimal(0))

    def reserve(self, amount, attempt_id):
        amount = Decimal(str(amount))
        if not amount.is_finite() or amount <= 0:
            raise ValueError("Invalid reservation")
        if self.used + amount > self.cap:
            raise BudgetExceeded("Insufficient budget for the conservative request reservation")
        identifier = uuid.uuid4().hex
        self.data["requests"].append({"id": identifier, "attempt_id": attempt_id,
                                      "reserved_usd": str(amount), "status": "unknown_or_pending"})
        write_json(self.path, self.data)
        return identifier

    def settle(self, identifier, cost, usage):
        cost = Decimal(str(cost))
        if not cost.is_finite() or cost < 0:
            raise ValueError("Invalid reported cost")
        row = next(row for row in self.data["requests"] if row["id"] == identifier)
        if row["status"] != "unknown_or_pending":
            raise ValueError("Request already settled")
        row.update(cost_usd_upper=str(cost), usage=usage, status="settled")
        write_json(self.path, self.data)
        if cost > Decimal(row["reserved_usd"]):
            raise BudgetExceeded("Usage exceeded reservation; verify model limits and pricing")


def anthropic_turns(messages, blocks_by_ids):
    turns = []
    for message in messages:
        role = message["role"]
        if role == "tool":
            role = "user"
            blocks = [{"type": "tool_result", "tool_use_id": message["tool_call_id"],
                       "content": message["content"]}]
        elif message.get("tool_calls"):
            identifiers = tuple(t["id"] for t in message["tool_calls"])
            # Preserve the provider's thinking/signature blocks across tool turns.
            blocks = blocks_by_ids[identifiers]
        else:
            blocks = [{"type": "text", "text": message.get("content") or " "}]
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].extend(blocks)
        else:
            turns.append({"role": role, "content": list(blocks)})
    return turns


def openai_turns(messages, blocks_by_ids):
    """Replay complete Responses output, including encrypted reasoning."""
    items = []
    for message in messages:
        if message["role"] == "tool":
            items.append({"type": "function_call_output", "call_id": message["tool_call_id"],
                          "output": message["content"]})
        elif message.get("tool_calls"):
            items.extend(blocks_by_ids[tuple(t["id"] for t in message["tool_calls"])])
        else:
            items.append({"role": message["role"], "content": message.get("content") or " "})
    return items


class Transport:
    def __init__(self, provider, config, budget, attempt_id, *, max_calls=20,
                 max_output_tokens=2048, timeout_s=600, on_trace=None, client=None):
        if provider not in ("openai", "anthropic"):
            raise ValueError("Unsupported provider")
        self.provider, self.config, self.budget = provider, config, budget
        self.model = config["model"]
        self.attempt_id, self.max_calls = attempt_id, max_calls
        self.max_output_tokens = max_output_tokens
        self.deadline = time.monotonic() + timeout_s
        self.on_trace = on_trace or (lambda trace: None)
        self.traces, self.blocks_by_ids = [], {}
        self.limit_error = None
        self.fatal_error = None
        # Fixed official endpoints, no redirects/proxy environment, no automatic retries.
        self.client = client or httpx.Client(timeout=75, follow_redirects=False, trust_env=False)
        self.p_in = Decimal(str(config["input_usd_per_million"]))
        self.p_out = Decimal(str(config["output_usd_per_million"]))
        if any(not rate.is_finite() or rate <= 0 for rate in (self.p_in, self.p_out)):
            raise ValueError("Positive, verified model prices are required")
        if config["context_tokens"] <= 0 or max_output_tokens <= 0 or max_calls <= 0:
            raise ValueError("Invalid token/call limits")

    def close(self):
        self.client.close()

    def call(self, system, messages, tools):
        remaining = self.deadline - time.monotonic()
        if len(self.traces) >= self.max_calls or remaining <= 0:
            self.limit_error = "TaskLimitExceeded"
            raise TaskLimitExceeded()
        if self.provider == "openai":
            url = "https://api.openai.com/v1/responses"
            headers = {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]}
            payload = {"model": self.model, "instructions": system,
                       "input": openai_turns(messages, self.blocks_by_ids),
                       "tools": [{"type": "function", **t["function"], "strict": False} for t in tools],
                       "max_output_tokens": self.max_output_tokens,
                       "include": ["reasoning.encrypted_content"],
                       "store": False, **self.config["options"]}
        else:
            url = "https://api.anthropic.com/v1/messages"
            headers = {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"}
            if os.environ.get("ANTHROPIC_WORKSPACE_ID"):
                headers["anthropic-workspace-id"] = os.environ["ANTHROPIC_WORKSPACE_ID"]
            payload = {"model": self.model, "system": system,
                       "messages": anthropic_turns(messages, self.blocks_by_ids),
                       "tools": [{"name": t["function"]["name"], "description": t["function"]["description"],
                                  "input_schema": t["function"]["parameters"]} for t in tools],
                       "max_tokens": self.max_output_tokens, **self.config["options"]}
        # Reserve a FULL context at base price, plus the configured output ceiling.
        # There are no paid hosted tools, cache writes, batches, or priority tiers.
        reservation = (self.config["context_tokens"] * self.p_in + self.max_output_tokens * self.p_out) / 1000000
        try:
            request_id = self.budget.reserve(reservation, self.attempt_id)
        except BudgetExceeded:
            self.fatal_error = "BudgetExceeded"
            raise
        trace = {"request_id": request_id, "request_hash": digest(payload), "request": payload}
        self.traces.append(trace)
        self.on_trace(self.traces)
        started = time.monotonic()
        try:
            response = self.client.post(url, headers=headers, json=payload, timeout=min(75, remaining))
            trace["http_status"] = response.status_code
            if response.is_error:
                trace["provider_error"] = safe_provider_error(response)
            response.raise_for_status()
            raw = response.json()
            usage = raw["usage"]
            if self.provider == "openai":
                inputs, outputs = int(usage["input_tokens"]), int(usage["output_tokens"])
            else:
                inputs = int(usage["input_tokens"]) + int(usage.get("cache_read_input_tokens", 0))
                # Unexpected cache writes are conservatively charged at the 1-hour rate.
                inputs += 2 * int(usage.get("cache_creation_input_tokens", 0))
                outputs = int(usage["output_tokens"])
            if inputs < 0 or outputs < 0:
                raise ValueError("Invalid provider usage")
            cost = (inputs * self.p_in + outputs * self.p_out) / 1000000
            self.budget.settle(request_id, cost, usage)
            trace.update(response=raw, input_tokens=inputs, output_tokens=outputs, cost_usd_upper=float(cost))
            if self.provider == "openai":
                if raw["status"] != "completed":
                    raise IncompleteResponse()
                content = "".join(c["text"] for item in raw["output"] if item["type"] == "message"
                                  for c in item["content"] if c["type"] == "output_text")
                calls = [{"id": item["call_id"], "name": item["name"],
                          "arguments": json.loads(item["arguments"])}
                         for item in raw["output"] if item["type"] == "function_call"]
                if calls:
                    self.blocks_by_ids[tuple(c["id"] for c in calls)] = raw["output"]
            else:
                if raw["stop_reason"] not in ("end_turn", "tool_use"):
                    raise IncompleteResponse()
                content = "".join(b["text"] for b in raw["content"] if b["type"] == "text")
                calls = [{"id": b["id"], "name": b["name"], "arguments": b["input"]}
                         for b in raw["content"] if b["type"] == "tool_use"]
                if calls:
                    self.blocks_by_ids[tuple(c["id"] for c in calls)] = raw["content"]
            if any(not isinstance(c["arguments"], dict) for c in calls):
                raise ValueError("Tool arguments must be JSON objects")
            return {"content": content, "tool_calls": calls, "input_tokens": inputs,
                    "output_tokens": outputs, "cost_usd_upper": float(cost)}
        except Exception as exc:
            trace["error_type"] = type(exc).__name__
            # Stop the campaign on network/auth/schema transport failures; do not
            # spend the remaining budget repeatedly hitting a broken endpoint.
            if not isinstance(exc, IncompleteResponse):
                self.fatal_error = type(exc).__name__
            raise
        finally:
            trace["latency_s"] = time.monotonic() - started
            self.on_trace(self.traces)
