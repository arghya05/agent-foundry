import pytest

from agent_foundry.contracts import Identity, LLMResponse, Policy, ToolSpec
from agent_foundry.llm_gateway import (
    LLMGateway, MultiProvider, PricingRegistry, PromptCache, RateLimitExceeded,
)
from agent_foundry.runtime import RateLimiter
from agent_foundry.tools_gateway import PermissionDenied, ToolCache, ToolRegistry, tool_json_schema


def test_tool_registry_rbac_denies_unauthorized(identity, tool_registry):
    policy = Policy(allowed_tools=frozenset())  # nothing allowed
    with pytest.raises(PermissionDenied):
        tool_registry.invoke("lookup_order", {"order_id": "A100"}, identity=identity, policy=policy)


def test_tool_registry_invoke_success(identity, policy, tool_registry):
    result = tool_registry.invoke("lookup_order", {"order_id": "A100"}, identity=identity, policy=policy)
    assert result.ok and "A100" in result.output


def test_tool_registry_cache_serves_repeat_calls_instantly(identity, policy, lookup_order_tool):
    reg = ToolRegistry(cache=ToolCache(ttl_s=60))
    reg.register(lookup_order_tool)
    first = reg.invoke("lookup_order", {"order_id": "A100"}, identity=identity, policy=policy)
    second = reg.invoke("lookup_order", {"order_id": "A100"}, identity=identity, policy=policy)
    assert second.output == first.output and second.latency_ms == 0.0


def test_tool_registry_rate_limit_denies_past_burst(identity, policy, lookup_order_tool):
    reg = ToolRegistry(rate_limiter=RateLimiter(rate_per_s=0.001, burst=1))
    reg.register(lookup_order_tool)
    ok = reg.invoke("lookup_order", {"order_id": "A100"}, identity=identity, policy=policy)
    denied = reg.invoke("lookup_order", {"order_id": "A100"}, identity=identity, policy=policy)
    assert ok.ok and not denied.ok


def test_tool_cache_key_survives_list_and_dict_valued_args(identity):
    """Regression: the cache key used to be (name, tuple(sorted(args.items()))),
    which crashes with `TypeError: unhashable type` the moment any arg value is
    a list or dict — now it's a json.dumps-based string key instead."""
    tools = ToolRegistry(cache=ToolCache(ttl_s=60))
    spec = ToolSpec("search", "search", {"filters": "object", "tags": "array"}, lambda filters, tags: "ok")
    tools.register(spec)
    p = Policy(allowed_tools=frozenset({"search"}))

    result = tools.invoke("search", {"filters": {"status": "open"}, "tags": ["a", "b"]}, identity=identity, policy=p)
    assert result.ok and result.output == "ok"


def test_tool_cache_is_isolated_per_tenant():
    """Regression: the cache key used to be (tool, args) only — no tenant —
    so two tenants sharing a registry/cache could read each other's cached
    tool results for a tenant-specific tool. Proven here by two identities
    with the same tenant_id getting a cache hit, and a different tenant_id
    NOT getting one, even though the tool call and args are identical."""
    from agent_foundry.contracts import Identity

    calls = []

    def get_account(customer_id: str) -> str:
        calls.append(customer_id)
        return f"account for {customer_id}"

    tools = ToolRegistry(cache=ToolCache(ttl_s=60))
    tools.register(ToolSpec("get_account", "get account", {"customer_id": "string"}, get_account))
    p = Policy(allowed_tools=frozenset({"get_account"}))

    tenant_a_1 = Identity(id="a1", tenant_id="tenant-a")
    tenant_a_2 = Identity(id="a2", tenant_id="tenant-a")
    tenant_b = Identity(id="b1", tenant_id="tenant-b")

    tools.invoke("get_account", {"customer_id": "123"}, identity=tenant_a_1, policy=p)
    tools.invoke("get_account", {"customer_id": "123"}, identity=tenant_a_2, policy=p)  # same tenant -> cache hit
    tools.invoke("get_account", {"customer_id": "123"}, identity=tenant_b, policy=p)  # different tenant -> real call

    assert calls == ["123", "123"]  # tenant-a shared the cache; tenant-b did not


def test_destructive_tool_is_never_cached_even_on_success(identity):
    """Regression: successful results used to be cached unconditionally,
    even for a destructive tool (a refund) — the module's own comment
    already warned side-effecting operations shouldn't be cached, but
    nothing actually enforced that."""
    calls = []

    def issue_refund(order_id: str) -> str:
        calls.append(order_id)
        return f"refunded {order_id}"

    tools = ToolRegistry(cache=ToolCache(ttl_s=60))
    tools.register(ToolSpec("issue_refund", "refund", {"order_id": "string"}, issue_refund, destructive=True))
    p = Policy(allowed_tools=frozenset({"issue_refund"}))

    tools.invoke("issue_refund", {"order_id": "A100"}, identity=identity, policy=p)
    tools.invoke("issue_refund", {"order_id": "A100"}, identity=identity, policy=p)

    assert calls == ["A100", "A100"]  # never served from cache


def test_tool_invoke_rejects_missing_required_argument_without_raising_inside_fn(identity):
    """Regression: args from the model were passed straight to spec.fn(**args)
    with no validation boundary — a missing/mistyped argument surfaced as a
    raw exception from inside the tool body. Now it's a clean, structured
    ToolResult(ok=False) instead. The exact wording depends on whether real
    `jsonschema` is installed (preferred when available) or the dependency-
    free fallback runs — both must name the missing argument."""
    tools = ToolRegistry()
    tools.register(ToolSpec("lookup_order", "Look up an order", {"order_id": "string"}, lambda order_id: f"order {order_id}"))
    p = Policy(allowed_tools=frozenset({"lookup_order"}))

    result = tools.invoke("lookup_order", {}, identity=identity, policy=p)

    assert not result.ok and "order_id" in result.error


def test_tool_invoke_retries_up_to_max_retries_on_failure():
    """ToolSpec.max_retries lets a flaky tool call be retried automatically
    inside ToolRegistry.invoke() instead of always failing on the first
    transient error."""
    from agent_foundry.contracts import Identity

    attempts = []

    def flaky(order_id: str) -> str:
        attempts.append(order_id)
        if len(attempts) < 3:
            raise RuntimeError("transient")
        return "ok"

    tools = ToolRegistry()
    tools.register(ToolSpec("flaky", "flaky", {"order_id": "string"}, flaky, max_retries=2))
    identity = Identity(id="t", tenant_id="acme")
    p = Policy(allowed_tools=frozenset({"flaky"}))

    result = tools.invoke("flaky", {"order_id": "A100"}, identity=identity, policy=p)

    assert result.ok and result.output == "ok" and len(attempts) == 3


def test_tool_invoke_rejects_output_that_violates_its_declared_output_schema(identity):
    """Regression: ToolSpec.output_schema was pure metadata — nothing ever
    checked a tool's actual return value against it. A tool that returns a
    string when it declared an object output_schema must now fail cleanly."""
    tools = ToolRegistry()
    tools.register(ToolSpec(
        "lookup_order", "Look up an order", {"order_id": "string"},
        lambda order_id: "not an object",  # violates the schema below
        output_schema={"type": "object", "properties": {"status": {"type": "string"}}, "required": ["status"]},
    ))
    p = Policy(allowed_tools=frozenset({"lookup_order"}))

    result = tools.invoke("lookup_order", {"order_id": "A100"}, identity=identity, policy=p)

    assert not result.ok and "output_schema" in result.error


def test_tool_invoke_accepts_output_that_matches_its_declared_output_schema(identity):
    tools = ToolRegistry()
    tools.register(ToolSpec(
        "lookup_order", "Look up an order", {"order_id": "string"},
        lambda order_id: {"status": "shipped"},
        output_schema={"type": "object", "properties": {"status": {"type": "string"}}, "required": ["status"]},
    ))
    p = Policy(allowed_tools=frozenset({"lookup_order"}))

    result = tools.invoke("lookup_order", {"order_id": "A100"}, identity=identity, policy=p)

    assert result.ok and result.output == {"status": "shipped"}


def test_tool_json_schema_is_provider_agnostic():
    spec = ToolSpec("lookup_order", "Look up an order", {"order_id": "string"}, lambda order_id: "x")
    schema = tool_json_schema(spec)
    assert set(schema.keys()) == {"name", "description", "parameters"}
    assert schema["parameters"]["type"] == "object"


def test_llm_gateway_fails_over_to_next_model_on_provider_error():
    class FlakyThenGood:
        calls = 0
        def complete(self, messages, *, model, **kw):
            FlakyThenGood.calls += 1
            if model == "bad-model":
                raise RuntimeError("outage")
            return LLMResponse(text="ok", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0)

    gw = LLMGateway(provider=FlakyThenGood(), routes={"default": ["bad-model", "good-model"]})
    resp = gw.complete([{"role": "user", "content": "hi"}])
    assert resp.text == "ok" and resp.model == "good-model"


def test_llm_gateway_cache_avoids_a_second_provider_call():
    class CountingProvider:
        calls = 0
        def complete(self, messages, *, model, **kw):
            CountingProvider.calls += 1
            return LLMResponse(text="r", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0)

    gw = LLMGateway(provider=CountingProvider(), cache=PromptCache())
    msgs = [{"role": "user", "content": "hi"}]
    gw.complete(msgs)
    gw.complete(msgs)
    assert CountingProvider.calls == 1


def test_prompt_cache_does_not_crash_on_multimodal_list_content():
    cache = PromptCache()
    messages = [{"role": "user", "content": [{"type": "image", "source": {}}, {"type": "text", "text": "?"}]}]
    resp = LLMResponse(text="x", model="m", input_tokens=1, output_tokens=1, cost_usd=0.0)
    cache.set("m", messages, resp)
    assert cache.get("m", messages) is resp


def test_llm_gateway_rate_limiter_raises_after_burst():
    class CountingProvider:
        def complete(self, messages, *, model, **kw):
            return LLMResponse(text="r", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0)

    gw = LLMGateway(provider=CountingProvider(), rate_limiter=RateLimiter(rate_per_s=0.001, burst=1))
    gw.complete([{"role": "user", "content": "1"}])
    with pytest.raises(RuntimeError):
        gw.complete([{"role": "user", "content": "2"}])


def test_multiprovider_dispatches_by_model_name():
    class ProviderA:
        def complete(self, messages, *, model, **kw):
            return LLMResponse(text="from-a", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0)

    class ProviderB:
        def complete(self, messages, *, model, **kw):
            return LLMResponse(text="from-b", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0)

    mp = MultiProvider(by_model={"model-a": ProviderA(), "model-b": ProviderB()})
    assert mp.complete([], model="model-a").text == "from-a"
    assert mp.complete([], model="model-b").text == "from-b"


def test_pricing_registry_estimates_an_unknown_model_instead_of_metering_zero():
    """Regression: unlisted models used to silently cost $0 (a real spend
    of $22 recorded as $0 against a budget ceiling). Default policy
    ("estimate") must charge a real, non-zero amount instead."""
    registry = PricingRegistry(rates={"known-model": (1.0, 2.0)})

    known_cost = registry.calculate("known-model", input_tokens=1_000_000, output_tokens=1_000_000)
    assert known_cost == 3.0  # 1M*$1 + 1M*$2, exact registered rate

    unknown_cost = registry.calculate("mystery-model", input_tokens=1_000_000, output_tokens=1_000_000)
    assert unknown_cost > 0.0  # never silently $0 for an unlisted model


def test_pricing_registry_block_policy_raises_on_an_unlisted_model():
    from agent_foundry.llm_gateway import UnknownPricingError

    registry = PricingRegistry(unknown_model_policy="block")
    with pytest.raises(UnknownPricingError):
        registry.calculate("mystery-model", input_tokens=100, output_tokens=100)


def test_pricing_registry_allow_policy_preserves_the_original_zero_cost_behavior():
    registry = PricingRegistry(unknown_model_policy="allow")
    assert registry.calculate("mystery-model", input_tokens=1_000_000, output_tokens=1_000_000) == 0.0


def test_anthropic_and_openai_translate_the_same_generic_tool_schema():
    """The correctness fix: the SAME provider-agnostic schema from
    tool_json_schema() must translate correctly for both vendor wire formats."""
    from agent_foundry.llm_gateway import AnthropicProvider, OpenAIProvider

    spec = ToolSpec("lookup_order", "Look up an order", {"order_id": "string"}, lambda order_id: "x")
    generic = tool_json_schema(spec)

    ap = AnthropicProvider.__new__(AnthropicProvider)
    ap.pricing = PricingRegistry()
    captured_a = {}
    class FakeBlock:
        type = "text"; text = "ok"
    class FakeUsage:
        input_tokens = 1; output_tokens = 1
    class FakeAResp:
        content = [FakeBlock()]; usage = FakeUsage()
    class FakeAMessages:
        def create(self, **kw):
            captured_a.update(kw); return FakeAResp()
    class FakeAClient:
        messages = FakeAMessages()
    ap._client = FakeAClient()
    ap.complete([{"role": "user", "content": "hi"}], model="claude-sonnet-5", tools=[generic])
    assert captured_a["tools"][0]["input_schema"] == generic["parameters"]

    op = OpenAIProvider.__new__(OpenAIProvider)
    op.pricing = PricingRegistry()
    captured_o = {}
    class FakeMsg:
        content = "ok"; tool_calls = None
    class FakeChoice:
        message = FakeMsg()
    class FakeOUsage:
        prompt_tokens = 1; completion_tokens = 1
    class FakeOResp:
        choices = [FakeChoice()]; usage = FakeOUsage()
    class FakeCompletions:
        def create(self, **kw):
            captured_o.update(kw); return FakeOResp()
    class FakeChat:
        completions = FakeCompletions()
    class FakeOClient:
        chat = FakeChat()
    op._client = FakeOClient()
    op.complete([{"role": "user", "content": "hi"}], model="gpt-5", tools=[generic])
    assert captured_o["tools"][0]["type"] == "function"
    assert captured_o["tools"][0]["function"]["parameters"] == generic["parameters"]


def test_anthropic_provider_omits_system_key_entirely_when_no_system_message():
    """Regression test: a call with no {"role": "system", ...} message (e.g.
    document_store.py's image transcription, guardrails.py's LLM-judge check —
    both real call sites with no system message) used to send a literal
    `system=None` through to the Anthropic API, which rejects it outright:
    'system: Input should be a valid array' (a real 400 hit against the live
    API, not hypothetical — found while testing an image upload live). The
    fix must omit the "system" key from the request entirely rather than
    pass None."""
    from agent_foundry.llm_gateway import AnthropicProvider

    ap = AnthropicProvider.__new__(AnthropicProvider)
    ap.pricing = PricingRegistry()
    captured = {}
    class FakeBlock:
        type = "text"; text = "ok"
    class FakeUsage:
        input_tokens = 1; output_tokens = 1
    class FakeAResp:
        content = [FakeBlock()]; usage = FakeUsage()
    class FakeAMessages:
        def create(self, **kw):
            captured.update(kw); return FakeAResp()
    class FakeAClient:
        messages = FakeAMessages()
    ap._client = FakeAClient()

    ap.complete([{"role": "user", "content": "describe this image"}], model="claude-sonnet-5")

    assert "system" not in captured


def test_anthropic_provider_still_sends_system_when_present():
    """The fix above must not turn off sending system prompts on the normal
    (orchestration.py) path, which always includes one."""
    from agent_foundry.llm_gateway import AnthropicProvider

    ap = AnthropicProvider.__new__(AnthropicProvider)
    ap.pricing = PricingRegistry()
    captured = {}
    class FakeBlock:
        type = "text"; text = "ok"
    class FakeUsage:
        input_tokens = 1; output_tokens = 1
    class FakeAResp:
        content = [FakeBlock()]; usage = FakeUsage()
    class FakeAMessages:
        def create(self, **kw):
            captured.update(kw); return FakeAResp()
    class FakeAClient:
        messages = FakeAMessages()
    ap._client = FakeAClient()

    ap.complete([{"role": "system", "content": "You are a helpful agent."}, {"role": "user", "content": "hi"}], model="claude-sonnet-5")

    assert captured["system"] == "You are a helpful agent."
