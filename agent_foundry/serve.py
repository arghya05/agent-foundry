"""HTTP serving — wraps any compiled graph as a minimal FastAPI app, complete
with a real browser chat UI and human-in-the-loop approval, not just a bare
JSON API. This is the generic deployment surface: containerize it (see
Dockerfile) and it runs on any cloud that runs a container — AWS ECS/Fargate/
App Runner, GCP Cloud Run, Azure Container Apps, any Kubernetes, a bare VM.
Nothing in agent_foundry/ imports a cloud-specific SDK anywhere; the container
is the entire portability boundary. Requires `pip install fastapi uvicorn`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic import BaseModel
else:
    try:
        from pydantic import BaseModel
    except ImportError:
        BaseModel = object


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


def invoke_graph_chat_turn(graph: Any, *, message: str, thread_id: str) -> ChatResponse:
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
    graph.invoke() directly."""
    from fastapi import HTTPException

    from .runtime import BudgetExceeded

    try:
        result = graph.invoke(
            {"messages": [{"role": "user", "content": message}], "thread_id": thread_id},
            {"configurable": {"thread_id": thread_id}},
        )
    except BudgetExceeded as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    return chat_response_from_result(result)


def build_http_app(graph: Any, *, serve_demo_ui: bool = True, serve_chat_route: bool = True, serve_resume_route: bool = True) -> Any:
    """A real deployment surface: GET / (a working browser chat UI), POST /chat,
    POST /resume (approve/deny a paused destructive action), GET /health.

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
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse

    app = FastAPI()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    if serve_demo_ui:
        @app.get("/", response_class=HTMLResponse)
        def index() -> str:
            return _PAGE

    if serve_chat_route:
        @app.post("/chat", response_model=ChatResponse)
        def chat(req: ChatRequest) -> ChatResponse:
            return invoke_graph_chat_turn(graph, message=req.message, thread_id=req.thread_id)

    if serve_resume_route:
        @app.post("/resume", response_model=ChatResponse)
        def resume(req: ResumeRequest) -> ChatResponse:
            from fastapi import HTTPException
            from langgraph.types import Command

            from .runtime import BudgetExceeded

            try:
                result = graph.invoke(Command(resume={"approved": req.approved}), {"configurable": {"thread_id": req.thread_id}})
            except BudgetExceeded as e:  # same as invoke_graph_chat_turn — see its docstring
                raise HTTPException(status_code=429, detail=str(e)) from e
            return chat_response_from_result(result)

    return app
