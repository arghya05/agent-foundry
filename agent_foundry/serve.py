"""HTTP serving — wraps any compiled graph as a minimal FastAPI app, complete
with a real browser chat UI and human-in-the-loop approval, not just a bare
JSON API. This is the generic deployment surface: containerize it (see
Dockerfile) and it runs on any cloud that runs a container — AWS ECS/Fargate/
App Runner, GCP Cloud Run, Azure Container Apps, any Kubernetes, a bare VM.
Nothing in agent_foundry/ imports a cloud-specific SDK anywhere; the container
is the entire portability boundary. Requires `pip install fastapi uvicorn`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Protocol

from .contracts import Identity

if TYPE_CHECKING:
    from fastapi import Request
    from pydantic import BaseModel
else:
    try:
        from fastapi import Request
    except ImportError:
        Request = Any
    try:
        from pydantic import BaseModel
    except ImportError:
        BaseModel = object


class AuthenticationError(Exception):
    """Raised by an AuthResolver to reject a request — build_http_app turns
    this into a 401, never an unhandled 500."""


class AuthResolver(Protocol):
    """Resolves an authenticated caller's Identity from request headers —
    the server boundary's own version of "never trust the model for
    identity": the runtime already never trusts a model-supplied session_id/
    user_id (make_act_node overrides both with the graph's real values); this
    is the same posture applied one layer further out, to the HTTP client
    itself. Protocol, not a mandate to use any specific scheme (JWT/OAuth/
    API key) — implement it against whatever your deployment already uses,
    the same way OPAPolicyEngine/CedarPolicyEngine are two of many valid
    policy_engine.PolicyEngine implementations. Raise AuthenticationError to
    reject the request."""

    def resolve(self, headers: Mapping[str, str]) -> Identity: ...


@dataclass
class ApiKeyAuthResolver:
    """Zero-dependency reference AuthResolver: a static {api_key: Identity}
    map, checked against `Authorization: Bearer <key>` (or a bare
    `X-API-Key` header). For real JWT/OAuth verification, implement
    AuthResolver against your IdP/library of choice instead — this exists so
    build_http_app(auth=...) has SOME real, runnable default to point at,
    the same role InMemoryVectorStore/GuardrailEngine play elsewhere in this
    package."""

    identities: dict[str, Identity]

    def resolve(self, headers: Mapping[str, str]) -> Identity:
        auth_header = headers.get("authorization", "") or headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            key = auth_header[len("bearer "):].strip()
        else:
            key = headers.get("x-api-key", "") or headers.get("X-API-Key", "")
        identity = self.identities.get(key)
        if identity is None:
            raise AuthenticationError("invalid or missing API key")
        return identity


class ChatRequest(BaseModel):
    thread_id: str
    message: str


class ResumeRequest(BaseModel):
    thread_id: str
    approved: bool


class ChatResponse(BaseModel):
    status: str  # "ok" | "awaiting_approval"
    reply: str | None = None
    pending: dict[str, Any] | None = None


_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Agent</title>
<style>
body{font-family:-apple-system,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;color:#1c1a17;background:#f6f4f0}
#log{display:flex;flex-direction:column;gap:10px;margin-bottom:16px;min-height:200px}
.msg{padding:10px 14px;border-radius:10px;max-width:80%}
.user{align-self:flex-end;background:#1c1a17;color:#fff}
.agent{align-self:flex-start;background:#fff;border:1px solid #ddd7cb}
.approval{align-self:flex-start;background:#fef3e0;border:1px solid #e0b04a;padding:10px 14px;border-radius:10px}
form{display:flex;gap:8px}
input[type=text]{flex:1;padding:10px;border:1px solid #ddd7cb;border-radius:8px;font-size:14px}
button{padding:10px 16px;border:0;border-radius:8px;background:#1c1a17;color:#fff;cursor:pointer}
</style></head><body>
<h3>Agent</h3>
<div id="log"></div>
<form id="f"><input id="m" type="text" placeholder="Say something..." autocomplete="off"><button>Send</button></form>
<script>
const threadId = 'web-' + Math.random().toString(36).slice(2);
const log = document.getElementById('log');
function add(cls, text){ const d=document.createElement('div'); d.className='msg '+cls; d.textContent=text; log.appendChild(d); log.scrollTop=log.scrollHeight; return d; }
document.getElementById('f').onsubmit = async (e) => {
  e.preventDefault();
  const input = document.getElementById('m');
  const text = input.value.trim();
  if (!text) return;
  add('user', text); input.value = '';
  const res = await fetch('/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({thread_id: threadId, message: text})});
  const data = await res.json();
  handle(data);
};
function handle(data){
  if (data.status === 'awaiting_approval') {
    const d = document.createElement('div'); d.className = 'approval';
    d.textContent = 'Approval needed: ' + data.pending.tool + ' ' + JSON.stringify(data.pending.args) + ' — ' + data.pending.reason + '  ';
    const yes = document.createElement('button'); yes.textContent = 'Approve';
    const no = document.createElement('button'); no.textContent = 'Deny';
    yes.onclick = () => resume(true); no.onclick = () => resume(false);
    d.appendChild(yes); d.appendChild(no); log.appendChild(d); log.scrollTop = log.scrollHeight;
  } else {
    add('agent', data.reply);
  }
}
async function resume(approved){
  const res = await fetch('/resume', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({thread_id: threadId, approved})});
  handle(await res.json());
}
</script></body></html>"""


def chat_response_from_result(result: dict) -> ChatResponse:
    """Turns a graph.invoke() result into a ChatResponse — module-level so a
    caller that overrides /chat entirely (see build_http_app's
    serve_chat_route) can reuse this instead of reimplementing the
    interrupt/awaiting_approval handling."""
    interrupts = result.get("__interrupt__")
    if interrupts:
        pending = interrupts[0].value
        return ChatResponse(status="awaiting_approval", pending=pending)
    return ChatResponse(status="ok", reply=result["messages"][-1]["content"])


def invoke_graph_chat_turn(graph: Any, *, message: str, thread_id: str, identity: Identity | None = None) -> ChatResponse:
    """graph.invoke() for one chat turn, plus response shaping — the one
    place runtime.BudgetExceeded (a RunBudget/LatencyBudget ceiling —
    cost, step count, or cumulative session wall-clock time) becomes a
    proper HTTP 429 instead of an unhandled exception. Found live: nothing
    anywhere in this codebase ever caught BudgetExceeded before this — it
    propagated out of graph.invoke() as a raw 500, and (a real
    FastAPI/Starlette quirk: an unhandled exception's response doesn't
    reliably pick up CORSMiddleware's headers) surfaced to the browser as an
    opaque "Failed to fetch" with zero explanation, discovered testing a
    genuine multi-turn conversation against healthcare/backend/agent.py's
    LatencyBudget. Both build_http_app's own /chat below and any caller that
    registers its own (serve_chat_route=False, e.g. healthcare/backend/
    app.py's role-aware chat) should invoke through this rather than calling
    graph.invoke() directly.

    `identity`: the AUTHENTICATED caller (from AuthResolver.resolve(), not
    the thread_id-namespacing alone) — seeded into state["request_identity"]
    so orchestration._resolve_identity picks it up for the PDP/tool-
    invocation/audit decisions THIS turn makes, instead of those decisions
    always seeing AgentConfig's static, graph-build-time identity no
    matter who the real caller is. None (default, when build_http_app's
    own `auth` isn't configured): unchanged behavior."""
    from fastapi import HTTPException

    from .runtime import BudgetExceeded

    state: dict[str, Any] = {"messages": [{"role": "user", "content": message}], "thread_id": thread_id}
    if identity is not None:
        state["request_identity"] = {"id": identity.id, "tenant_id": identity.tenant_id, "roles": tuple(identity.roles)}
    try:
        result = graph.invoke(state, {"configurable": {"thread_id": thread_id}})
    except BudgetExceeded as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    return chat_response_from_result(result)


def build_http_app(
    graph: Any, *, serve_demo_ui: bool = True, serve_chat_route: bool = True, serve_resume_route: bool = True,
    auth: AuthResolver | None = None, allow_unauthenticated_demo: bool = False,
) -> Any:
    """A real deployment surface: GET / (a working browser chat UI), POST /chat,
    POST /resume (approve/deny a paused destructive action), GET /health.

    `auth`: every /chat and /resume request must resolve to an Identity via
    `auth.resolve(request.headers)` (401 if it doesn't) — the client-
    supplied `thread_id` is then namespaced under `{tenant_id}:{identity.id}:
    {thread_id}` before it ever reaches the graph. Namespacing by tenant_id
    ALONE would still let two different users of the SAME tenant collide on
    the same client-chosen thread_id (Alice and Bob both picking "support"),
    so identity.id is part of the namespace too — real, disjoint per-user
    conversation isolation, not just per-tenant. See AuthResolver's own
    docstring for wiring in real JWT/OAuth verification instead of the
    reference ApiKeyAuthResolver.

    Required unless `allow_unauthenticated_demo=True` — a governed serving
    surface should not default to trusting a client-supplied identity, the
    same "never trust the caller" posture the runtime already applies to a
    model-supplied session_id/user_id. Pass `allow_unauthenticated_demo=True`
    to explicitly opt into the old behavior (the client's thread_id trusted
    verbatim, no auth at all) — fine for local development, a demo, or a
    single-tenant deployment already sitting behind its own auth gateway;
    the flag name is deliberately loud so it can't be set by accident.

    ChatRequest/ChatResponse are module-level, not nested in this function — with
    `from __future__ import annotations` active, a Pydantic model FastAPI can't
    resolve via the module's global namespace (e.g. one defined inside a function)
    silently gets treated as a query parameter instead of a request body.

    `serve_chat_route=False` skips registering POST /chat entirely — for a
    deployment that needs its own request shape (e.g. healthcare/backend/
    app.py's role-aware chat, which routes to one of several role-specific
    graphs instead of the single `graph` this function takes). FastAPI
    matches routes in registration order, so a route registered here can't
    be overridden by registering another at the same path afterward — this
    flag exists so the caller can register its own POST /chat instead, reusing
    chat_response_from_result() above for the interrupt-handling logic.

    `serve_resume_route=False` skips registering POST /resume — for the same
    reason as serve_chat_route=False: a deployment with more than one graph
    (healthcare/backend/app.py's one-graph-per-role) can't resume a paused
    thread through a route bound to a single fixed `graph` — the paused
    thread might belong to a different graph object than this one, even
    though they share a checkpointer. The caller registers its own /resume,
    routing to the correct graph the same way its own /chat does.

    `serve_demo_ui=False` skips registering GET / entirely — for a deployment
    with its own separate, real frontend (e.g. healthcare/backend/app.py),
    this generic unbranded/session-less reference page at the API's own root
    is confusing at best (found live: a real user landed on it by navigating
    to the API's base URL instead of the actual frontend's port, and
    reasonably mistook it for a broken/old version of the real app) — the
    caller can register its own GET / instead. Defaults to True so this
    stays a genuine batteries-included "point a browser at it and it works"
    surface for anything that has no frontend of its own (the framework's
    own quickstart/demo use)."""
    if auth is None and not allow_unauthenticated_demo:
        raise ValueError(
            "build_http_app requires auth=<AuthResolver> for a governed deployment — "
            "pass allow_unauthenticated_demo=True to explicitly opt into the unauthenticated "
            "demo behavior (the client's thread_id trusted verbatim, no identity resolved)."
        )

    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse

    app = FastAPI()

    def _resolve_caller(request: Request) -> Identity | None:
        if auth is None:
            return None
        try:
            return auth.resolve(request.headers)
        except AuthenticationError as e:
            raise HTTPException(status_code=401, detail=str(e)) from e

    def _namespaced_thread_id(request_thread_id: str, identity: Identity | None) -> str:
        if identity is None:
            return request_thread_id
        # tenant_id ALONE isn't enough — two different users of the same
        # tenant could still collide on the same client-chosen thread_id
        # (see this function's own docstring). identity.id makes the
        # isolation per-USER, not merely per-tenant.
        return f"{identity.tenant_id}:{identity.id}:{request_thread_id}"

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    if serve_demo_ui:
        @app.get("/", response_class=HTMLResponse)
        def index() -> str:
            return _PAGE

    if serve_chat_route:
        @app.post("/chat", response_model=ChatResponse)
        def chat(req: ChatRequest, request: Request) -> ChatResponse:
            identity = _resolve_caller(request)
            thread_id = _namespaced_thread_id(req.thread_id, identity)
            return invoke_graph_chat_turn(graph, message=req.message, thread_id=thread_id, identity=identity)

    if serve_resume_route:
        @app.post("/resume", response_model=ChatResponse)
        def resume(req: ResumeRequest, request: Request) -> ChatResponse:
            from langgraph.types import Command

            from .runtime import BudgetExceeded

            identity = _resolve_caller(request)
            thread_id = _namespaced_thread_id(req.thread_id, identity)
            try:
                result = graph.invoke(Command(resume={"approved": req.approved}), {"configurable": {"thread_id": thread_id}})
            except BudgetExceeded as e:  # same as invoke_graph_chat_turn — see its docstring
                raise HTTPException(status_code=429, detail=str(e)) from e
            return chat_response_from_result(result)

    return app
