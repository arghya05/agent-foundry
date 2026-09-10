"""A tiny standalone module for test_agent_spec.py's resolve_tool()/AgentSpec
tests to reference by import path ("_spec_fixture_tools:echo") — kept
separate from conftest.py so it's importable on its own, the same way a
real user's tools module would be."""


def echo(text: str) -> str:
    """Return text unchanged."""
    return text
