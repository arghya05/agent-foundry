"""Channels — connect any external messaging surface to a compiled graph, the
same way mcp_tools.py connects any MCP server to the Tools Gateway. Slack is the
concrete reference implementation (real signature verification, real Events API
shape); the same pattern — verify, extract text + a thread id, invoke the graph,
post the reply back through that channel's own API — is how Teams, Twilio SMS,
or an email-via-webhook provider would plug in too.

Requires `pip install fastapi uvicorn` for build_slack_app.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.request
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from fastapi import Request, Response
else:
    try:
        from fastapi import Request, Response
    except ImportError:
        Request = Response = object


def verify_slack_signature(*, signing_secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    """Slack's documented request-signing scheme: HMAC-SHA256 over
    'v0:{timestamp}:{body}', keyed by the app's signing secret. Rejects requests
    older than 5 minutes, per Slack's own replay-protection recommendation."""
    try:
        if abs(time.time() - float(timestamp)) > 60 * 5:
            return False
    except ValueError:
        return False
    basestring = f"v0:{timestamp}:{body.decode()}"
    computed = "v0=" + hmac.new(signing_secret.encode(), basestring.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature)


def _post_to_slack(bot_token: str, channel: str, text: str) -> None:
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps({"channel": channel, "text": text}).encode(),
        headers={"Authorization": f"Bearer {bot_token}", "Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=10)


def build_slack_app(
    graph: Any,
    *,
    signing_secret: str,
    bot_token: str | None = None,
    post_message: Callable[[str, str], None] | None = None,
) -> Any:
    """A real FastAPI app implementing Slack's Events API at /slack/events:
    verifies the request signature, handles the one-time URL-verification
    handshake, extracts a message event, invokes the graph, and posts the reply
    back via chat.postMessage. `post_message(channel, text)` defaults to a real
    Slack API call using `bot_token`; inject your own for testing or for a
    different delivery mechanism."""
    from fastapi import FastAPI

    poster = post_message or (lambda channel, text: _post_to_slack(bot_token or "", channel, text))
    app = FastAPI()

    @app.post("/slack/events")
    async def slack_events(request: Request) -> Response:
        body = await request.body()
        timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
        signature = request.headers.get("X-Slack-Signature", "")
        if not verify_slack_signature(signing_secret=signing_secret, timestamp=timestamp, body=body, signature=signature):
            return Response(status_code=401)

        payload = json.loads(body)
        if payload.get("type") == "url_verification":
            return Response(content=payload["challenge"], media_type="text/plain")

        event = payload.get("event", {})
        if event.get("type") == "message" and "bot_id" not in event:
            text = event.get("text", "")
            channel_id = event["channel"]
            thread_id = event.get("thread_ts") or event["ts"]
            result = graph.invoke(
                {"messages": [{"role": "user", "content": text}], "thread_id": thread_id},
                {"configurable": {"thread_id": thread_id}},
            )
            reply = result["messages"][-1]["content"]
            poster(channel_id, reply)
        return Response(status_code=200)

    return app
