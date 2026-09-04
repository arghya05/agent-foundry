# OWASP Top 10 for LLM Applications — Cross-Check

*What in Agent Foundry mitigates each risk, and what's honestly still a gap.*

This maps the OWASP LLM Top 10 (2025) to concrete, already-tested code in
`agent_foundry/` — not aspirational coverage. Where nothing in the framework
addresses a risk, that's stated plainly rather than stretched to fit.

| # | Risk | Mitigated by | Honest gap |
|---|---|---|---|
| LLM01 | Prompt Injection | `guardrails.py`: `GuardrailEngine.check_input` (regex markers) and `LLMGuardrails.check_input` (LLM-judgment — verified catching a paraphrased injection the regex missed) | Neither is a trained classifier; a sufficiently novel phrasing can still slip past both. Pair with a real moderation API for high-stakes input if regex/LLM-judgment isn't enough |
| LLM02 | Sensitive Information Disclosure | `guardrails.py`: `redact()` and `check_output` strip email/SSN/card patterns before output leaves the agent; `security.py`'s `VaultCredentialProvider` keeps secrets out of prompts/logs entirely | Pattern-based PII detection has the same recall limits as LLM01 — no dedicated PII-detection model included |
| LLM03 | Supply Chain | `security.py`'s `ToolManifestRegistry` detects drift in a tool's signature/schema after registration (catches a tool silently changing behavior) | No SBOM generation or automated dependency-vulnerability scanning is built in — that's a CI-pipeline concern (e.g. `pip-audit`), not something `agent_foundry` itself does at runtime |
| LLM04 | Data and Model Poisoning | `context.py`: RAG passages carry `{"source", ...}` metadata so provenance is at least visible to a reviewer | No automated validation that ingested documents are trustworthy before they enter `ChromaVectorStore` — that check is the integrating team's responsibility at ingestion time |
| LLM05 | Improper Output Handling | `guardrails.py check_output`; `kpi.schema_valid_kpi` (real JSON Schema validation before a structured output is trusted downstream); `sandbox.py` (any code the model asks to run executes in a restricted namespace with a timeout, never directly) | `sandbox.py`'s own docstring is explicit: process-level isolation only, not a container/VM boundary — don't point it at genuinely hostile input without a stronger sandbox in front of it |
| LLM06 | Excessive Agency | `contracts.AutonomyLevel` (L0-L5, gates whether the agent can act at all); `Policy.allowed_tools` (RBAC); `Policy.requires_approval` + LangGraph `interrupt()` (synchronous HITL); `runtime.RunBudget`/`LatencyBudget` (hard ceilings); `escalation.py` (hand off instead of denying outright) | None — this is the risk the framework is most deliberately built around |
| LLM07 | System Prompt Leakage | `guardrails.py`'s injection markers include "reveal your system prompt"; `LLMGuardrails.check_input` catches paraphrased attempts | No dedicated *output-side* check that the system prompt's actual text didn't leak into a response — `check_output` looks for PII patterns, not for prompt-content echoing. A team with a sensitive system prompt should add a custom KPI or output check for this specifically |
| LLM08 | Vector and Embedding Weaknesses | `context.py`'s `ChromaVectorStore` is verified thread-isolated (metadata-filtered per thread, tested that one thread's upserts don't leak into another's retrieval) | No embedding-poisoning detection (a malicious document crafted to rank highly for unrelated queries) — that's an open research problem, not something a reference implementation should claim to solve |
| LLM09 | Misinformation | `kpi.py`: `db_match_kpi` (deterministic fact-check against a real database — verified catching a fabricated claim), `reference_check_kpi` (grounding against cited documents), `completeness_kpi`, `llm_judge_kpi` | KPIs catch misinformation *at eval time*, after generation — there's no generation-time constrained-decoding guarantee against fabrication |
| LLM10 | Unbounded Consumption | `runtime.py`: `RunBudget` (cost ceiling, fails closed), `LatencyBudget` (cumulative wall-clock ceiling), `RateLimiter` (burst control), `CircuitBreaker` (stops calling a failing dependency); `Policy.max_steps_per_thread` | None at the framework level — a misconfigured `max_cost_usd_per_thread` (set too high) is still an operator error the framework can't prevent, only bound |

**Bottom line:** 7 of 10 risks have direct, tested mitigations already in the
codebase; the other 3 (LLM03 supply chain, LLM04 data poisoning, LLM08 embedding
weaknesses) have partial coverage with an explicit, named gap — deliberately not
papered over with a heuristic that would only create false confidence.
