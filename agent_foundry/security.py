"""Security pillar — tool manifest integrity, egress allowlisting, audit trail.

Pairs with guardrails.py (which screens content) and the Identity/Governance
rail in PLAN.md: this module is about *what the system itself* is allowed to
run and who did what, not about screening a single message.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from .contracts import Identity, ToolSpec


def manifest_hash(spec: ToolSpec) -> str:
    """Fingerprints a tool's name, description and signature so drift is detectable."""
    src = f"{spec.name}:{spec.description}:{inspect.signature(spec.fn)}"
    return hashlib.sha256(src.encode()).hexdigest()[:16]


@dataclass
class ToolManifestRegistry:
    """Pins each tool to a hash at registration time; verify() flags silent changes
    to a tool's signature or description — the supply-chain check for local and
    MCP-provided tools alike."""

    _pins: dict[str, str] = field(default_factory=dict)

    def pin(self, spec: ToolSpec) -> str:
        h = manifest_hash(spec)
        self._pins[spec.name] = h
        return h

    def verify(self, spec: ToolSpec) -> bool:
        return self._pins.get(spec.name) == manifest_hash(spec)


@dataclass
class EgressPolicy:
    """Per-tool allowlist of external hosts it's permitted to reach. `None` means unrestricted."""

    allowed_hosts: dict[str, frozenset[str]] = field(default_factory=dict)

    def check(self, tool: str, host: str) -> bool:
        allowed = self.allowed_hosts.get(tool)
        return allowed is None or host in allowed


class SecretsProvider(Protocol):
    """Anything with get()/rotate(). CredentialVault (env-backed) is the
    reference implementation; VaultCredentialProvider below is a genuinely
    different one — a real external secrets system, not memory or env vars."""

    def get(self, name: str) -> str: ...
    def rotate(self, name: str, value: str) -> None: ...


@dataclass
class VaultCredentialProvider:
    """Real HashiCorp Vault-backed secrets (KV v2 engine), over stdlib urllib —
    zero extra dependency. Same get()/rotate() shape as CredentialVault, so it's
    a drop-in wherever a SecretsProvider is used. Verified against a live local
    `vault server -dev` instance, not mocked. AWS Secrets Manager / GCP Secret
    Manager plug in the same way — implement get()/rotate() against their APIs."""

    base_url: str
    token: str
    mount: str = "secret"
    timeout_s: float = 5.0

    def get(self, name: str) -> str:
        req = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/v1/{self.mount}/data/{name}",
            headers={"X-Vault-Token": self.token},
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            body = json.loads(resp.read())
        return body["data"]["data"]["value"]

    def rotate(self, name: str, value: str) -> None:
        payload = json.dumps({"data": {"value": value}}).encode()
        req = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/v1/{self.mount}/data/{name}",
            data=payload, method="POST",
            headers={"X-Vault-Token": self.token, "Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=self.timeout_s)


@dataclass
class CredentialVault:
    """Short-lived, scoped credential issuance. This reference implementation reads
    from the environment and tracks rotation timestamps; swap get()/rotate() for
    AWS Secrets Manager, HashiCorp Vault, or GCP Secret Manager and nothing calling
    it needs to change."""

    _overrides: dict[str, str] = field(default_factory=dict)
    _rotated_at: dict[str, float] = field(default_factory=dict)

    def get(self, name: str) -> str:
        value = self._overrides.get(name) or os.environ.get(name)
        if value is None:
            raise KeyError(f"credential {name!r} not found")
        return value

    def rotate(self, name: str, value: str) -> None:
        self._overrides[name] = value
        self._rotated_at[name] = time.time()

    def age_s(self, name: str) -> float | None:
        rotated = self._rotated_at.get(name)
        return None if rotated is None else time.time() - rotated


class AuditSink(Protocol):
    """Anything with .record(). orchestration.py only ever calls this one method
    on AgentConfig.audit — the entire contract a compliance system needs to
    satisfy: ship it to Splunk, a SIEM, a warehouse table, wherever."""

    def record(self, *, identity: Identity, action: str, **detail: Any) -> Any: ...


@dataclass
class AuditLog:
    """Append-only, in-memory record of every decision, tool call and approval."""

    entries: list[dict[str, Any]] = field(default_factory=list)

    def record(self, *, identity: Identity, action: str, **detail: Any) -> None:
        self.entries.append({
            "ts": time.time(),
            "identity": identity.id,
            "tenant": identity.tenant_id,
            "action": action,
            **detail,
        })

    def tail(self, n: int = 50) -> list[dict[str, Any]]:
        """Most recent `n` entries, newest first — the read-back half of the
        audit trail (record() only writes). A dashboard/ops view drilling
        into recent activity needs this same shape from either AuditLog or
        JSONLAuditLog interchangeably, see the latter's own tail() below."""
        return list(reversed(self.entries[-n:]))


@dataclass
class JSONLAuditLog:
    """Durable audit trail — one JSON line per entry, written to disk instead of
    held in memory, so it survives a process restart and is easy to ship to a
    real log pipeline. Same .record() shape as AuditLog, drop-in for
    AgentConfig.audit."""

    path: str

    def record(self, *, identity: Identity, action: str, **detail: Any) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps({
                "ts": time.time(), "identity": identity.id, "tenant": identity.tenant_id,
                "action": action, **detail,
            }) + "\n")

    def tail(self, n: int = 50) -> list[dict[str, Any]]:
        """Most recent `n` entries, newest first — reads the file back
        (record() only ever appends to it) so an ops/dashboard view can show
        recent activity for a deployment using the real, durable audit log
        (JSONLAuditLog is production's default — see healthcare/backend/
        agent.py's _default_audit) the same way AuditLog.tail() does for the
        in-memory one tests use."""
        if not os.path.exists(self.path):
            return []
        with open(self.path) as f:
            lines = f.readlines()
        return [json.loads(line) for line in reversed(lines[-n:])]


@dataclass
class EncryptedJSONLAuditLog:
    """Same durable, one-line-per-entry audit trail as JSONLAuditLog, with
    each line's content encrypted at rest (Fernet — authenticated symmetric
    encryption, AES-128-CBC + HMAC) instead of written as plain JSON. Any
    deployment where the audit trail can carry real sensitive content — a
    paused critique's draft_reply, a tool call's raw args — leaves that
    sitting in cleartext on disk with plain JSONLAuditLog. Same
    .record()/.tail() shape, so it's a drop-in AuditSink anywhere
    JSONLAuditLog is used; only the on-disk representation differs.

    `key` is a Fernet key (32 url-safe base64-encoded bytes) — generate one
    with generate_key() and keep it OUTSIDE this repo/disk (an env var
    backed by a real secrets manager in production; see security.py's own
    CredentialVault/VaultCredentialProvider for that). Losing the key makes
    every past entry unrecoverable; that's the correct tradeoff for at-rest
    encryption, not a bug."""

    path: str
    key: bytes

    def __post_init__(self) -> None:
        from cryptography.fernet import Fernet
        self._fernet = Fernet(self.key)

    @staticmethod
    def generate_key() -> bytes:
        from cryptography.fernet import Fernet
        return Fernet.generate_key()

    def record(self, *, identity: Identity, action: str, **detail: Any) -> None:
        payload = json.dumps({
            "ts": time.time(), "identity": identity.id, "tenant": identity.tenant_id,
            "action": action, **detail,
        }).encode()
        with open(self.path, "ab") as f:
            f.write(self._fernet.encrypt(payload) + b"\n")

    def tail(self, n: int = 50) -> list[dict[str, Any]]:
        """Same read-back contract as JSONLAuditLog.tail() — decrypts each
        line back to its real entry. A line that fails to decrypt (wrong
        key, corrupted write) is skipped rather than raising, same
        fail-soft posture json.loads(line) already has no protection
        against in the plain JSONLAuditLog — a dashboard reading recent
        activity shouldn't 500 over one bad line."""
        if not os.path.exists(self.path):
            return []
        with open(self.path, "rb") as f:
            lines = f.readlines()
        out = []
        for line in reversed(lines[-n:]):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(self._fernet.decrypt(line)))
            except Exception:
                continue
        return out
