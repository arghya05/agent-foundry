"""Prompt loading — prompts live as plain text/markdown files, not Python string
literals, so a non-engineer can edit an agent's instructions, a diff on a prompt
file is reviewable on its own, and the same prompt can be reused or A/B'd without
touching orchestration code.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .versioning import VersionStore


def load_prompt(path: str | Path, /, **variables: str) -> str:
    text = Path(path).read_text()
    return text.format(**variables) if variables else text


@dataclass
class PromptLibrary:
    """Loads prompts by name from a directory — add a new agent's prompt by
    dropping a new `<name>.md` file in; no code change required."""

    directory: Path

    def __post_init__(self) -> None:
        self.directory = Path(self.directory)

    def get(self, name: str, /, *, locale: str | None = None, **variables: str) -> str:
        # `name` is positional-only so a prompt template variable literally
        # called {name} (a very plausible one) can't collide with it.
        # locale is optional and additive: `<name>.<locale>.md` (e.g.
        # healthcare_assistant.hi-IN.md) is used if present, otherwise this
        # falls back to the base `<name>.md` unchanged — no locale variant
        # file required for every prompt.
        if locale:
            localized = self.directory / f"{name}.{locale}.md"
            if localized.exists():
                return load_prompt(localized, **variables)
        return load_prompt(self.directory / f"{name}.md", **variables)


@dataclass
class VersionedPromptLibrary:
    """Same .get(name, **variables) -> str shape as PromptLibrary, but backed by
    a versioning.VersionStore instead of flat files — publish a new prompt
    version, roll back instantly if it underperforms, keep full history. Drop-in
    anywhere only .get() is used (orchestration.py never imports PromptLibrary
    directly — it just receives a system_prompt string)."""

    store: "VersionStore"

    def get(self, name: str, /, *, locale: str | None = None, **variables: str) -> str:
        # Same locale-variant convention as PromptLibrary.get: a locale-suffixed
        # published name (e.g. "healthcare_assistant.hi-IN") is used if it has
        # ever been published to this store, otherwise falls back to `name`.
        text = None
        if locale:
            try:
                text = self.store.get(f"{name}.{locale}")
            except FileNotFoundError:
                pass
        if text is None:
            text = self.store.get(name)
        return text.format(**variables) if variables else text

    def publish(self, name: str, content: str, *, label: str = "") -> str:
        return self.store.publish(name, content, label=label)

    def rollback(self, name: str, *, version: str) -> None:
        self.store.rollback(name, version=version)

    def history(self, name: str) -> list[dict[str, str]]:
        return self.store.history(name)
