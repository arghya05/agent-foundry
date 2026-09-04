"""Feature Flags — decoupled on/off (and percentage-rollout) switches for agent
behavior: swap a prompt version, gate a new tool, enable a new topology, all
without a code deploy. FeatureFlagProvider is a Protocol so this integrates with
LaunchDarkly/Unleash/a config service the same way VectorStore integrates with
Pinecone/Chroma — implement is_enabled(), nothing else in the framework changes.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Protocol

from .contracts import Identity


class FeatureFlagProvider(Protocol):
    def is_enabled(self, flag: str, *, identity: Identity | None = None, default: bool = False) -> bool: ...


@dataclass
class StaticFeatureFlagProvider:
    """Zero-dependency reference implementation. A flag is either a plain bool
    (on/off for everyone) or an int 0-100 (a rollout percentage) — bucketed by a
    stable hash of (flag, identity), so the same identity always lands on the
    same side of the rollout instead of flapping between calls."""

    flags: dict[str, bool | int] = field(default_factory=dict)

    def is_enabled(self, flag: str, *, identity: Identity | None = None, default: bool = False) -> bool:
        if flag not in self.flags:
            return default
        value = self.flags[flag]
        if isinstance(value, bool):
            return value
        bucket_key = f"{flag}:{identity.id if identity else 'anonymous'}"
        bucket = int(hashlib.sha256(bucket_key.encode()).hexdigest(), 16) % 100
        return bucket < value

    def set(self, flag: str, value: bool | int) -> None:
        self.flags[flag] = value
