import tempfile

import pytest

from agent_foundry.prompts import VersionedPromptLibrary
from agent_foundry.versioning import FileVersionStore


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield FileVersionStore(tmp)


def test_publish_and_get_current(store):
    store.publish("support_agent", "You are a support agent v1.")
    assert store.get("support_agent") == "You are a support agent v1."


def test_publish_again_moves_current(store):
    store.publish("support_agent", "v1 text")
    store.publish("support_agent", "v2 text")
    assert store.get("support_agent") == "v2 text"


def test_get_specific_version_ignores_current(store):
    v1 = store.publish("support_agent", "v1 text")
    store.publish("support_agent", "v2 text")
    assert store.get("support_agent", version=v1) == "v1 text"


def test_rollback_moves_current_pointer_back(store):
    v1 = store.publish("support_agent", "v1 text")
    store.publish("support_agent", "v2 text — has a bug")
    store.rollback("support_agent", version=v1)
    assert store.get("support_agent") == "v1 text"


def test_rollback_to_unknown_version_raises(store):
    store.publish("support_agent", "v1 text")
    with pytest.raises(ValueError):
        store.rollback("support_agent", version="does-not-exist")


def test_history_lists_versions_with_labels_and_current_flag(store):
    v1 = store.publish("support_agent", "v1 text", label="initial")
    v2 = store.publish("support_agent", "v2 text", label="tone fix")
    history = store.history("support_agent")
    by_version = {h["version"]: h for h in history}
    assert by_version[v1]["label"] == "initial" and by_version[v1]["current"] is False
    assert by_version[v2]["label"] == "tone fix" and by_version[v2]["current"] is True


def test_get_with_no_versions_raises(store):
    with pytest.raises(FileNotFoundError):
        store.get("never_published")


def test_versioned_prompt_library_drop_in_for_get(store):
    store.publish("support_agent", "Hello {name}, you are a support agent.")
    library = VersionedPromptLibrary(store=store)
    assert library.get("support_agent", name="Alice") == "Hello Alice, you are a support agent."


def test_versioned_prompt_library_publish_and_rollback(store):
    library = VersionedPromptLibrary(store=store)
    v1 = library.publish("support_agent", "v1 prompt", label="initial")
    library.publish("support_agent", "v2 prompt — regressed tone")
    assert library.get("support_agent") == "v2 prompt — regressed tone"
    library.rollback("support_agent", version=v1)
    assert library.get("support_agent") == "v1 prompt"
    assert len(library.history("support_agent")) == 2


def test_versioned_prompt_library_uses_locale_variant_when_published(store):
    store.publish("support_agent", "You are a support agent.")
    store.publish("support_agent.hi-IN", "Aap ek sahayak agent hain.")
    library = VersionedPromptLibrary(store=store)
    assert library.get("support_agent", locale="hi-IN") == "Aap ek sahayak agent hain."


def test_versioned_prompt_library_falls_back_when_locale_variant_never_published(store):
    store.publish("support_agent", "You are a support agent.")
    library = VersionedPromptLibrary(store=store)
    assert library.get("support_agent", locale="fr-FR") == "You are a support agent."
