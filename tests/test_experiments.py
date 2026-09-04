from agent_foundry.contracts import Identity
from agent_foundry.experiments import Experiment, ExperimentTracker


def test_assignment_is_deterministic_per_identity():
    experiment = Experiment(name="prompt_tone", variants={"formal": 0.5, "casual": 0.5})
    identity = Identity(id="user-a", tenant_id="acme")
    first = experiment.assign(identity)
    for _ in range(20):
        assert experiment.assign(identity) == first


def test_assignment_splits_across_many_identities():
    experiment = Experiment(name="prompt_tone", variants={"formal": 0.5, "casual": 0.5})
    identities = [Identity(id=f"user-{i}", tenant_id="acme") for i in range(200)]
    counts = {"formal": 0, "casual": 0}
    for identity in identities:
        counts[experiment.assign(identity)] += 1
    assert 60 < counts["formal"] < 140
    assert 60 < counts["casual"] < 140


def test_assignment_respects_unequal_weights():
    experiment = Experiment(name="prompt_tone", variants={"formal": 0.9, "casual": 0.1})
    identities = [Identity(id=f"user-{i}", tenant_id="acme") for i in range(300)]
    counts = {"formal": 0, "casual": 0}
    for identity in identities:
        counts[experiment.assign(identity)] += 1
    assert counts["formal"] > counts["casual"] * 3


def test_tracker_summary_differentiates_variants():
    tracker = ExperimentTracker()
    for v in (0.9, 0.85, 0.95):
        tracker.record("prompt_tone", "formal", v)
    for v in (0.5, 0.6, 0.4):
        tracker.record("prompt_tone", "casual", v)

    summary = tracker.summary("prompt_tone")
    assert summary["formal"]["count"] == 3
    assert abs(summary["formal"]["mean"] - 0.9) < 1e-9
    assert summary["formal"]["mean"] > summary["casual"]["mean"]


def test_tracker_summary_ignores_other_experiments():
    tracker = ExperimentTracker()
    tracker.record("prompt_tone", "formal", 0.9)
    tracker.record("routing_strategy", "supervisor", 0.7)
    assert set(tracker.summary("prompt_tone")) == {"formal"}
