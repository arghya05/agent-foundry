from agent_foundry.kpi import KPIBoard, cost_kpi
from agent_foundry.planning import BanditSelector, DecisionMaker, Objective, Planner, StrategyRule, StrategySelector


def test_planner_picks_lower_cost_option():
    board = KPIBoard()
    board.register(cost_kpi())
    planner = Planner(board=board, objectives=[Objective("cost", weight=1.0)])
    candidates = {"cheap": {"cost_usd": 0.001}, "expensive": {"cost_usd": 0.05}}
    assert planner.choose(candidates) == "cheap"
    assert planner.rank(candidates) == ["cheap", "expensive"]


def test_bandit_selector_learns_from_observed_rewards():
    bandit = BanditSelector(epsilon=0.0)
    bandit.observe("a", 0.2)
    bandit.observe("b", 0.9)
    bandit.observe("b", 0.8)
    assert bandit.choose({"a": {}, "b": {}}) == "b"


def test_planner_and_bandit_both_satisfy_decisionmaker_protocol():
    def use_as_decision_maker(dm: DecisionMaker, candidates: dict) -> str:
        return dm.choose(candidates)

    board = KPIBoard()
    board.register(cost_kpi())
    planner = Planner(board=board, objectives=[Objective("cost")])
    bandit = BanditSelector(epsilon=0.0)
    bandit.observe("x", 1.0)
    candidates = {"x": {"cost_usd": 0.01}, "y": {"cost_usd": 0.01}}
    assert use_as_decision_maker(planner, candidates) in candidates
    assert use_as_decision_maker(bandit, candidates) in candidates


def test_strategy_selector_picks_by_scored_complexity_and_risk():
    selector = StrategySelector(
        rules=[
            StrategyRule("single_agent", lambda ctx: ctx["complexity"] < 0.3),
            StrategyRule("debate_judge", lambda ctx: ctx["risk"] > 0.8),
        ],
        default="supervisor",
    )
    assert selector.select({"complexity": 0.1, "risk": 0.1}) == "single_agent"
    assert selector.select({"complexity": 0.9, "risk": 0.95}) == "debate_judge"
    assert selector.select({"complexity": 0.9, "risk": 0.1}) == "supervisor"
