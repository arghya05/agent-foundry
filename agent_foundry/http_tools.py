"""HTTP/REST tool adapter — the "3rd-party API adapters" chip under Tools
Gateway: wraps any REST endpoint as a ToolSpec using stdlib urllib, so this needs
zero extra dependency and works for literally any HTTP API.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from .contracts import ToolSpec


def http_tool(
    name: str,
    description: str,
    *,
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    timeout_s: float = 10.0,
) -> ToolSpec:
    """`url` may contain {placeholders} filled from the call's kwargs (e.g.
    url="https://api.example.com/orders/{order_id}"); any remaining kwargs become
    query params (GET) or a JSON body (everything else). Response is parsed as
    JSON when possible, otherwise returned as raw text."""

    def call(**kwargs: Any) -> Any:
        resolved_url = url.format(**kwargs)
        remaining = {k: v for k, v in kwargs.items() if "{" + k + "}" not in url}
        req_headers = dict(headers or {})
        data = None
        if method.upper() == "GET":
            if remaining:
                resolved_url = f"{resolved_url}?{urllib.parse.urlencode(remaining)}"
        else:
            data = json.dumps(remaining).encode()
            req_headers.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(resolved_url, data=data, headers=req_headers, method=method.upper())
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode()
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return body

    return ToolSpec(name=name, description=description, parameters={}, fn=call)
