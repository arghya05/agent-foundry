# Governed tools in an external agent framework

`GovernedToolGateway` executes an individual registered tool through Foundry's
current action controls. `to_governed_langchain_tool` exposes that boundary as a
LangChain `StructuredTool`, with synchronous and asynchronous invocation.

```python
from agent_foundry.governed_tools import GovernedToolGateway
from agent_foundry.quickstart import to_governed_langchain_tool

# agent is your configured Foundry Agent. The host supplies this function
# from authenticated request state, never from model-generated arguments.
def current_context():
    return authenticated_request.execution_context

gateway = GovernedToolGateway(agent.config)
lookup = to_governed_langchain_tool(
    gateway, "lookup_order", context_provider=current_context,
    required_capabilities=frozenset({"action_policy", "request_identity"}),
)
# Pass lookup to the host LangChain agent's tools list.
```

The application supplies `agent` and `authenticated_request`; the example does
not create an authentication service. Resolve permissions afresh on each call,
use a stable trusted thread ID for the intended budget scope, and pass the current
request budget, cancellation token and deadline. Both user and tenant are required.

## Enforced boundary

Every invocation resolves current identity and the intersection of configured,
request and inherited policies. The PDP checks tool allowlists, scopes, data
classification, external policy, declared egress, autonomy, cost and approval
requirements before dispatch. OPA/custom policy receives caller, tenant, roles
and a copy of the bound arguments. Arguments and cached/fresh outputs are checked
against declared schemas. Install the `schema` extra for full JSON Schema checks.

Tool parameters named `user_id`, `tenant_id` and `session_id` are bound from the
trusted context and omitted from the exported model schema. They cannot be chosen
by the model through this adapter. The application must still validate ownership
of resources named by other arguments, such as an order ID.

Each admitted call consumes one step, including invalid-input and cached calls.
Parent request budgets remain inherited when a nested call uses another thread ID.
There is no model call inside this gateway; an external model loop needs separate
usage accounting. Audit events record the caller, tenant, run/thread/trace IDs,
decision and action digest; argument values are not copied into the audit event.
The existing tracer measures tool execution. Configure storage/retention for
production audit durability.

## Approval, effects and capability limits

An approval requirement raises `ToolApprovalRequired` before execution. This
adapter deliberately exposes no `approved=True` bypass. It does not yet provide
an authenticated durable approval workflow or turn LangChain errors into Foundry
graph interrupts. Use a supported Foundry approval workflow until the explicit
approval lifecycle is integrated here.

Non-idempotent destructive tools cannot enable automatic retries through this
gateway. An optional supplied idempotency key is bound to the tenant, caller,
tool version and arguments before reaching the registry store. This does not
establish atomic external effects, crash recovery, or exactly-once execution.
Declared idempotency still has to hold in the downstream service.

`gateway.require_capabilities(...)` rejects unsupported requirements, including
`inner_agent_actions`. A crew wrapped as one tool remains opaque internally.
Only the LangChain export is integration-tested in this iteration; a CrewAI tool
adapter must be tested against its actual version and tool API separately.
Timeouts and cancellation are cooperative and do not terminate a running tool.
Configuration, tool registration and authentication are trusted host concerns;
this boundary is not a sandbox against malicious Python.

## Migration from raw exports

`to_langchain_tool(spec)` and `to_langchain_tools(registry)` now reject implicit
raw exports. Prefer the governed helper above. If the host deliberately owns all
controls itself, the previous conversion remains available through explicit
`allow_unguarded=True`. That opt-in exports the raw callable, including none of
Foundry's runtime governance. Plain functions passed directly to LangChain also
remain the host's responsibility.

The related policy repair also affects native and LangGraph runs: a request's
stricter autonomy and named approval requirements are now applied even when the
configured guard was built with a more permissive policy. Hard denials from
either policy/guard still take precedence over an approval request.
