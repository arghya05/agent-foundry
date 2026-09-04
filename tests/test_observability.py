from agent_foundry.observability import CostLedger, Metrics, Tracer, check_alerts, render_dashboard


def test_tracer_span_records_duration_and_cost():
    t = Tracer("t1")
    with t.span("orchestration.think") as s:
        s["attributes"].update(cost_usd=0.01)
    assert t.total_cost_usd() == 0.01
    assert t.spans[0]["duration_ms"] >= 0


def test_metrics_percentile_and_tool_error_rate():
    t = Tracer("t1")
    for ok in (True, True, False):
        with t.span("orchestration.act", tool="x") as s:
            s["attributes"].update(ok=ok)
    m = Metrics(t)
    assert m.tool_error_rate() == 1 / 3


def test_check_alerts_flags_high_cost_and_error_rate():
    t = Tracer("t1")
    with t.span("orchestration.act", tool="x") as s:
        s["attributes"].update(ok=False)
    m = Metrics(t)
    alerts = check_alerts(m, budget_cost_usd=0.9, max_cost_usd=1.0)
    assert any("cost" in a for a in alerts)
    assert any("error rate" in a for a in alerts)


def test_cost_ledger_tracks_per_completed_task():
    ledger = CostLedger()
    ledger.close_task(thread_id="t1", tenant_id="acme", cost_usd=0.02, steps=3, outcome="completed")
    ledger.close_task(thread_id="t2", tenant_id="acme", cost_usd=0.04, steps=5, outcome="completed")
    assert len(ledger.completed) == 2
    assert abs(ledger.cost_per_task() - 0.03) < 1e-9
    assert ledger.by_tenant() == {"acme": 0.06}


def test_render_dashboard_includes_cost_and_eval():
    from agent_foundry.eval import EvalHarness
    from agent_foundry.runtime import RunBudget
    from agent_foundry.contracts import Policy

    t = Tracer("t1")
    ev = EvalHarness()
    ev.record("atomic", "think", "responded", 1.0)
    budget = RunBudget(Policy(max_cost_usd_per_thread=1.0))
    budget.spend(0.1)
    out = render_dashboard(tracer=t, eval_harness=ev, budget=budget)
    assert "t1" in out and "0.1000" in out
