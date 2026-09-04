import pytest

from agent_foundry.contracts import Identity, LLMResponse, Policy, ToolSpec
from agent_foundry.llm_gateway import (
    LLMGateway, MultiProvider, PromptCache, RateLimitExceeded,
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


def test_anthropic_and_openai_translate_the_same_generic_tool_schema():
    """The correctness fix: the SAME provider-agnostic schema from
    tool_json_schema() must translate correctly for both vendor wire formats."""
    from agent_foundry.llm_gateway import AnthropicProvider, OpenAIProvider

    spec = ToolSpec("lookup_order", "Look up an order", {"order_id": "string"}, lambda order_id: "x")
    generic = tool_json_schema(spec)

    ap = AnthropicProvider.__new__(AnthropicProvider)
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
