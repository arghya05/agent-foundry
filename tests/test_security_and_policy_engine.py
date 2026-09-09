import json
import os
import shutil
import socket
import subprocess
import tempfile
import time

import pytest

from agent_foundry.contracts import Identity, ToolSpec
from agent_foundry.security import AuditLog, CredentialVault, EgressPolicy, EncryptedJSONLAuditLog, JSONLAuditLog, ToolManifestRegistry, manifest_hash


def test_tool_manifest_registry_detects_drift():
    spec = ToolSpec("lookup_order", "Look up an order", {}, lambda order_id: order_id)
    reg = ToolManifestRegistry()
    reg.pin(spec)
    assert reg.verify(spec)
    drifted = ToolSpec("lookup_order", "Look up an order — CHANGED", {}, lambda order_id: order_id)
    assert not reg.verify(drifted)


def test_manifest_hash_detects_a_body_change_with_an_unchanged_signature():
    """Regression: manifest_hash used to hash name:description:signature
    only — changing ONLY a function's body (same name/description/params)
    left the fingerprint unchanged, defeating the supply-chain check for
    exactly the case that matters (the implementation, not the interface)."""
    def refund_v1(order_id: str) -> str:
        return f"refunded {order_id}"

    def refund_v2(order_id: str) -> str:
        return f"refunded {order_id} TWICE"  # same signature, different body

    spec_v1 = ToolSpec("refund", "Refund an order", {"order_id": "string"}, refund_v1)
    spec_v2 = ToolSpec("refund", "Refund an order", {"order_id": "string"}, refund_v2)

    assert manifest_hash(spec_v1) != manifest_hash(spec_v2)


def test_manifest_hash_falls_back_to_signature_only_when_source_is_unavailable():
    """A dynamically built callable (no retrievable source) must not raise —
    manifest_hash falls back to name:description:signature, same as before
    this fix existed."""
    fn = eval("lambda order_id: order_id")  # noqa: S307 — test-only, source not retrievable from a string eval
    spec = ToolSpec("lookup_order", "Look up an order", {}, fn)
    assert manifest_hash(spec)  # doesn't raise


def test_policy_decision_point_denies_a_disallowed_host_even_though_the_guardrail_alone_allows_it():
    """The mandatory Policy Decision Point the review asked for: composes
    the deterministic action guardrail with an EgressPolicy host allowlist
    in ONE call, instead of a caller having to remember to check both
    separately. GuardrailEngine.check_action alone has no notion of hosts
    at all — it would allow this call; PolicyDecisionPoint.decide() must not."""
    from agent_foundry.contracts import AutonomyLevel, Policy
    from agent_foundry.guardrails import GuardrailEngine
    from agent_foundry.policy_engine import PolicyDecisionPoint

    identity = Identity(id="agent-1", tenant_id="acme")
    policy = Policy(allowed_tools=frozenset({"fetch_weather"}), autonomy=AutonomyLevel.L4_POLICY_BOUND)
    egress = EgressPolicy(allowed_hosts={"fetch_weather": frozenset({"api.weather.com"})})
    pdp = PolicyDecisionPoint(guardrails=GuardrailEngine(policy), egress=egress)

    allowed = pdp.decide("fetch_weather", {"host": "api.weather.com"}, identity=identity, policy=policy, host="api.weather.com")
    denied = pdp.decide("fetch_weather", {"host": "evil.example.com"}, identity=identity, policy=policy, host="evil.example.com")

    assert allowed.allowed
    assert not denied.allowed and "egress" in denied.reason


def test_policy_decision_point_composes_an_external_policy_engine():
    """Same composition, for the OPA/Cedar PolicyEngine slot instead of
    EgressPolicy — a PDP wired with a policy_engine that denies everything
    must deny even a tool_action the guardrail alone would allow."""
    from agent_foundry.contracts import AutonomyLevel, Policy
    from agent_foundry.guardrails import GuardrailEngine
    from agent_foundry.policy_engine import PolicyDecisionPoint

    class DenyAll:
        def allow(self, input):
            return False

    identity = Identity(id="agent-1", tenant_id="acme")
    policy = Policy(allowed_tools=frozenset({"lookup_order"}), autonomy=AutonomyLevel.L4_POLICY_BOUND)
    pdp = PolicyDecisionPoint(guardrails=GuardrailEngine(policy), policy_engine=DenyAll())

    result = pdp.decide("lookup_order", {"order_id": "A100"}, identity=identity, policy=policy)

    assert not result.allowed and "external policy engine" in result.reason


def test_egress_policy_allowlist():
    policy = EgressPolicy(allowed_hosts={"weather_tool": frozenset({"api.weather.com"})})
    assert policy.check("weather_tool", "api.weather.com")
    assert not policy.check("weather_tool", "evil.example.com")
    assert policy.check("unrestricted_tool", "anything.example.com")  # no entry = unrestricted


def test_audit_log_records_identity_and_action():
    log = AuditLog()
    log.record(identity=Identity(id="a", tenant_id="acme"), action="tool_call", tool="refund", ok=True)
    assert log.entries[0]["action"] == "tool_call" and log.entries[0]["tenant"] == "acme"


def test_jsonl_audit_log_writes_durable_entries():
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    try:
        log = JSONLAuditLog(path=path)
        log.record(identity=Identity(id="a", tenant_id="acme"), action="approval_decision", approved=True)
        line = json.loads(open(path).read().strip())
        assert line["action"] == "approval_decision" and line["approved"] is True
    finally:
        os.unlink(path)


def test_audit_log_tail_returns_most_recent_entries_newest_first():
    log = AuditLog()
    for i in range(5):
        log.record(identity=Identity(id="a", tenant_id="acme"), action=f"event_{i}")
    tail = log.tail(3)
    assert [e["action"] for e in tail] == ["event_4", "event_3", "event_2"]


def test_jsonl_audit_log_tail_reads_the_file_back_newest_first():
    """A dashboard/ops view drilling into recent activity for a production
    deployment (JSONLAuditLog is healthcare/backend's real default) needs
    the same read-back capability AuditLog.tail() gives tests — this is the
    durable version, reading its own file rather than an in-memory list."""
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    try:
        log = JSONLAuditLog(path=path)
        for i in range(5):
            log.record(identity=Identity(id="a", tenant_id="acme"), action=f"event_{i}")
        tail = log.tail(3)
        assert [e["action"] for e in tail] == ["event_4", "event_3", "event_2"]
    finally:
        os.unlink(path)


def test_jsonl_audit_log_tail_on_a_file_that_does_not_exist_yet_returns_empty():
    log = JSONLAuditLog(path="/tmp/agent_foundry_nonexistent_audit_log_test.jsonl")
    assert log.tail() == []


def test_encrypted_jsonl_audit_log_is_unreadable_on_disk_without_the_key():
    """The real point of EncryptedJSONLAuditLog: what's actually written to
    disk must not contain the plaintext — a draft_reply or tool args in a
    plain JSONLAuditLog line sit in cleartext for anyone with filesystem
    access; encrypted, the raw bytes on disk must show no trace of it."""
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    try:
        key = EncryptedJSONLAuditLog.generate_key()
        log = EncryptedJSONLAuditLog(path=path, key=key)
        log.record(identity=Identity(id="a", tenant_id="acme"), action="critique_review", draft_reply="a real patient-facing clinical draft")

        raw_bytes = open(path, "rb").read()
        assert b"a real patient-facing clinical draft" not in raw_bytes
        assert b"critique_review" not in raw_bytes
    finally:
        os.unlink(path)


def test_encrypted_jsonl_audit_log_round_trips_through_tail():
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    try:
        key = EncryptedJSONLAuditLog.generate_key()
        log = EncryptedJSONLAuditLog(path=path, key=key)
        for i in range(5):
            log.record(identity=Identity(id="a", tenant_id="acme"), action=f"event_{i}")
        tail = log.tail(3)
        assert [e["action"] for e in tail] == ["event_4", "event_3", "event_2"]
    finally:
        os.unlink(path)


def test_encrypted_jsonl_audit_log_wrong_key_cannot_decrypt():
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    try:
        log = EncryptedJSONLAuditLog(path=path, key=EncryptedJSONLAuditLog.generate_key())
        log.record(identity=Identity(id="a", tenant_id="acme"), action="tool_call")

        wrong_key_log = EncryptedJSONLAuditLog(path=path, key=EncryptedJSONLAuditLog.generate_key())
        assert wrong_key_log.tail() == []  # fails closed — skips undecryptable lines, doesn't crash or leak
    finally:
        os.unlink(path)


def test_encrypted_jsonl_audit_log_tail_on_a_file_that_does_not_exist_yet_returns_empty():
    log = EncryptedJSONLAuditLog(path="/tmp/agent_foundry_nonexistent_encrypted_audit_log_test.jsonl", key=EncryptedJSONLAuditLog.generate_key())
    assert log.tail() == []


def test_credential_vault_get_rotate_and_missing():
    os.environ["TEST_AGENT_FOUNDRY_SECRET"] = "from-env"
    vault = CredentialVault()
    assert vault.get("TEST_AGENT_FOUNDRY_SECRET") == "from-env"
    vault.rotate("TEST_AGENT_FOUNDRY_SECRET", "rotated")
    assert vault.get("TEST_AGENT_FOUNDRY_SECRET") == "rotated"
    assert vault.age_s("TEST_AGENT_FOUNDRY_SECRET") is not None
    with pytest.raises(KeyError):
        vault.get("NO_SUCH_SECRET_XYZ")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def opa_server():
    """Real local OPA server, skipped entirely if the `opa` binary isn't on
    PATH — this is deliberately a real integration test, not a mock, matching
    how this policy engine was originally verified in development."""
    opa_bin = shutil.which("opa")
    if opa_bin is None:
        pytest.skip("opa binary not on PATH")

    port = _free_port()
    fd, policy_path = tempfile.mkstemp(suffix=".rego")
    os.write(fd, b"""package agent_foundry

default allow = false

allow if {
	input.tool in input.allowed_tools
	input.cost_so_far < input.max_cost
}
""")
    os.close(fd)
    proc = subprocess.Popen(
        [opa_bin, "run", "--server", f"--addr=localhost:{port}", policy_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            try:
                import urllib.request
                urllib.request.urlopen(f"http://localhost:{port}/health", timeout=1)
                break
            except Exception:
                time.sleep(0.1)
        else:
            pytest.fail("OPA server did not become healthy in time")
        yield f"http://localhost:{port}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        os.unlink(policy_path)


def test_opa_policy_engine_real_server_allows_and_denies(opa_server):
    from agent_foundry.policy_engine import OPAPolicyEngine

    engine = OPAPolicyEngine(base_url=opa_server)
    assert engine.allow({"tool": "lookup_order", "allowed_tools": ["lookup_order"], "cost_so_far": 0.01, "max_cost": 1.0})
    assert not engine.allow({"tool": "delete_db", "allowed_tools": ["lookup_order"], "cost_so_far": 0.01, "max_cost": 1.0})
    assert not engine.allow({"tool": "lookup_order", "allowed_tools": ["lookup_order"], "cost_so_far": 2.0, "max_cost": 1.0})


@pytest.fixture(scope="module")
def vault_server():
    """Real local Vault dev server, skipped entirely if the `vault` binary
    isn't on PATH."""
    vault_bin = shutil.which("vault") or "/opt/homebrew/opt/vault/bin/vault"
    if not (shutil.which("vault") or os.path.exists(vault_bin)):
        pytest.skip("vault binary not found")

    port = _free_port()
    proc = subprocess.Popen(
        [vault_bin, "server", "-dev", f"-dev-root-token-id=test-root-token", f"-dev-listen-address=127.0.0.1:{port}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            try:
                import urllib.request
                urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/sys/health", timeout=1)
                break
            except Exception:
                time.sleep(0.1)
        else:
            pytest.fail("Vault dev server did not become healthy in time")
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_vault_credential_provider_real_server_round_trip(vault_server):
    from agent_foundry.security import VaultCredentialProvider

    provider = VaultCredentialProvider(base_url=vault_server, token="test-root-token")
    provider.rotate("anthropic_api_key", "sk-ant-test-value")
    assert provider.get("anthropic_api_key") == "sk-ant-test-value"
