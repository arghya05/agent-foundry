"""Blackboard — a shared reasoning workspace multiple agents read and write to,
instead of passing messages directly to each other. No LangGraph dependency here
on purpose: usable standalone, or wired into a graph via
orchestration.build_blackboard_graph.
"""
from __future__ import annotations

from dataclasses import dataclass, field

_SECTIONS = {
    "fact": "facts",
    "hypothesis": "hypotheses",
    "evidence": "evidence",
    "task": "tasks",
    "contradiction": "contradictions",
    "question": "open_questions",
}


@dataclass
class Blackboard:
    facts: list[str] = field(default_factory=list)
    hypotheses: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    tasks: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)

    def post(self, section: str, text: str) -> bool:
        attr = _SECTIONS.get(section)
        if attr is None:
            return False
        getattr(self, attr).append(text)
        return True

    def render(self) -> str:
        labels = ("Facts", "Hypotheses", "Evidence", "Tasks", "Contradictions", "Open Questions")
        lists = (self.facts, self.hypotheses, self.evidence, self.tasks, self.contradictions, self.open_questions)
        return "\n".join(
            f"{label}:\n" + "\n".join(f"- {x}" for x in items) if items else f"{label}: (none)"
            for label, items in zip(labels, lists)
        )


def parse_post(content: str) -> tuple[str, str] | None:
    """Parses the `POST <section>: <text>` convention agents use to write to the
    board. Section must be one of _SECTIONS' keys."""
    if not isinstance(content, str) or not content.startswith("POST "):
        return None
    section, _, text = content[len("POST "):].partition(":")
    section = section.strip().lower()
    return (section, text.strip()) if section in _SECTIONS else None
