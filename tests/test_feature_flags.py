from agent_foundry.contracts import Identity
from agent_foundry.feature_flags import StaticFeatureFlagProvider


def test_missing_flag_returns_default():
    flags = StaticFeatureFlagProvider()
    assert flags.is_enabled("new_prompt_v2") is False
    assert flags.is_enabled("new_prompt_v2", default=True) is True


def test_boolean_flag_is_all_or_nothing():
    flags = StaticFeatureFlagProvider({"new_prompt_v2": True})
    a = Identity(id="user-a", tenant_id="acme")
    b = Identity(id="user-b", tenant_id="acme")
    assert flags.is_enabled("new_prompt_v2", identity=a) is True
    assert flags.is_enabled("new_prompt_v2", identity=b) is True


def test_percentage_rollout_is_stable_per_identity():
    flags = StaticFeatureFlagProvider({"new_prompt_v2": 50})
    identity = Identity(id="user-a", tenant_id="acme")
    first = flags.is_enabled("new_prompt_v2", identity=identity)
    for _ in range(20):
        assert flags.is_enabled("new_prompt_v2", identity=identity) == first


def test_percentage_rollout_splits_across_many_identities():
    flags = StaticFeatureFlagProvider({"new_prompt_v2": 50})
    identities = [Identity(id=f"user-{i}", tenant_id="acme") for i in range(200)]
    on = sum(1 for i in identities if flags.is_enabled("new_prompt_v2", identity=i))
    assert 60 < on < 140  # roughly half, not all-or-nothing


def test_zero_percent_rollout_is_always_off():
    flags = StaticFeatureFlagProvider({"new_prompt_v2": 0})
    identity = Identity(id="user-a", tenant_id="acme")
    assert flags.is_enabled("new_prompt_v2", identity=identity) is False


def test_hundred_percent_rollout_is_always_on():
    flags = StaticFeatureFlagProvider({"new_prompt_v2": 100})
    identity = Identity(id="user-a", tenant_id="acme")
    assert flags.is_enabled("new_prompt_v2", identity=identity) is True


def test_set_updates_a_flag():
    flags = StaticFeatureFlagProvider()
    flags.set("new_prompt_v2", True)
    assert flags.is_enabled("new_prompt_v2") is True
