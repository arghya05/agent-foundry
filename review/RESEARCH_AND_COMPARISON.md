# Research and comparable projects

**Follow-up status (2026-09-28):** consult [CURRENT_STATUS.md](CURRENT_STATUS.md), [live results](LIVE_BENCHMARK_RESULTS.md), and [the updated novelty gate](RESEARCH_GATE_20260928.md). Proposed requirements below are not all implemented; original measurements remain dated evidence.

Research cutoff: **2026-09-27**. Sources are primary papers, official documentation, and project repositories. This is a scoped architecture review, not an exhaustive systematic literature review. Unless explicitly stated, performance reported by a paper/project was not independently reproduced here.

Reading-depth labels: **A** = abstract/metadata inspected; **S** = selected full-text sections also inspected; **D** = official documentation/README inspected. Abstract-level entries support directions to investigate, not a complete assessment of assumptions or empirical validity. Recent preprints are not automatically established best practice.

## What the papers imply for this repository

| Work and primary source | Depth | Useful idea | Repository implication and limit |
| --- | --- | --- | --- |
| [ReAct](https://arxiv.org/abs/2210.03629), 2022 | A | Interleave reasoning, actions, and observations | Existing agent loops already follow a familiar paradigm; this alone is not 2026 novelty |
| [CoALA](https://arxiv.org/abs/2309.02427), 2023 | A | Organize agents through memory, action space, and decision processes | Good conceptual vocabulary for the layers; a taxonomy does not establish runtime safety |
| [Reflexion](https://arxiv.org/abs/2303.11366), 2023 | A | Use task feedback and verbal reflection across attempts | Evaluate critique/reflection against an equal-budget baseline; reflection quality depends on feedback |
| [CodeAct](https://arxiv.org/abs/2402.01030), 2024 | A | Executable code as an expressive agent action representation | Valuable for computational tasks only with real isolation; `exec` restrictions are insufficient |
| [MemGPT](https://arxiv.org/abs/2310.08560), 2023 | A | Manage limited context using a memory hierarchy | Add explicit working-context management and provenance; more stored text is not automatically useful memory |
| [A-MEM](https://arxiv.org/abs/2502.12110), 2025 | A | Link and organize agent memories dynamically | Candidate experimental memory backend; compare retrieval and contamination behavior before adoption |
| [Zep](https://arxiv.org/abs/2501.13956), 2025 | A | Temporal knowledge representations for agent memory | Current in-memory triples do not establish temporal reasoning; version facts and test corrections |
| [Mem0](https://arxiv.org/abs/2504.19413), 2025 | A | Extract and manage persistent conversational memories | Measure accuracy, latency, privacy, and deletion; do not transfer paper gains to this implementation |
| [LongMemEval](https://arxiv.org/abs/2410.10813), 2024 | A | Evaluate capabilities required for long-term conversational memory | Use temporal, update, and abstention tests rather than only vector similarity |
| [Agentic Context Engineering (ACE)](https://arxiv.org/abs/2510.04618), 2025 | S | Generate, reflect on, and curate evolving contextual knowledge | Prototype versioned context updates with quality feedback and held-out tests; avoid uncontrolled prompt growth |
| [MAST: Why Do Multi-Agent LLM Systems Fail?](https://arxiv.org/abs/2503.13657), 2025 | S, v2 | Analyze failures of specification, coordination, and verification | Add failure labels for role drift, missing stop conditions, history loss, and bad verification; topology count is not quality |
| [CaMeL](https://arxiv.org/abs/2503.18813), 2025 | S, v2 | Separate trusted control and untrusted data; enforce capabilities/information flow | Strong design inspiration for tool/context boundaries; its threat model and limitations do not support universal injection immunity |
| [AgentDojo](https://arxiv.org/abs/2406.13352), 2024 | A + D | Evaluate attacks and defenses in tool-using environments | Measure attack outcomes and clean utility through the real action boundary |
| [τ-bench](https://arxiv.org/abs/2406.12045), 2024 | A | Tool-agent-user tasks with policy and end-state evaluation | Strong match for support agents; repeated-run reliability matters more than a polished single demo |
| [τ²-bench](https://arxiv.org/abs/2506.07982), 2025 | A | Agent/user coordination in a dual-control environment | Test interaction and clarification as well as tool selection; pin user-simulator configuration |
| [Towards a Science of Scaling Agent Systems](https://arxiv.org/abs/2512.08296), 2025–2026 | S, v3 | Study when coordination helps and when its cost harms performance | Evaluate single and multi-agent methods under matched budgets; findings from studied tasks/agent counts are not universal laws |
| [Scaling LLM-Driven Multi-Agent Systems: Design Principles and Architectural Scalability Analysis](https://arxiv.org/abs/2607.27942), 2026 | A | Study architectural complexity and communication choices | Treat as recent evidence motivating optional coordination and concise communication, not proof of a universal best topology |
| [Beyond Similarity: Trustworthy Memory Search for Personal AI Agents](https://arxiv.org/abs/2606.06054), 2026 | A | Treat memory retrieval as a trust-sensitive operation | Add memory authorization and contamination tests; semantic similarity alone is not a trust decision |
| [AgentSpec: Customizable Runtime Enforcement](https://arxiv.org/abs/2503.18666), 2025 | A | Structured runtime constraints and enforcement | Direct prior art: “we added runtime policies” is not a novel contribution; unrelated to this repo's same-named `AgentSpec` class |
| [Pro2Guard](https://arxiv.org/abs/2508.00500), 2025 | A | Predict future unsafe states using a learned probabilistic abstraction | Relevant baseline for proactive safety; assumptions and distribution shift require full-text investigation |
| [SABER](https://arxiv.org/abs/2512.07850), 2025–2026 | S, v1 §§4.1–4.2 | Target verification/reflection at environment-changing steps and clean context | Directly relevant to action guards and adaptive context; mutating-step checks are not by themselves new |
| [WorkBench](https://arxiv.org/abs/2405.00823), 2024; [2026 revisit](https://arxiv.org/abs/2606.13715v2) | A + D | Evaluate workplace actions using resulting environment state | Strong product benchmark; use versioned ground truth/scorer, and track unwanted changes alongside success |
| [TheAgentCompany](https://arxiv.org/abs/2412.14161), 2024 | A + D | Evaluate work across a simulated company's applications | Tests a broader enterprise agent application; it requires environments and tools beyond the current small demos |

The [CaMeL reference repository](https://github.com/google-research/camel-prompt-injection) describes itself as a research prototype and is not a maintained production security subsystem. Adopt validated ideas, not a security promise inferred from a paper title.

### Additional 2026 novelty check

The follow-up search for the user's publication requirement found several closer current works. [AgenTRIM](https://arxiv.org/abs/2601.12449v2) and [ActGov](https://arxiv.org/abs/2609.24446v2) directly overlap adaptive tool controls and action authorization. [The Verifier Tax](https://arxiv.org/abs/2603.19328) studies the difference between blocking unsafe steps and achieving safe completion. [Strategic Verification](https://www.preprints.org/manuscript/202608.2057) proposes budgeted verification, and [Safety Testing LLM Agents at Scale](https://arxiv.org/abs/2607.01793) uses environment evidence for testing. These were checked at abstract/overview depth; their empirical claims were not reproduced. The proposed frontier direction and its unresolved novelty gate are detailed in [RESEARCH_DESIGN_2026.md](RESEARCH_DESIGN_2026.md).

The engineering advice to start with simpler workflows and add autonomy when it produces measurable benefit is consistent with [Anthropic's agent design guidance](https://www.anthropic.com/engineering/building-effective-agents). Its [context engineering discussion](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) is also relevant to selecting, compacting, and separating context. These are engineering sources, not controlled evidence that one implementation beats another.

**Follow-up reading:** ActGov v2 and AgenTRIM v2 now have selected full-method/limitation inspection; SABER v1 has selected implementation-section inspection. See [CLOSEST_REPOSITORIES.md](CLOSEST_REPOSITORIES.md) and its manifest for exact scope. This supersedes the abstract-only depth for those three entries, without implying a full-paper reproduction.

## Similar repositories and what to learn

The table below is the original **documentation-based comparison of product scope and design**, not a security audit or performance ranking. The [pinned follow-up](CLOSEST_REPOSITORIES.md) adds selected source inspection for seven entries and a free upstream benchmark baseline reproduction. “Present in another project” in this table means documented by that project; configuration and version matter. Hosted/commercial services are not assumed to be part of the open-source package.

| Project | Main comparison axis | Lesson for Agent Foundry | Fair comparison boundary |
| --- | --- | --- | --- |
| [LangGraph](https://github.com/langchain-ai/langgraph) | Stateful graph orchestration, persistence, interrupts | Build on its lifecycle mechanisms where selected; maintain a clear native alternative | Persistent deployment requires configured storage; hosted platform features are separate |
| [Deep Agents](https://github.com/langchain-ai/deepagents) | Opinionated harness with subagents, files, context management, and memory | A useful default harness can reduce developer assembly work; it also competes directly on ease of starting | It builds on LangChain/LangGraph; compare harness behavior as well as runtime overhead |
| [Pydantic AI](https://github.com/pydantic/pydantic-ai) | Typed inputs/outputs, dependency integration, tool validation | Improve typed boundaries and clear error behavior | Type safety does not alone establish durable execution or multitenant security |
| [Google ADK](https://github.com/google/adk-python) | Agent composition, evaluation, development/deployment tooling | An integrated development-to-evaluation path matters | Compare the open toolkit and the chosen deployment separately |
| [CrewAI](https://github.com/crewAIInc/crewAI) | Role/task abstractions and flow orchestration | Support familiar authoring without dropping tool governance | Crews/Flows and commercial platform capabilities are distinct; use current adapter contracts |
| [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) | Agent/workflow tooling in Python and .NET | Compare current workflow abstractions and enterprise integration | Do not benchmark only older AutoGen patterns and call that a current ecosystem comparison |
| [OpenAI Agents SDK documentation](https://developers.openai.com/api/docs/guides/agents/sdk) | Agent loop, tool/handoff integration, tracing | Keep simple authoring and explicit lifecycle hooks | SDK facilities and managed platform services are separate; compare equivalent configurations |
| [Agno](https://github.com/agno-agi/agno) | Agent/team SDK and operational runtime/platform surface | Operator experience is part of the product, not only Python classes | Published performance claims need reproduction under matching workloads |
| [Dify](https://github.com/langgenius/dify) | Application platform, visual workflows, knowledge integration | Startups also need configuration, run inspection, and delivery | Different product layer and licensing; review terms before code reuse |
| [Langflow](https://github.com/langflow-ai/langflow) | Visual composition and Python-oriented integrations | Visual debugging/composition may help later onboarding | A canvas is not proof of security, correctness, or research novelty |
| [Letta](https://github.com/letta-ai/letta) | Memory-oriented agent lineage and current coding-agent direction | Persistent memory has a substantial ecosystem already | Current repository points to Letta Code; pin the intended codebase/version instead of relying on old server descriptions |
| [OpenHands](https://github.com/OpenHands/OpenHands) | Software-agent execution and tooling | Learn task environments, artifacts, and isolation patterns | Domain-specific coding results do not establish general enterprise performance |
| [Microsoft Agent Governance Toolkit](https://github.com/microsoft/agent-governance-toolkit) | Policy, identity, execution controls, governance integrations | Strong overlapping engineering prior art; consider integration and direct comparison | README coverage claims are not independent security certification; no local validation here |
| [AgentGovBench](https://github.com/agentic-control-plane/agentgovbench) | Deterministic governance scenarios across framework integrations | Reuse/compare existing identity, delegation, policy, audit, and tenant tests | Vendor-maintained benchmark; independently inspect runner/scorer and add held-out cases |

For LangGraph deployment, see [durable execution documentation](https://docs.langchain.com/oss/python/langgraph/durable-execution). For adapter upkeep, check [LangChain v1 migration](https://docs.langchain.com/oss/python/migrate/langchain-v1) and [CrewAI tool contracts](https://docs.crewai.com/en/concepts/tools). A broad minimum dependency declaration does not demonstrate compatibility with every intervening release.

## SOTA and differentiation judgment

The current repository combines many recognized patterns. That makes it potentially useful, but does not establish an architectural frontier. Memory hierarchies, ReAct loops, reflection, tool gates, framework adapters, and multi-agent topologies already have substantial prior art.

A credible differentiation hypothesis is **consistent operational guarantees across supported runtimes, packaged so a startup can adopt them with little glue code**. That hypothesis must be tested against existing governance toolkits and AgentGovBench; it is not an established unoccupied category. A narrower research contribution may involve how authority and side-effect safety survive crash/replay and policy changes across adapters. Novelty remains unproven until a deeper comparison shows a genuine gap.

Benchmark leadership can be pursued for specified tasks and budgets. It should not be conflated with universal enterprise readiness. Prefer a published Pareto comparison—verified task success, unwanted actions, latency, and total cost—to a blanket “beats every benchmark” claim.

## Research follow-ups before a paper

1. Read full methods, assumptions, artifact code, and limitations for AgentSpec, Pro2Guard, SABER, CaMeL, and closely related governance systems.
2. Extend the completed AgentGovBench source/scorer audit with a real Foundry adapter and independent evidence-completeness checks. Keep official scores distinct from supplemental tests.
3. Extend the seven pinned source comparisons into matched runnable baselines, recording configurations and package licenses. Produce reproducible environments rather than a star-count leaderboard.
4. Reproduce the nearest relevant baseline before designing a new mechanism or benchmark. Revise the contribution if existing methods already solve it.
5. Track literature updates until submission. The current source survey is adequate for architectural direction, not a guarantee that no overlapping paper exists.
