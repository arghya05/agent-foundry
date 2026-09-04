from agent_foundry.contracts import GuardrailResult, Identity
from agent_foundry.escalation import QueueEscalator


def test_escalate_creates_a_pending_ticket():
    escalator = QueueEscalator()
    identity = Identity(id="agent-1", tenant_id="acme")
    ticket = escalator.escalate(identity=identity, reason="ambiguous refund amount", context={"order_id": "A100"})
    assert ticket.identity_id == "agent-1"
    assert ticket.resolved is False
    assert escalator.pending() == [ticket]


def test_resolve_removes_ticket_from_pending():
    escalator = QueueEscalator()
    identity = Identity(id="agent-1", tenant_id="acme")
    ticket = escalator.escalate(identity=identity, reason="needs manager sign-off")
    escalator.resolve(ticket.id, "approved by manager")
    assert escalator.pending() == []
    assert ticket.resolved is True
    assert ticket.resolution == "approved by manager"


def test_multiple_tickets_track_independently():
    escalator = QueueEscalator()
    identity = Identity(id="agent-1", tenant_id="acme")
    t1 = escalator.escalate(identity=identity, reason="case 1")
    t2 = escalator.escalate(identity=identity, reason="case 2")
    escalator.resolve(t1.id, "handled")
    pending = escalator.pending()
    assert len(pending) == 1 and pending[0].id == t2.id


def test_guardrail_result_escalate_flag_defaults_false():
    assert GuardrailResult(allowed=False, reason="cost ceiling").escalate is False


def test_a_custom_check_can_flag_escalate_instead_of_flat_deny():
    def check_ambiguous_refund(amount_usd: float) -> GuardrailResult:
        if amount_usd > 500:
            # too large to auto-deny outright; a human should decide, not the agent
            return GuardrailResult(False, "refund exceeds auto-approval ceiling", "action", escalate=True)
        return GuardrailResult(True, stage="action")

    result = check_ambiguous_refund(750.0)
    assert result.allowed is False and result.escalate is True

    escalator = QueueEscalator()
    identity = Identity(id="agent-1", tenant_id="acme")
    if result.escalate:
        ticket = escalator.escalate(identity=identity, reason=result.reason, context={"amount_usd": 750.0})
        assert ticket in escalator.pending()
