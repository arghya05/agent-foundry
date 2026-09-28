# Repository file coverage

Reviewed snapshot: `2623ea1b2fe2d93beb1976c0d4e2449891b6900a`, 2026-09-27.

All tracked files were inventoried and read by the scan process. Text and Python syntax/symbols were scanned; selected source paths were manually examined in depth. PDF text was extracted and architecture/UI images were visually inspected. This ledger does not equate automated reading with a manual line-by-line audit or dynamic coverage.

The review inspected the checked-out GitHub snapshot and selected public repository material. It does not claim to have audited every historical branch, pull-request discussion, or inaccessible GitHub page.

## Depth labels

- **Source scan + deep path review:** risk-bearing implementation and call paths manually inspected; still not every configuration tested.
- **Source scan:** contents/symbols included in the broad scan; no exhaustive per-function verification claimed.
- **Test scan + selected execution:** test content inventoried/scanned and module included in the recorded pytest run; see logs for pass/skip/failure.
- **Test scan:** included in inventory/content scan, not selected for execution.
- **Documentation/config scan:** read in the source/documentation corpus; statements are claims to check against implementation.
- **PDF extracted / image viewed:** non-code material included by the appropriate inspection method.

## Counts

- Tracked files: 225.
- Python files: 133.
- Python lines: 22,151.
- Test-named functions in AST inventory: 568.
- Test modules selected: 44.

Hashes and detailed symbols are in [file-inventory.json](evidence/file-inventory.json). New review artifacts are not included in the baseline inventory.

## Every tracked path

| Path | Lines | Inspection / execution |
| --- | ---: | --- |
| [.github/workflows/build.yml](../.github/workflows/build.yml) | 27 | Documentation/config scan |
| [.github/workflows/security.yml](../.github/workflows/security.yml) | 39 | Documentation/config scan |
| [.github/workflows/test.yml](../.github/workflows/test.yml) | 70 | Documentation/config scan |
| [.gitignore](../.gitignore) | 13 | Documentation/config scan |
| [Dockerfile](../Dockerfile) | 26 | Documentation/config scan |
| [LICENSE](../LICENSE) | 21 | Documentation/config scan |
| [README.md](../README.md) | 1199 | Documentation/config scan |
| [agent_foundry/__init__.py](../agent_foundry/__init__.py) | 78 | Source scan |
| [agent_foundry/a2a_bridge.py](../agent_foundry/a2a_bridge.py) | 136 | Source scan |
| [agent_foundry/agent_spec.py](../agent_foundry/agent_spec.py) | 230 | Source scan |
| [agent_foundry/autogen_bridge.py](../agent_foundry/autogen_bridge.py) | 32 | Source scan |
| [agent_foundry/batch.py](../agent_foundry/batch.py) | 106 | Source scan |
| [agent_foundry/benchmark.py](../agent_foundry/benchmark.py) | 69 | Source scan |
| [agent_foundry/blackboard.py](../agent_foundry/blackboard.py) | 52 | Source scan |
| [agent_foundry/channels.py](../agent_foundry/channels.py) | 95 | Source scan |
| [agent_foundry/cli.py](../agent_foundry/cli.py) | 198 | Source scan |
| [agent_foundry/context.py](../agent_foundry/context.py) | 457 | Source scan + deep path review |
| [agent_foundry/contracts.py](../agent_foundry/contracts.py) | 170 | Source scan |
| [agent_foundry/core/__init__.py](../agent_foundry/core/__init__.py) | 5 | Source scan |
| [agent_foundry/core/agent.py](../agent_foundry/core/agent.py) | 664 | Source scan + deep path review |
| [agent_foundry/core/engines.py](../agent_foundry/core/engines.py) | 122 | Source scan + deep path review |
| [agent_foundry/core/evalgate.py](../agent_foundry/core/evalgate.py) | 331 | Source scan + deep path review |
| [agent_foundry/core/execution_context.py](../agent_foundry/core/execution_context.py) | 118 | Source scan + deep path review |
| [agent_foundry/core/model_router.py](../agent_foundry/core/model_router.py) | 75 | Source scan |
| [agent_foundry/core/native_engine.py](../agent_foundry/core/native_engine.py) | 600 | Source scan + deep path review |
| [agent_foundry/core/native_orchestration.py](../agent_foundry/core/native_orchestration.py) | 667 | Source scan + deep path review |
| [agent_foundry/core/protocols.py](../agent_foundry/core/protocols.py) | 104 | Source scan + deep path review |
| [agent_foundry/core/registries.py](../agent_foundry/core/registries.py) | 105 | Source scan |
| [agent_foundry/core/result.py](../agent_foundry/core/result.py) | 29 | Source scan |
| [agent_foundry/core/run.py](../agent_foundry/core/run.py) | 185 | Source scan + deep path review |
| [agent_foundry/core/state_store.py](../agent_foundry/core/state_store.py) | 140 | Source scan + deep path review |
| [agent_foundry/core/tool_decorator.py](../agent_foundry/core/tool_decorator.py) | 72 | Source scan + deep path review |
| [agent_foundry/crewai_bridge.py](../agent_foundry/crewai_bridge.py) | 41 | Source scan + deep path review |
| [agent_foundry/data_connectors.py](../agent_foundry/data_connectors.py) | 83 | Source scan + deep path review |
| [agent_foundry/distributed.py](../agent_foundry/distributed.py) | 357 | Source scan + deep path review |
| [agent_foundry/escalation.py](../agent_foundry/escalation.py) | 59 | Source scan |
| [agent_foundry/eval.py](../agent_foundry/eval.py) | 115 | Source scan |
| [agent_foundry/eval_dataset.py](../agent_foundry/eval_dataset.py) | 83 | Source scan |
| [agent_foundry/events.py](../agent_foundry/events.py) | 90 | Source scan |
| [agent_foundry/experiments.py](../agent_foundry/experiments.py) | 48 | Source scan |
| [agent_foundry/feature_flags.py](../agent_foundry/feature_flags.py) | 40 | Source scan |
| [agent_foundry/guardrails.py](../agent_foundry/guardrails.py) | 148 | Source scan |
| [agent_foundry/http_tools.py](../agent_foundry/http_tools.py) | 54 | Source scan + deep path review |
| [agent_foundry/i18n.py](../agent_foundry/i18n.py) | 91 | Source scan |
| [agent_foundry/kpi.py](../agent_foundry/kpi.py) | 467 | Source scan + deep path review |
| [agent_foundry/livekit_bridge.py](../agent_foundry/livekit_bridge.py) | 148 | Source scan + deep path review |
| [agent_foundry/llm_gateway.py](../agent_foundry/llm_gateway.py) | 431 | Source scan + deep path review |
| [agent_foundry/mcp_tools.py](../agent_foundry/mcp_tools.py) | 147 | Source scan |
| [agent_foundry/observability.py](../agent_foundry/observability.py) | 223 | Source scan + deep path review |
| [agent_foundry/orchestration.py](../agent_foundry/orchestration.py) | 1533 | Source scan + deep path review |
| [agent_foundry/planning.py](../agent_foundry/planning.py) | 117 | Source scan |
| [agent_foundry/policy_engine.py](../agent_foundry/policy_engine.py) | 179 | Source scan + deep path review |
| [agent_foundry/prompts.py](../agent_foundry/prompts.py) | 76 | Source scan |
| [agent_foundry/quickstart.py](../agent_foundry/quickstart.py) | 66 | Source scan + deep path review |
| [agent_foundry/reinforcement.py](../agent_foundry/reinforcement.py) | 55 | Source scan |
| [agent_foundry/runtime.py](../agent_foundry/runtime.py) | 323 | Source scan + deep path review |
| [agent_foundry/sandbox.py](../agent_foundry/sandbox.py) | 52 | Source scan + deep path review |
| [agent_foundry/scaffold.py](../agent_foundry/scaffold.py) | 146 | Source scan |
| [agent_foundry/security.py](../agent_foundry/security.py) | 252 | Source scan + deep path review |
| [agent_foundry/serve.py](../agent_foundry/serve.py) | 323 | Source scan + deep path review |
| [agent_foundry/tools_gateway.py](../agent_foundry/tools_gateway.py) | 306 | Source scan + deep path review |
| [agent_foundry/ui/__init__.py](../agent_foundry/ui/__init__.py) | 3 | Source scan |
| [agent_foundry/ui/console.py](../agent_foundry/ui/console.py) | 52 | Source scan |
| [agent_foundry/versioning.py](../agent_foundry/versioning.py) | 70 | Source scan |
| [benchmarks/native_vs_langgraph.py](../benchmarks/native_vs_langgraph.py) | 306 | Source review + scripted microbenchmark execution |
| [benchmarks/scalability_load_test.py](../benchmarks/scalability_load_test.py) | 238 | Source scan |
| [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) | 300 | Documentation/config scan |
| [docs/Agent-Foundry-Architecture.pdf](../docs/Agent-Foundry-Architecture.pdf) | 1046 | PDF extracted |
| [docs/Agent-Foundry-Implementation-Guide.pdf](../docs/Agent-Foundry-Implementation-Guide.pdf) | 1069 | PDF extracted |
| [docs/BACKUP_DR.md](../docs/BACKUP_DR.md) | 96 | Documentation/config scan |
| [docs/HOW_IT_WORKS.md](../docs/HOW_IT_WORKS.md) | 608 | Documentation/config scan |
| [docs/HOW_IT_WORKS.pdf](../docs/HOW_IT_WORKS.pdf) | 2859 | PDF extracted |
| [docs/IMPLEMENTATION_GUIDE.md](../docs/IMPLEMENTATION_GUIDE.md) | 546 | Documentation/config scan |
| [docs/OWASP_LLM_TOP10.md](../docs/OWASP_LLM_TOP10.md) | 25 | Documentation/config scan |
| [docs/diagrams/architecture.jpg](../docs/diagrams/architecture.jpg) | — | Image viewed |
| [docs/diagrams/architecture.mmd](../docs/diagrams/architecture.mmd) | 137 | Documentation/config scan |
| [docs/diagrams/reference-universal-agentic-architecture-2026.png](../docs/diagrams/reference-universal-agentic-architecture-2026.png) | — | Image viewed |
| [docs/diagrams/turn-sequence.jpg](../docs/diagrams/turn-sequence.jpg) | — | Image viewed |
| [docs/diagrams/turn-sequence.mmd](../docs/diagrams/turn-sequence.mmd) | 31 | Documentation/config scan |
| [docs/qa/README.md](../docs/qa/README.md) | 100 | Documentation/config scan |
| [docs/qa/a2a_bridge/README.md](../docs/qa/a2a_bridge/README.md) | 63 | Documentation/config scan |
| [docs/qa/agent_spec/README.md](../docs/qa/agent_spec/README.md) | 85 | Documentation/config scan |
| [docs/qa/autogen_bridge/README.md](../docs/qa/autogen_bridge/README.md) | 51 | Documentation/config scan |
| [docs/qa/batch/README.md](../docs/qa/batch/README.md) | 67 | Documentation/config scan |
| [docs/qa/benchmark/README.md](../docs/qa/benchmark/README.md) | 63 | Documentation/config scan |
| [docs/qa/blackboard/README.md](../docs/qa/blackboard/README.md) | 67 | Documentation/config scan |
| [docs/qa/channels/README.md](../docs/qa/channels/README.md) | 63 | Documentation/config scan |
| [docs/qa/cli/README.md](../docs/qa/cli/README.md) | 71 | Documentation/config scan |
| [docs/qa/configuration/README.md](../docs/qa/configuration/README.md) | 48 | Documentation/config scan |
| [docs/qa/context/README.md](../docs/qa/context/README.md) | 59 | Documentation/config scan |
| [docs/qa/contracts/README.md](../docs/qa/contracts/README.md) | 63 | Documentation/config scan |
| [docs/qa/core_agent/README.md](../docs/qa/core_agent/README.md) | 75 | Documentation/config scan |
| [docs/qa/core_engines/README.md](../docs/qa/core_engines/README.md) | 63 | Documentation/config scan |
| [docs/qa/core_evalgate/README.md](../docs/qa/core_evalgate/README.md) | 67 | Documentation/config scan |
| [docs/qa/core_execution_context/README.md](../docs/qa/core_execution_context/README.md) | 63 | Documentation/config scan |
| [docs/qa/core_model_router/README.md](../docs/qa/core_model_router/README.md) | 71 | Documentation/config scan |
| [docs/qa/core_native_engine/README.md](../docs/qa/core_native_engine/README.md) | 63 | Documentation/config scan |
| [docs/qa/core_native_orchestration/README.md](../docs/qa/core_native_orchestration/README.md) | 63 | Documentation/config scan |
| [docs/qa/core_protocols/README.md](../docs/qa/core_protocols/README.md) | 63 | Documentation/config scan |
| [docs/qa/core_registries/README.md](../docs/qa/core_registries/README.md) | 73 | Documentation/config scan |
| [docs/qa/core_result/README.md](../docs/qa/core_result/README.md) | 63 | Documentation/config scan |
| [docs/qa/core_run/README.md](../docs/qa/core_run/README.md) | 67 | Documentation/config scan |
| [docs/qa/core_state_store/README.md](../docs/qa/core_state_store/README.md) | 83 | Documentation/config scan |
| [docs/qa/core_tool_decorator/README.md](../docs/qa/core_tool_decorator/README.md) | 71 | Documentation/config scan |
| [docs/qa/crewai_bridge/README.md](../docs/qa/crewai_bridge/README.md) | 51 | Documentation/config scan |
| [docs/qa/data_connectors/README.md](../docs/qa/data_connectors/README.md) | 51 | Documentation/config scan |
| [docs/qa/distributed/README.md](../docs/qa/distributed/README.md) | 67 | Documentation/config scan |
| [docs/qa/escalation/README.md](../docs/qa/escalation/README.md) | 63 | Documentation/config scan |
| [docs/qa/eval/README.md](../docs/qa/eval/README.md) | 63 | Documentation/config scan |
| [docs/qa/eval_dataset/README.md](../docs/qa/eval_dataset/README.md) | 63 | Documentation/config scan |
| [docs/qa/events/README.md](../docs/qa/events/README.md) | 63 | Documentation/config scan |
| [docs/qa/experiments/README.md](../docs/qa/experiments/README.md) | 63 | Documentation/config scan |
| [docs/qa/feature_flags/README.md](../docs/qa/feature_flags/README.md) | 63 | Documentation/config scan |
| [docs/qa/guardrails/README.md](../docs/qa/guardrails/README.md) | 67 | Documentation/config scan |
| [docs/qa/http_tools/README.md](../docs/qa/http_tools/README.md) | 51 | Documentation/config scan |
| [docs/qa/i18n/README.md](../docs/qa/i18n/README.md) | 47 | Documentation/config scan |
| [docs/qa/kpi/README.md](../docs/qa/kpi/README.md) | 75 | Documentation/config scan |
| [docs/qa/llm_gateway/README.md](../docs/qa/llm_gateway/README.md) | 83 | Documentation/config scan |
| [docs/qa/mcp_tools/README.md](../docs/qa/mcp_tools/README.md) | 59 | Documentation/config scan |
| [docs/qa/observability/README.md](../docs/qa/observability/README.md) | 63 | Documentation/config scan |
| [docs/qa/orchestration/README.md](../docs/qa/orchestration/README.md) | 83 | Documentation/config scan |
| [docs/qa/planning/README.md](../docs/qa/planning/README.md) | 63 | Documentation/config scan |
| [docs/qa/policy_engine/README.md](../docs/qa/policy_engine/README.md) | 63 | Documentation/config scan |
| [docs/qa/prompts/README.md](../docs/qa/prompts/README.md) | 67 | Documentation/config scan |
| [docs/qa/quickstart/README.md](../docs/qa/quickstart/README.md) | 51 | Documentation/config scan |
| [docs/qa/reinforcement/README.md](../docs/qa/reinforcement/README.md) | 63 | Documentation/config scan |
| [docs/qa/runtime/README.md](../docs/qa/runtime/README.md) | 71 | Documentation/config scan |
| [docs/qa/sandbox/README.md](../docs/qa/sandbox/README.md) | 69 | Documentation/config scan |
| [docs/qa/scaffold/README.md](../docs/qa/scaffold/README.md) | 63 | Documentation/config scan |
| [docs/qa/security/README.md](../docs/qa/security/README.md) | 64 | Documentation/config scan |
| [docs/qa/serve/README.md](../docs/qa/serve/README.md) | 67 | Documentation/config scan |
| [docs/qa/tools_gateway/README.md](../docs/qa/tools_gateway/README.md) | 59 | Documentation/config scan |
| [docs/qa/ui_console/README.md](../docs/qa/ui_console/README.md) | 47 | Documentation/config scan |
| [docs/qa/versioning/README.md](../docs/qa/versioning/README.md) | 63 | Documentation/config scan |
| [docs/screenshots/serve-demo-ui.png](../docs/screenshots/serve-demo-ui.png) | — | Image viewed |
| [examples/autonomous_workflow/README.md](../examples/autonomous_workflow/README.md) | 59 | Documentation/config scan |
| [examples/autonomous_workflow/agent.py](../examples/autonomous_workflow/agent.py) | 93 | Source scan |
| [examples/autonomous_workflow/agent.yaml](../examples/autonomous_workflow/agent.yaml) | 32 | Documentation/config scan |
| [examples/autonomous_workflow/eval_dataset.json](../examples/autonomous_workflow/eval_dataset.json) | 18 | Documentation/config scan |
| [examples/commerce_agent/README.md](../examples/commerce_agent/README.md) | 45 | Documentation/config scan |
| [examples/commerce_agent/agent.py](../examples/commerce_agent/agent.py) | 90 | Source scan |
| [examples/commerce_agent/agent.yaml](../examples/commerce_agent/agent.yaml) | 29 | Documentation/config scan |
| [examples/commerce_agent/eval_dataset.json](../examples/commerce_agent/eval_dataset.json) | 25 | Documentation/config scan |
| [examples/research_agent/README.md](../examples/research_agent/README.md) | 52 | Documentation/config scan |
| [examples/research_agent/agent.py](../examples/research_agent/agent.py) | 79 | Source scan |
| [examples/research_agent/agent.yaml](../examples/research_agent/agent.yaml) | 24 | Documentation/config scan |
| [examples/research_agent/eval_dataset.json](../examples/research_agent/eval_dataset.json) | 24 | Documentation/config scan |
| [examples/serve_http.py](../examples/serve_http.py) | 52 | Source scan |
| [examples/serve_http_distributed.py](../examples/serve_http_distributed.py) | 69 | Source scan |
| [examples/support_agent.py](../examples/support_agent.py) | 186 | Source scan |
| [examples/voice_agent/README.md](../examples/voice_agent/README.md) | 56 | Documentation/config scan |
| [examples/voice_agent/agent.py](../examples/voice_agent/agent.py) | 104 | Source scan |
| [prompts/support_agent.md](../prompts/support_agent.md) | 6 | Documentation/config scan |
| [pyproject.toml](../pyproject.toml) | 102 | Documentation/config scan |
| [pytest.ini](../pytest.ini) | 6 | Documentation/config scan |
| [requirements-distributed.txt](../requirements-distributed.txt) | 10 | Documentation/config scan |
| [requirements-test.txt](../requirements-test.txt) | 15 | Documentation/config scan |
| [requirements.txt](../requirements.txt) | 8 | Documentation/config scan |
| [tests/_mcp_test_server.py](../tests/_mcp_test_server.py) | 22 | Test scan |
| [tests/_spec_fixture_tools.py](../tests/_spec_fixture_tools.py) | 9 | Test scan |
| [tests/conftest.py](../tests/conftest.py) | 79 | Test scan |
| [tests/test_a2a_bridge.py](../tests/test_a2a_bridge.py) | 95 | Test scan |
| [tests/test_agent_distributed_redis.py](../tests/test_agent_distributed_redis.py) | 91 | Test scan |
| [tests/test_agent_serve.py](../tests/test_agent_serve.py) | 76 | Test scan |
| [tests/test_agent_spec.py](../tests/test_agent_spec.py) | 194 | Test scan + selected execution |
| [tests/test_agent_stream.py](../tests/test_agent_stream.py) | 234 | Test scan |
| [tests/test_async_tools.py](../tests/test_async_tools.py) | 60 | Test scan + selected execution |
| [tests/test_autogen_bridge.py](../tests/test_autogen_bridge.py) | 49 | Test scan |
| [tests/test_batch.py](../tests/test_batch.py) | 79 | Test scan + selected execution |
| [tests/test_benchmark_and_scaffold.py](../tests/test_benchmark_and_scaffold.py) | 63 | Test scan |
| [tests/test_cedar_policy_engine.py](../tests/test_cedar_policy_engine.py) | 79 | Test scan |
| [tests/test_cli.py](../tests/test_cli.py) | 187 | Test scan |
| [tests/test_context.py](../tests/test_context.py) | 211 | Test scan + selected execution |
| [tests/test_contracts_and_guardrails.py](../tests/test_contracts_and_guardrails.py) | 81 | Test scan + selected execution |
| [tests/test_core_agent.py](../tests/test_core_agent.py) | 299 | Test scan + selected execution |
| [tests/test_core_extensions.py](../tests/test_core_extensions.py) | 282 | Test scan + selected execution |
| [tests/test_crewai_bridge.py](../tests/test_crewai_bridge.py) | 65 | Test scan + selected execution |
| [tests/test_crewai_bridge_live.py](../tests/test_crewai_bridge_live.py) | 48 | Test scan |
| [tests/test_data_connectors.py](../tests/test_data_connectors.py) | 82 | Test scan + selected execution |
| [tests/test_distributed.py](../tests/test_distributed.py) | 273 | Test scan |
| [tests/test_escalation.py](../tests/test_escalation.py) | 52 | Test scan + selected execution |
| [tests/test_eval_and_kpi.py](../tests/test_eval_and_kpi.py) | 322 | Test scan + selected execution |
| [tests/test_eval_dataset.py](../tests/test_eval_dataset.py) | 90 | Test scan + selected execution |
| [tests/test_evalgate.py](../tests/test_evalgate.py) | 298 | Test scan + selected execution |
| [tests/test_events.py](../tests/test_events.py) | 58 | Test scan + selected execution |
| [tests/test_examples_autonomous_workflow.py](../tests/test_examples_autonomous_workflow.py) | 130 | Test scan |
| [tests/test_examples_commerce_agent.py](../tests/test_examples_commerce_agent.py) | 83 | Test scan |
| [tests/test_examples_research_agent.py](../tests/test_examples_research_agent.py) | 101 | Test scan |
| [tests/test_experiments.py](../tests/test_experiments.py) | 49 | Test scan + selected execution |
| [tests/test_feature_flags.py](../tests/test_feature_flags.py) | 49 | Test scan + selected execution |
| [tests/test_http_tools.py](../tests/test_http_tools.py) | 72 | Test scan |
| [tests/test_i18n.py](../tests/test_i18n.py) | 44 | Test scan + selected execution |
| [tests/test_idempotency.py](../tests/test_idempotency.py) | 77 | Test scan + selected execution |
| [tests/test_langgraph_optional.py](../tests/test_langgraph_optional.py) | 85 | Test scan |
| [tests/test_livekit_bridge.py](../tests/test_livekit_bridge.py) | 146 | Test scan |
| [tests/test_mcp_tools.py](../tests/test_mcp_tools.py) | 84 | Test scan |
| [tests/test_native_engine.py](../tests/test_native_engine.py) | 207 | Test scan + selected execution |
| [tests/test_native_engine_concurrency.py](../tests/test_native_engine_concurrency.py) | 117 | Test scan + selected execution |
| [tests/test_native_engine_mutual_exclusion.py](../tests/test_native_engine_mutual_exclusion.py) | 73 | Test scan + selected execution |
| [tests/test_native_engine_state_store.py](../tests/test_native_engine_state_store.py) | 122 | Test scan + selected execution |
| [tests/test_native_engine_tool_dispatch.py](../tests/test_native_engine_tool_dispatch.py) | 109 | Test scan + selected execution |
| [tests/test_native_orchestration.py](../tests/test_native_orchestration.py) | 164 | Test scan + selected execution |
| [tests/test_native_orchestration_concurrency.py](../tests/test_native_orchestration_concurrency.py) | 104 | Test scan + selected execution |
| [tests/test_native_orchestration_durability.py](../tests/test_native_orchestration_durability.py) | 181 | Test scan + selected execution |
| [tests/test_native_orchestration_hitl.py](../tests/test_native_orchestration_hitl.py) | 138 | Test scan + selected execution |
| [tests/test_native_orchestration_max_concurrency.py](../tests/test_native_orchestration_max_concurrency.py) | 67 | Test scan + selected execution |
| [tests/test_native_orchestration_streaming.py](../tests/test_native_orchestration_streaming.py) | 125 | Test scan + selected execution |
| [tests/test_observability.py](../tests/test_observability.py) | 51 | Test scan + selected execution |
| [tests/test_orchestration.py](../tests/test_orchestration.py) | 1335 | Test scan + selected execution |
| [tests/test_orchestration_concurrent_tools.py](../tests/test_orchestration_concurrent_tools.py) | 146 | Test scan + selected execution |
| [tests/test_planning.py](../tests/test_planning.py) | 46 | Test scan + selected execution |
| [tests/test_prompts.py](../tests/test_prompts.py) | 21 | Test scan + selected execution |
| [tests/test_protocol_substitutability.py](../tests/test_protocol_substitutability.py) | 281 | Test scan + selected execution |
| [tests/test_public_api_surface.py](../tests/test_public_api_surface.py) | 49 | Test scan + selected execution |
| [tests/test_run_lifecycle.py](../tests/test_run_lifecycle.py) | 196 | Test scan + selected execution |
| [tests/test_runtime.py](../tests/test_runtime.py) | 278 | Test scan + selected execution |
| [tests/test_runtime_override.py](../tests/test_runtime_override.py) | 94 | Test scan + selected execution |
| [tests/test_sandbox.py](../tests/test_sandbox.py) | 41 | Test scan + selected execution |
| [tests/test_security_and_policy_engine.py](../tests/test_security_and_policy_engine.py) | 363 | Test scan + selected execution |
| [tests/test_serve_and_channels.py](../tests/test_serve_and_channels.py) | 272 | Test scan + selected execution |
| [tests/test_state_store.py](../tests/test_state_store.py) | 82 | Test scan |
| [tests/test_state_store_backends.py](../tests/test_state_store_backends.py) | 198 | Test scan |
| [tests/test_tools_and_llm_gateway.py](../tests/test_tools_and_llm_gateway.py) | 396 | Test scan |
| [tests/test_versioning.py](../tests/test_versioning.py) | 85 | Test scan + selected execution |
| [tests/test_workflow_engine_protocol.py](../tests/test_workflow_engine_protocol.py) | 177 | Test scan + selected execution |
