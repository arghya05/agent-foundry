"""Event Bus — pub/sub for async, cross-agent or cross-service events: the
"Event Driven" orchestration pattern, and the "Event Bus/Stream" communication
chip. EventBus is a Protocol; InMemoryEventBus is the zero-dependency reference
implementation — real and fully tested. KafkaEventBus matches kafka-python's
actual producer/consumer API (verified against the installed client library,
config keys and method signatures confirmed) but there's no Kafka broker in this
environment to round-trip against — unlike MCP and OPA elsewhere in this package,
which were verified against real live processes, this one needs your own broker
before you trust it in production. Requires `pip install kafka-python`.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


class EventBus(Protocol):
    def publish(self, topic: str, event: dict[str, Any]) -> None: ...
    def subscribe(self, topic: str, handler: Callable[[dict[str, Any]], None]) -> None: ...


@dataclass
class InMemoryEventBus:
    """Synchronous, in-process pub/sub: every subscriber on a topic is called
    immediately, in publish() order, on the publishing thread."""

    _subscribers: dict[str, list[Callable[[dict[str, Any]], None]]] = field(default_factory=dict)
    _log: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def publish(self, topic: str, event: dict[str, Any]) -> None:
        self._log.append((topic, event))
        for handler in self._subscribers.get(topic, []):
            handler(event)

    def subscribe(self, topic: str, handler: Callable[[dict[str, Any]], None]) -> None:
        self._subscribers.setdefault(topic, []).append(handler)

    def history(self, topic: str | None = None) -> list[tuple[str, dict[str, Any]]]:
        return [e for e in self._log if topic is None or e[0] == topic]


class KafkaEventBus:
    def __init__(self, bootstrap_servers: str) -> None:
        from kafka import KafkaProducer

        self._bootstrap_servers = bootstrap_servers
        self._producer = KafkaProducer(
            bootstrap_servers=bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode(),
        )

    def publish(self, topic: str, event: dict[str, Any]) -> None:
        self._producer.send(topic, value=event)
        self._producer.flush()

    def subscribe(self, topic: str, handler: Callable[[dict[str, Any]], None]) -> None:
        """Runs a consumer loop on a background thread, calling handler per message."""
        from kafka import KafkaConsumer

        def run() -> None:
            consumer = KafkaConsumer(
                topic,
                bootstrap_servers=self._bootstrap_servers,
                value_deserializer=lambda v: json.loads(v.decode()),
            )
            for message in consumer:
                handler(message.value)

        threading.Thread(target=run, daemon=True).start()


def wire_event_driven(*, graph: Any, bus: EventBus, topic: str, thread_id_fn: Callable[[dict[str, Any]], str] | None = None) -> None:
    """The Event Driven pattern: subscribes any compiled graph (any build_*_graph
    output — event-driven isn't a topology of its own, it's a trigger mechanism
    orthogonal to which topology handles the event) to a bus topic, so it runs
    automatically whenever an event is published instead of being called directly.
    The event dict becomes the triggered turn's user message; thread_id_fn derives
    a thread id from the event (default: one shared thread per topic)."""
    thread_id_fn = thread_id_fn or (lambda event: f"event-{topic}")

    def on_event(event: dict[str, Any]) -> None:
        thread_id = thread_id_fn(event)
        graph.invoke(
            {"messages": [{"role": "user", "content": json.dumps(event)}], "thread_id": thread_id},
            {"configurable": {"thread_id": thread_id}},
        )

    bus.subscribe(topic, on_event)
