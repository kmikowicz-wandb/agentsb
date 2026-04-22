"""Tests for the CLI layer: argparse wiring, env forwarding."""
from __future__ import annotations

import pytest

from agentsb import cli


def _parse(argv, agents=("claude", "codex", "aider", "forge")):
    return cli.parse(list(argv), list(agents))


# -------------------- parse ---------------------------------------------

def test_parse_agent_only():
    ns, agent, args = _parse(["claude"])
    assert ns.mode == "run"
    assert agent == "claude"
    assert args == []


def test_parse_agent_with_args():
    ns, agent, args = _parse(["claude", "-p", "hello"])
    assert agent == "claude"
    assert args == ["-p", "hello"]


def test_parse_wrapper_flag_before_agent():
    ns, agent, args = _parse(["--with-claude-config", "claude", "-p", "hi"])
    assert ns.with_claude_config is True
    assert agent == "claude"
    assert args == ["-p", "hi"]


def test_parse_workspace_flag_with_value():
    ns, agent, args = _parse(["-w", "/tmp", "claude"])
    assert ns.workspace == "/tmp"
    assert agent == "claude"


def test_parse_workspace_flag_equals_form():
    ns, agent, args = _parse(["--workspace=/tmp", "claude"])
    assert ns.workspace == "/tmp"
    assert agent == "claude"


def test_parse_shell_mode_without_agent():
    ns, agent, args = _parse(["--shell"])
    assert ns.mode == "shell"
    assert agent is None
    assert args == []


def test_parse_ephemeral_flag():
    ns, agent, args = _parse(["--ephemeral", "claude"])
    assert ns.ephemeral is True
    assert agent == "claude"


def test_parse_rejects_unknown_agent(capsys):
    with pytest.raises(SystemExit) as excinfo:
        _parse(["unknown-agent-name"])
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "unknown flag or agent" in err


def test_parse_rejects_unknown_flag_before_agent():
    with pytest.raises(SystemExit):
        _parse(["--totally-bogus", "claude"])


def test_parse_explicit_double_dash_before_agent_args():
    ns, agent, args = _parse(["claude", "--", "--help"])
    assert agent == "claude"
    assert args == ["--help"]


def test_parse_mode_defaults_to_run():
    ns, agent, _ = _parse(["claude"])
    assert ns.mode == "run"


# -------------------- forwarded_env_pairs -------------------------------

def test_forwarded_env_pairs_returns_only_set_keys(monkeypatch):
    for k in cli.FORWARDED_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("TERM", "xterm-256color")

    pairs = cli.forwarded_env_pairs()
    assert "ANTHROPIC_API_KEY=sk-test" in pairs
    assert "TERM=xterm-256color" in pairs
    assert not any(p.startswith("OPENAI_API_KEY=") for p in pairs)


def test_forwarded_env_pairs_empty_when_nothing_set(monkeypatch):
    for k in cli.FORWARDED_ENV:
        monkeypatch.delenv(k, raising=False)
    assert cli.forwarded_env_pairs() == []
