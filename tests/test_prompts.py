import tempfile
from pathlib import Path

from agent_foundry.prompts import PromptLibrary


def test_prompt_library_falls_back_to_base_file_without_a_locale_variant():
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "support_agent.md").write_text("You are a support agent.")
        library = PromptLibrary(directory=tmp)
        assert library.get("support_agent") == "You are a support agent."
        assert library.get("support_agent", locale="hi-IN") == "You are a support agent."


def test_prompt_library_uses_locale_variant_file_when_present():
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "support_agent.md").write_text("You are a support agent.")
        Path(tmp, "support_agent.hi-IN.md").write_text("Aap ek sahayak agent hain.")
        library = PromptLibrary(directory=tmp)
        assert library.get("support_agent", locale="hi-IN") == "Aap ek sahayak agent hain."
        assert library.get("support_agent") == "You are a support agent."
