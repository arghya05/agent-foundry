"""Escalation — the third outcome besides "auto-approve" and "deny". Distinct
from Policy.requires_approval, which pauses the *same* thread for a synchronous
approve/deny via LangGraph's interrupt(): an escalation hands the case off
entirely — to a human working an async queue, or to a different, more-trusted
agent — instead of blocking the calling thread on an immediate decision.

Escalator is a Protocol; QueueEscalator is the zero-dependency reference
implementation. contracts.GuardrailResult carries an `escalate` flag (default
False, so every existing check that never escalates is unaffected) — a check
that decides a case needs human judgment rather than a flat denial sets
escalate=True instead of just allowed=False, and the caller routes that ticket
to an Escalator instead of stopping the agent outright.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from .contracts import Identity


@dataclass
class EscalationTicket:
    id: str
    identity_id: str
    reason: str
    context: dict[str, Any]
    created_at: float = field(default_factory=time.time)
    resolved: bool = False
    resolution: str | None = None


class Escalator(Protocol):
    def escalate(self, *, identity: Identity, reason: str, context: dict[str, Any] | None = None) -> EscalationTicket: ...


@dataclass
class QueueEscalator:
    """In-memory ticket queue: escalate() enqueues, pending() is what an
    operator (or another process) works from, resolve() closes a ticket. Swap
    for a real ticketing system (Zendesk, Jira Service Desk, a Slack channel
    post) by implementing the same escalate() shape — the tickets it returns
    look the same either way."""

    _tickets: dict[str, EscalationTicket] = field(default_factory=dict)

    def escalate(self, *, identity: Identity, reason: str, context: dict[str, Any] | None = None) -> EscalationTicket:
        ticket = EscalationTicket(id=uuid.uuid4().hex, identity_id=identity.id, reason=reason, context=context or {})
        self._tickets[ticket.id] = ticket
        return ticket

    def pending(self) -> list[EscalationTicket]:
        return [t for t in self._tickets.values() if not t.resolved]

    def resolve(self, ticket_id: str, resolution: str) -> None:
        self._tickets[ticket_id].resolved = True
        self._tickets[ticket_id].resolution = resolution
