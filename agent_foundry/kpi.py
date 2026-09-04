"""KPI framework — the general mechanism guardrails, eval and planning compose
with, instead of a fixed set of hardcoded categories. A builder defines any named
KPI — efficiency, conciseness, policy adherence, response time, cost, groundedness,
whatever the use case needs — with a scoring function and a target. Nothing here
imports from the rest of agent_foundry; guardrails.py, eval.py and orchestration.py
compose with it from the outside, by choice, not by inheritance.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

Direction = Literal["maximize", "minimize", "target"]

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    """Lowercase, punctuation-stripped word set — plain `.split()` treats
    "500mg" (a reply) and "500 mg" (a real document, tokenized as two words
    "500"/"mg") as non-matching, and "daily." (trailing punctuation) as a
    different word from "daily" in the source text. Against real embedded
    document text (not hand-written test fixtures) this false-negatives
    genuinely correct, well-grounded answers — found live, wiring
    reference_check_kpi's exact math into a LIVE blocking gate (healthcare/
    backend's critique step) surfaced it immediately against a real
    ChromaDB-retrieved passage, where the old hand-written KPI test fixtures
    never would have."""
    return set(_WORD_RE.findall(text.lower()))


def word_overlap(text: str, passages: list[str]) -> float:
    """Fraction of `text`'s own words that appear somewhere in `passages` —
    the actual grounding metric reference_check_kpi below uses, exposed
    standalone so a live gate (not just an offline KPI context dict) can
    reuse the identical, tested math instead of a second, drifting copy of
    the same tokenization logic."""
    if not text or not passages:
        return 0.0
    text_words = _tokenize(text)
    ref_words = {w for p in passages for w in _tokenize(p)}
    return len(text_words & ref_words) / max(len(text_words), 1)


@dataclass
class KPIResult:
    name: str
    value: float
    passed: bool


@dataclass
class KPI:
    """One named, pluggable metric.

    `score(context)` computes a raw value from whatever dict of facts you hand it
    (latency_ms, cost_usd, output_text, steps, ...). `direction` + `threshold` (and
    `target` for direction="target") decide pass/fail. `weight` is this KPI's say
    when a Planner combines several into one objective score.
    """

    name: str
    score: Callable[[dict[str, Any]], float]
    direction: Direction = "maximize"
    threshold: float | None = None
    target: float | None = None
    weight: float = 1.0

    def evaluate(self, context: dict[str, Any]) -> KPIResult:
        value = self.score(context)
        passed = True
        if self.direction == "maximize" and self.threshold is not None:
            passed = value >= self.threshold
        elif self.direction == "minimize" and self.threshold is not None:
            passed = value <= self.threshold
        elif self.direction == "target" and self.target is not None and self.threshold is not None:
            passed = abs(value - self.target) <= self.threshold
        return KPIResult(name=self.name, value=value, passed=passed)


@dataclass
class KPIBoard:
    """A registered set of KPIs a builder plays with — add or remove any dimension
    without touching guardrails.py, eval.py or orchestration.py."""

    kpis: dict[str, KPI] = field(default_factory=dict)

    def register(self, kpi: KPI) -> None:
        self.kpis[kpi.name] = kpi

    def remove(self, name: str) -> None:
        self.kpis.pop(name, None)

    def evaluate_all(self, context: dict[str, Any]) -> list[KPIResult]:
        return [kpi.evaluate(context) for kpi in self.kpis.values()]

    def failing(self, context: dict[str, Any]) -> list[KPIResult]:
        return [r for r in self.evaluate_all(context) if not r.passed]

    def weighted_score(self, context: dict[str, Any]) -> float:
        """A single composite number — what a Planner optimizes for."""
        results = self.evaluate_all(context)
        total_weight = sum(self.kpis[r.name].weight for r in results) or 1.0
        return sum(r.value * self.kpis[r.name].weight for r in results) / total_weight


# ---- Reference KPI catalogue — the "all the KPIs I can play with" starter set ----
# Every one of these is just a KPI(...) call; write your own the same way for
# anything domain-specific (a fintech agent's "AML flag rate", a support agent's
# "first-contact resolution", whatever the use case needs).

def efficiency_kpi(*, threshold: float = 0.5, weight: float = 1.0) -> KPI:
    """1.0 = finished in the fewest possible steps; 0.0 = used the entire step budget."""
    def score(ctx: dict[str, Any]) -> float:
        steps, max_steps = ctx.get("steps", 1), ctx.get("max_steps", 1) or 1
        return max(0.0, 1.0 - (steps / max_steps))
    return KPI(name="efficiency", score=score, direction="maximize", threshold=threshold, weight=weight)


def conciseness_kpi(*, target_chars: int = 400, tolerance: int = 200, weight: float = 1.0) -> KPI:
    def score(ctx: dict[str, Any]) -> float:
        return float(len(ctx.get("output_text", "")))
    return KPI(name="conciseness", score=score, direction="target", target=target_chars, threshold=tolerance, weight=weight)


def policy_adherence_kpi(*, threshold: float = 1.0, weight: float = 1.0) -> KPI:
    def score(ctx: dict[str, Any]) -> float:
        return 1.0 if ctx.get("guardrail_violations", 0) == 0 else 0.0
    return KPI(name="policy_adherence", score=score, direction="maximize", threshold=threshold, weight=weight)


def completeness_kpi(*, judge: Callable[[str], float] | None = None, required: Callable[[dict[str, Any]], list[str]] | None = None, threshold: float = 0.7, weight: float = 1.0) -> KPI:
    """A different axis from correctness: an answer can be entirely true and
    still leave out half of what was asked. Two methods, same as correctness:
    pass `judge` (an LLM-judge callable, e.g. from make_llm_judge(llm,
    "completeness")) for a judgment call, or `required` (ctx -> the list of
    elements the answer must cover, e.g. every sub-question) for a deterministic
    checklist match — the fraction of required items actually present in
    ctx["output_text"]. Exactly one of the two must be given."""
    if (judge is None) == (required is None):
        raise ValueError("completeness_kpi needs exactly one of judge= or required=")

    def score(ctx: dict[str, Any]) -> float:
        if judge is not None:
            return judge(ctx.get("output_text", ""))
        items = required(ctx)
        if not items:
            return 1.0
        text = ctx.get("output_text", "").lower()
        covered = sum(1 for item in items if item.lower() in text)
        return covered / len(items)

    return KPI(name="completeness", score=score, direction="maximize", threshold=threshold, weight=weight)


def response_time_kpi(*, max_ms: float = 3000.0, weight: float = 1.0) -> KPI:
    def score(ctx: dict[str, Any]) -> float:
        return float(ctx.get("latency_ms", 0.0))
    return KPI(name="response_time", score=score, direction="minimize", threshold=max_ms, weight=weight)


def cost_kpi(*, max_usd: float = 0.05, weight: float = 1.0) -> KPI:
    def score(ctx: dict[str, Any]) -> float:
        return float(ctx.get("cost_usd", 0.0))
    return KPI(name="cost", score=score, direction="minimize", threshold=max_usd, weight=weight)


def groundedness_kpi(*, threshold: float = 0.8, weight: float = 1.0) -> KPI:
    def score(ctx: dict[str, Any]) -> float:
        return float(ctx.get("groundedness_score", 1.0))
    return KPI(name="groundedness", score=score, direction="maximize", threshold=threshold, weight=weight)


def user_satisfaction_kpi(*, threshold: float = 0.7, weight: float = 1.0) -> KPI:
    def score(ctx: dict[str, Any]) -> float:
        return float(ctx.get("csat_score", 1.0))
    return KPI(name="user_satisfaction", score=score, direction="maximize", threshold=threshold, weight=weight)


def task_success_kpi(*, threshold: float = 1.0, weight: float = 1.0) -> KPI:
    def score(ctx: dict[str, Any]) -> float:
        return 1.0 if ctx.get("task_completed") else 0.0
    return KPI(name="task_success", score=score, direction="maximize", threshold=threshold, weight=weight)


def tool_error_rate_kpi(*, max_rate: float = 0.1, weight: float = 1.0) -> KPI:
    def score(ctx: dict[str, Any]) -> float:
        return float(ctx.get("tool_error_rate", 0.0))
    return KPI(name="tool_error_rate", score=score, direction="minimize", threshold=max_rate, weight=weight)


def hallucination_rate_kpi(*, max_rate: float = 0.05, weight: float = 1.0) -> KPI:
    def score(ctx: dict[str, Any]) -> float:
        return float(ctx.get("hallucination_rate", 0.0))
    return KPI(name="hallucination_rate", score=score, direction="minimize", threshold=max_rate, weight=weight)


def llm_judge_kpi(
    name: str,
    *,
    judge: Callable[[str], float],
    direction: Direction = "maximize",
    threshold: float | None = None,
    weight: float = 1.0,
) -> KPI:
    """LLM-as-judge: `judge` is any callable that takes the text to evaluate
    (ctx["output_text"] by default) and returns a 0..1 score — typically a small
    wrapper around an LLM call asking it to rate one criterion (see
    llm_gateway.make_llm_judge for a ready-made one). Composes with every other
    KPI exactly the same way: register it on a KPIBoard, give it a weight, done —
    "correctness 60% (LLM judge) + efficiency 20% + conciseness 20%" is just three
    KPI registrations with weights that happen to sum to 1.0."""

    def score(ctx: dict[str, Any]) -> float:
        return judge(ctx.get("output_text", ""))

    return KPI(name=name, score=score, direction=direction, threshold=threshold, weight=weight)


def schema_valid_kpi(name: str, *, schema: dict[str, Any], weight: float = 1.0) -> KPI:
    """Deterministic method: validates ctx["output_text"] (parsed as JSON)
    against a real JSON Schema — for an agent that promises structured output.
    Requires `pip install jsonschema`."""
    import json

    import jsonschema

    def score(ctx: dict[str, Any]) -> float:
        try:
            data = json.loads(ctx.get("output_text", ""))
            jsonschema.validate(data, schema)
            return 1.0
        except (json.JSONDecodeError, jsonschema.ValidationError):
            return 0.0

    return KPI(name=name, score=score, direction="maximize", threshold=1.0, weight=weight)


def db_match_kpi(name: str, *, lookup: Callable[[dict[str, Any]], tuple[Any, Any]], weight: float = 1.0) -> KPI:
    """Deterministic method: `lookup(ctx)` returns (claimed, actual) — e.g. a
    value parsed out of the output text vs. the real value from a database (see
    data_connectors.py) or anywhere else. Scores 1.0 on an exact match, 0.0
    otherwise — no LLM call, no ambiguity, a hard fact check. kpi.py doesn't need
    to know what a database even is: the caller's `lookup` closure does the
    fetching, this just compares the two values."""

    def score(ctx: dict[str, Any]) -> float:
        claimed, actual = lookup(ctx)
        return 1.0 if claimed == actual else 0.0

    return KPI(name=name, score=score, direction="maximize", threshold=1.0, weight=weight)


def reference_check_kpi(name: str, *, references: Callable[[dict[str, Any]], list[str]], threshold: float = 0.3, weight: float = 1.0) -> KPI:
    """Deterministic method: how much of the output's own wording is actually
    supported by cited reference passages (keyword-overlap grounding) —
    `references(ctx)` returns the passages to check against, wherever you keep
    them (context.py's semantic memory, a document store, a fixed list)."""

    def score(ctx: dict[str, Any]) -> float:
        return word_overlap(ctx.get("output_text", ""), references(ctx))

    return KPI(name=name, score=score, direction="maximize", threshold=threshold, weight=weight)


_NUMERIC_CLAIM_RE = re.compile(
    r"\d[\d.,]*\s?(?:mg|ml|mcg|g|kg|%|mmol/l|mg/dl|mmhg|bpm|times?|tablets?|days?|hours?|weeks?)\b",
    re.IGNORECASE,
)


def _normalize_claim(text: str) -> str:
    return re.sub(r"\s+", "", text.lower())


def fact_check_kpi(name: str, *, references: Callable[[dict[str, Any]], list[str]], weight: float = 1.0) -> KPI:
    """Deterministic, and deliberately stricter than reference_check_kpi's
    fuzzy bag-of-words overlap: extracts explicit NUMERIC claims from the
    output — dosages, frequencies, durations ("500mg", "twice daily", "48
    hours") — and checks each one appears, verbatim (whitespace-
    insensitive), somewhere in the reference passages. A wrong number
    ("500mg" when the source actually says "50mg") is exactly the error
    class word_overlap's bag-of-words math can't catch — "500mg" and
    "50mg" still share almost every token with the source text. Scores 1.0
    when the output makes no numeric claims (nothing to get wrong) or every
    claim is verbatim-supported; the fraction supported otherwise."""

    def score(ctx: dict[str, Any]) -> float:
        text = ctx.get("output_text", "")
        claims = _NUMERIC_CLAIM_RE.findall(text)
        if not claims:
            return 1.0
        source = _normalize_claim(" ".join(references(ctx)))
        supported = sum(1 for c in claims if _normalize_claim(c) in source)
        return supported / len(claims)

    return KPI(name=name, score=score, direction="maximize", threshold=1.0, weight=weight)


def composite_grounding_kpi(
    name: str,
    *,
    references: Callable[[dict[str, Any]], list[str]],
    judge: Callable[[str, list[str]], float] | None = None,
    weight_reference: float = 0.4,
    weight_fact: float = 0.4,
    weight_judge: float = 0.2,
    threshold: float = 0.5,
    weight: float = 1.0,
) -> KPI:
    """Couples three independent grounding signals into one score rather than
    trusting any single method alone — each catches a different failure mode
    the others miss:
      - reference (word_overlap, via reference_check_kpi): fuzzy bag-of-
        words — catches wholesale unsupported text, cheap, no LLM call.
      - fact (fact_check_kpi): strict verbatim numeric-claim check — catches
        a specific wrong number ("500mg" vs the source's "50mg") that
        word_overlap's fuzzy math shares almost every token with anyway.
      - judge (LLM-as-judge, optional): catches semantic/contextual
        mistakes neither deterministic check can (e.g. right numbers, wrong
        drug entirely) — pass a `(text, references) -> float` callable, e.g.
        agent_foundry.llm_gateway.make_grounding_judge(llm). Omitted
        (weight_judge forced to 0) when no `judge` is given, so this stays
        fully deterministic and free to run without one.

    Weights default to reference=0.4/fact=0.4/judge=0.2 — the two
    deterministic, free, always-available checks carry most of the weight;
    judge is real signal but the only non-deterministic, costed one.

    fact's weight is dynamically excluded (not blended in at all, not
    silently zero-scored either) on any output with no numeric claims to
    check — fact_check_kpi itself correctly auto-passes (1.0) that case
    ("nothing to get wrong"), but blending a flat 1.0 in at full weight
    would pull the COMPOSITE up regardless of how ungrounded the prose
    itself is, defeating the very case reference exists to catch (a
    wholesale-unrelated, hallucinated answer that happens to contain no
    numbers). Found live wiring this into a real blocking escalation gate:
    "completely unrelated text sharing no words with the uploaded
    prescription whatsoever" — reference alone scored ~0.0 as intended, but
    blended with fact's auto-pass 1.0 at 40% weight, the composite came out
    ~0.5, well above the gate's escalate_threshold. Excluding fact's weight
    (not its score) when there's nothing for it to check keeps the
    composite's sensitivity exactly where reference alone already had it,
    while still using fact fully whenever the output DOES make a specific,
    checkable numeric claim."""
    if judge is None:
        weight_judge = 0.0
    _fact = fact_check_kpi(f"_{name}_fact", references=references)
    _reference = reference_check_kpi(f"_{name}_reference", references=references, threshold=0.0)

    def score(ctx: dict[str, Any]) -> float:
        text = ctx.get("output_text", "")
        parts: list[tuple[float, float]] = [(_reference.score(ctx), weight_reference)]
        if _NUMERIC_CLAIM_RE.search(text):
            parts.append((_fact.score(ctx), weight_fact))
        if judge is not None:
            parts.append((judge(text, references(ctx)), weight_judge))
        total = sum(w for _, w in parts) or 1.0
        return sum(s * w for s, w in parts) / total

    return KPI(name=name, score=score, direction="maximize", threshold=threshold, weight=weight)


def complexity_kpi(*, threshold: float = 0.6) -> KPI:
    """Reports/gates a complexity score you've already computed (ctx["complexity"],
    0..1) — this KPI doesn't invent a complexity measure, planning.StrategySelector
    is what acts on it."""
    def score(ctx: dict[str, Any]) -> float:
        return float(ctx.get("complexity", 0.0))
    return KPI(name="complexity", score=score, direction="minimize", threshold=threshold)


def risk_kpi(*, threshold: float = 0.5) -> KPI:
    def score(ctx: dict[str, Any]) -> float:
        return float(ctx.get("risk", 0.0))
    return KPI(name="risk", score=score, direction="minimize", threshold=threshold)
