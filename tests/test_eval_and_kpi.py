import json
import os
import tempfile

from agent_foundry.eval import EvalHarness, JSONLEvalSink
from agent_foundry.kpi import (
    KPI, KPIBoard, completeness_kpi, composite_grounding_kpi, cost_kpi, db_match_kpi,
    efficiency_kpi, fact_check_kpi, llm_judge_kpi, reference_check_kpi, schema_valid_kpi, word_overlap,
)


def test_eval_harness_records_and_summarizes():
    h = EvalHarness()
    h.record("atomic", "think", "responded", 1.0)
    h.record("atomic", "think", "responded", 0.5)
    h.record("flow", "thread-1", "completed", 1.0)
    assert h.score_for("atomic") == 0.75
    assert h.summary()["flow"] == 1.0
    assert h.summary()["overall"] == 0.0  # nothing recorded there


def test_jsonl_eval_sink_writes_real_lines():
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    try:
        sink = JSONLEvalSink(path=path)
        sink.record("component", "lookup_order", "success", 1.0, ok=True)
        lines = open(path).read().strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["metric"] == "success" and record["ok"] is True
    finally:
        os.unlink(path)


def test_kpi_board_register_remove_and_custom_kpi():
    board = KPIBoard()
    board.register(efficiency_kpi())
    board.register(cost_kpi())
    assert set(board.kpis) == {"efficiency", "cost"}
    board.remove("cost")
    assert set(board.kpis) == {"efficiency"}

    board.register(KPI(name="aml_flag_rate", score=lambda ctx: ctx["flags"] / max(ctx["txns"], 1), direction="minimize", threshold=0.02))
    result = board.evaluate_all({"flags": 3, "txns": 100, "steps": 1, "max_steps": 10})
    assert any(r.name == "aml_flag_rate" and abs(r.value - 0.03) < 1e-9 for r in result)


def test_weighted_composite_differentiates_good_from_bad():
    board = KPIBoard()
    board.register(llm_judge_kpi("correctness", judge=lambda text: 0.9 if "GOOD" in text else 0.2, weight=0.6))
    board.register(efficiency_kpi(weight=0.4))
    good = {"output_text": "GOOD answer", "steps": 2, "max_steps": 10}
    bad = {"output_text": "bad answer", "steps": 9, "max_steps": 10}
    assert board.weighted_score(good) > board.weighted_score(bad)


def test_db_match_kpi_catches_a_fabricated_claim():
    real_status = {"A100": "shipped"}
    kpi = db_match_kpi("order_status_accurate", lookup=lambda ctx: (ctx["claimed"], real_status[ctx["order_id"]]))
    correct = kpi.evaluate({"claimed": "shipped", "order_id": "A100"})
    fabricated = kpi.evaluate({"claimed": "delivered", "order_id": "A100"})
    assert correct.passed and not fabricated.passed


def test_reference_check_kpi_grounds_against_documents():
    kpi = reference_check_kpi("grounded", references=lambda ctx: ["standard refund turnaround is 3-5 business days"])
    grounded = kpi.evaluate({"output_text": "standard refund turnaround is 3-5 business days"})
    ungrounded = kpi.evaluate({"output_text": "refunds are always instant and guaranteed"})
    assert grounded.value > ungrounded.value


def test_word_overlap_ignores_punctuation_and_spacing_differences():
    """Regression test: plain `.split()` treated "500mg" (a reply) and
    "500 mg" (real document text, two separate tokens) as non-matching, and
    trailing punctuation ("daily." vs "daily") as a different word entirely
    — found live wiring this exact math into a blocking gate
    (healthcare/backend's critique step), which false-negatived a genuinely
    correct, well-grounded answer against a real ChromaDB-retrieved
    passage. Word-boundary tokenization must treat these as matches."""
    passage = "Tab. Metformin SR 500 mg — 1 tablet ORALLY, TWICE DAILY, after food."
    reply = "You are prescribed Metformin 500mg twice daily."
    # the naive `.split()` version scored ~0.286 here (below reference_check_kpi's
    # own 0.3 default threshold) purely from "500mg"/"500 mg" and "daily."/"daily"
    # mismatches — exactly what made this a live, user-facing false negative,
    # not just a cosmetic scoring difference.
    assert word_overlap(reply, [passage]) >= 0.3


def test_word_overlap_empty_text_or_passages_scores_zero():
    assert word_overlap("", ["something"]) == 0.0
    assert word_overlap("something", []) == 0.0


def test_fact_check_kpi_catches_a_wrong_number_word_overlap_would_miss():
    """The actual gap fact_check_kpi exists to close: "500mg" and "50mg"
    share almost every token ("mg" plus most digit characters overlap in
    naive fuzzy matching), so a bag-of-words check alone can't reliably
    catch a specific wrong dosage. fact_check_kpi extracts the numeric
    claim and requires a VERBATIM (whitespace-insensitive) match against
    the source instead."""
    kpi = fact_check_kpi("dosage_facts", references=lambda ctx: ["Metformin SR 500 mg — 1 tablet twice daily"])
    correct = kpi.evaluate({"output_text": "Take Metformin 500mg twice daily."})
    wrong = kpi.evaluate({"output_text": "Take Metformin 50mg twice daily."})
    assert correct.value == 1.0
    assert wrong.value == 0.0


def test_fact_check_kpi_passes_when_the_output_makes_no_numeric_claims():
    kpi = fact_check_kpi("dosage_facts", references=lambda ctx: ["some reference text"])
    result = kpi.evaluate({"output_text": "Please consult your doctor about your medication."})
    assert result.value == 1.0


def test_composite_grounding_kpi_combines_reference_and_fact_checks():
    """Real proof the composite actually blends both deterministic signals,
    not just one of them — a wrong number tanks the composite even though
    the surrounding prose still overlaps heavily with the source (which is
    exactly the case reference_check_kpi's fuzzy math alone would score
    deceptively high)."""
    references = lambda ctx: ["Metformin SR 500 mg — 1 tablet twice daily, take with food"]
    kpi = composite_grounding_kpi("grounding", references=references, weight_reference=0.5, weight_fact=0.5, threshold=0.5)

    correct = kpi.evaluate({"output_text": "Take Metformin 500mg twice daily, with food."})
    wrong_number = kpi.evaluate({"output_text": "Take Metformin 5000mg twice daily, with food."})

    assert correct.passed
    assert not wrong_number.passed
    assert correct.value > wrong_number.value


def test_composite_grounding_kpi_couples_in_an_evidence_aware_judge():
    """judge here is a (text, references) -> float callable (NOT llm_judge_kpi's
    single-arg shape) — real proof both the references and the judge's own
    return value flow into the final composite score."""
    seen = {}

    def fake_judge(text, references):
        seen["text"] = text
        seen["references"] = references
        return 0.4  # deliberately below what the deterministic checks alone would give

    references = lambda ctx: ["Metformin SR 500 mg — 1 tablet twice daily"]
    kpi = composite_grounding_kpi(
        "grounding", references=references, judge=fake_judge,
        weight_reference=0.4, weight_fact=0.4, weight_judge=0.2, threshold=0.0,
    )
    only_deterministic = composite_grounding_kpi("grounding_no_judge", references=references, weight_reference=0.4, weight_fact=0.4, weight_judge=0.2, threshold=0.0)

    text = "Take Metformin 500mg twice daily."
    with_judge = kpi.evaluate({"output_text": text})
    without_judge = only_deterministic.evaluate({"output_text": text})

    assert seen["text"] == text
    assert seen["references"] == ["Metformin SR 500 mg — 1 tablet twice daily"]
    assert with_judge.value < without_judge.value  # the low judge score pulled the composite down


def test_composite_grounding_kpi_does_not_let_a_numberless_hallucination_hide_behind_facts_auto_pass():
    """Regression test for a real bug found live wiring this into a
    blocking escalation gate: fact_check_kpi correctly auto-passes (1.0)
    an output with no numeric claims ("nothing to get wrong"), but the
    first composite implementation blended that flat 1.0 in at full
    weight regardless — so a wholesale-unrelated, hallucinated answer
    with no numbers in it scored ~0.5 (reference's ~0.0 averaged with
    fact's auto-pass 1.0) instead of ~0.0, silently defeating the escalate
    gate for exactly the case reference alone already caught correctly.
    fact's weight must be excluded (not scored as 1.0) when there's
    nothing for it to check."""
    references = lambda ctx: ["standard refund turnaround is 3-5 business days"]
    kpi = composite_grounding_kpi("grounding", references=references, weight_reference=0.4, weight_fact=0.4, threshold=0.3)

    hallucinated = kpi.evaluate({"output_text": "Completely unrelated text sharing no words with the source whatsoever."})
    reference_only = reference_check_kpi("ref_only", references=references, threshold=0.3)
    reference_only_result = reference_only.evaluate({"output_text": "Completely unrelated text sharing no words with the source whatsoever."})

    assert not hallucinated.passed
    # the composite's score for a numberless, ungrounded answer must track
    # reference alone (fact excluded), not be pulled up by fact's auto-pass
    assert abs(hallucinated.value - reference_only_result.value) < 0.01


def test_completeness_kpi_deterministic_checklist():
    kpi = completeness_kpi(required=lambda ctx: ["amount", "timeline"])
    full = kpi.evaluate({"output_text": "the amount is $20 and the timeline is 3 days"})
    partial = kpi.evaluate({"output_text": "the amount is $20"})
    assert full.passed and not partial.passed


def test_schema_valid_kpi_catches_malformed_output():
    schema = {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}
    kpi = schema_valid_kpi("out_schema", schema=schema)
    assert kpi.evaluate({"output_text": '{"order_id": "A100"}'}).passed
    assert not kpi.evaluate({"output_text": "not json"}).passed
    assert not kpi.evaluate({"output_text": "{}"}).passed
