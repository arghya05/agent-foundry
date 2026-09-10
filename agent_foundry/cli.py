"""foundry — a thin CLI over existing agent_foundry modules. No new engine:
every subcommand is composition over something that already exists
(scaffold.create_agent, core.evalgate.run_eval, serve.build_http_app) —
closing the "framework should ship a CLI" gap without reimplementing
anything those modules already do.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def _load_module(path: str) -> Any:
    spec = importlib.util.spec_from_file_location(Path(path).stem, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"can't import {path!r} as a Python module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cmd_init(args: argparse.Namespace) -> None:
    from .scaffold import create_agent

    tools = [t.strip() for t in args.tools.split(",") if t.strip()] if args.tools else []
    paths = create_agent(args.name, directory=args.directory, tools=tools)
    print(f"wrote {paths['prompt']}")
    print(f"wrote {paths['agent']}")
    print(f"next: fill in the TODOs, set ANTHROPIC_API_KEY, then run: foundry run {paths['agent']}")


def cmd_run(args: argparse.Namespace) -> None:
    if args.spec is not None:
        if not args.message:
            raise SystemExit("foundry run --spec needs --message")
        from .agent_spec import AgentSpec, build_agent

        loader = AgentSpec.from_yaml if args.spec.endswith((".yaml", ".yml")) else AgentSpec.from_json
        agent = build_agent(loader(args.spec))
        result = agent.run(args.message)
        print(result.content)
        return
    if not args.script:
        raise SystemExit("foundry run needs either a script or --spec")
    raise SystemExit(subprocess.call([sys.executable, args.script]))


def cmd_eval(args: argparse.Namespace) -> None:
    from .core.evalgate import EvalCase, run_eval

    module = _load_module(args.script)
    agent = getattr(module, args.agent_attr, None)
    if agent is None:
        raise SystemExit(f"{args.script!r} has no top-level `{args.agent_attr}` — pass --agent-attr to name it")

    dataset = json.loads(Path(args.dataset).read_text())
    cases = [EvalCase(**case) for case in dataset]
    scorecard = run_eval(agent, cases, dataset_name=Path(args.dataset).stem)
    print(scorecard.render())

    if args.thresholds:
        ok, reasons = scorecard.passes(json.loads(args.thresholds))
        if not ok:
            print("\nFAILED thresholds:")
            for reason in reasons:
                print(f"  - {reason}")
            raise SystemExit(1)


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    module = _load_module(args.script)
    app = getattr(module, "app", None)
    if app is None:
        graph = getattr(module, "graph", None)
        if graph is None:
            raise SystemExit(f"{args.script!r} has no top-level `app` or `graph` for foundry to serve")
        from .serve import build_http_app
        app = build_http_app(graph, allow_unauthenticated_demo=args.allow_unauthenticated_demo)
    uvicorn.run(app, host=args.host, port=args.port)


_EXTRAS = (
    ("openai", "openai"),
    ("mcp", "mcp"),
    ("redis", "redis"),
    ("rag (chromadb)", "chromadb"),
    ("serve (fastapi)", "fastapi"),
    ("cedar (cedarpy)", "cedarpy"),
    ("otel (opentelemetry)", "opentelemetry.sdk"),
    ("a2a (a2a-sdk)", "a2a"),
    ("autogen", "autogen_agentchat"),
)


def cmd_inspect(args: argparse.Namespace) -> None:
    from . import __version__

    print(f"agent_foundry {__version__}")
    print()
    print("optional extras:")
    for label, module_name in _EXTRAS:
        try:
            importlib.import_module(module_name)
            print(f"  [x] {label}")
        except ImportError:
            print(f"  [ ] {label} — not installed (pip install 'agent-foundry[...]')")


def cmd_trace(args: argparse.Namespace) -> None:
    path = Path(args.path)
    if not path.exists():
        raise SystemExit(f"no such file: {path}")
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    for line in lines[-args.n:]:
        print(json.dumps(json.loads(line)))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="foundry", description="Agent Foundry CLI — thin composition over existing modules.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="scaffold a new agent (agent_foundry.scaffold)")
    p_init.add_argument("name")
    p_init.add_argument("--directory", default=".")
    p_init.add_argument("--tools", default="", help="comma-separated tool names, e.g. lookup_lead,send_email")
    p_init.set_defaults(func=cmd_init)

    p_run = sub.add_parser("run", help="run a scaffolded agent script, or (with --spec) a declarative AgentSpec")
    p_run.add_argument("script", nargs="?", help="a scaffolded .py script — omit when using --spec")
    p_run.add_argument("--spec", default=None, help="an AgentSpec file (.yaml/.yml or .json) to build and run instead of --script")
    p_run.add_argument("--message", default=None, help="the message to send the --spec agent (required with --spec)")
    p_run.set_defaults(func=cmd_run)

    p_eval = sub.add_parser("eval", help="score an Agent against a JSON dataset of cases (agent_foundry.core.evalgate)")
    p_eval.add_argument("script", help="a .py file with a top-level Agent instance")
    p_eval.add_argument("dataset", help="a JSON file: a list of objects matching EvalCase fields (input, name, expected_substring, expected_tool)")
    p_eval.add_argument("--agent-attr", default="agent", help="name of the top-level Agent variable in `script` (default: agent)")
    p_eval.add_argument("--thresholds", default=None, help='JSON object of Scorecard.passes() thresholds, e.g. \'{"task_success_rate_min": 0.9}\' — exits 1 if not met')
    p_eval.set_defaults(func=cmd_eval)

    p_serve = sub.add_parser("serve", help="serve a script's top-level `app` or `graph` over HTTP (agent_foundry.serve)")
    p_serve.add_argument("script")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    p_serve.add_argument("--allow-unauthenticated-demo", action="store_true",
                          help="opt into the unauthenticated demo behavior — the script's `app` (if it built one with its own auth already wired in) is used as-is regardless")
    p_serve.set_defaults(func=cmd_serve)

    p_inspect = sub.add_parser("inspect", help="show the installed version and which optional extras are available")
    p_inspect.set_defaults(func=cmd_inspect)

    p_trace = sub.add_parser("trace", help="tail a JSONL eval/audit log file")
    p_trace.add_argument("path")
    p_trace.add_argument("-n", type=int, default=20, help="number of most recent lines to show")
    p_trace.set_defaults(func=cmd_trace)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
