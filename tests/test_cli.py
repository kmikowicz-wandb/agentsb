"""Unit tests for agentsb's pure logic — argument parsing, agent discovery,
and environment forwarding. Anything touching Lima is integration-tested
elsewhere (or not at all in this basic suite).
"""
from __future__ import annotations

import os

import pytest

from conftest import agentsb


# -------------------- list_agents ----------------------------------------

def test_list_agents_returns_sorted_names():
    agents = agentsb.list_agents()
    assert agents == sorted(agents)
    # The repo ships these four by default.
    for expected in ("aider", "claude", "codex", "forge"):
        assert expected in agents


def test_list_agents_excludes_base(tmp_path, monkeypatch):
    """list_agents reads AGENTS_DIR, which is lima/agents — base.yaml lives
    one level up and must never appear in the agent list."""
    agents = agentsb.list_agents()
    assert "base" not in agents


# -------------------- parse (argv → ns, agent, agent_args) ---------------

def _parse(argv, agents=("claude", "codex", "aider", "forge")):
    return agentsb.parse(list(argv), list(agents))


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


def test_parse_rejects_unknown_flag_before_agent(capsys):
    with pytest.raises(SystemExit):
        _parse(["--totally-bogus", "claude"])
    # unknown flag captured via parse_known_args → ends up in `before`


def test_parse_explicit_double_dash_before_agent_args():
    # `agentsb claude -- --help` should pass --help to claude, not error.
    ns, agent, args = _parse(["claude", "--", "--help"])
    assert agent == "claude"
    assert args == ["--help"]


def test_parse_mode_defaults_to_run():
    ns, agent, _ = _parse(["claude"])
    assert ns.mode == "run"


# -------------------- forwarded_env_pairs --------------------------------

def test_forwarded_env_pairs_returns_only_set_keys(monkeypatch):
    # Clear all known keys, then set two.
    for k in agentsb.FORWARDED_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("TERM", "xterm-256color")

    pairs = agentsb.forwarded_env_pairs()
    assert "ANTHROPIC_API_KEY=sk-test" in pairs
    assert "TERM=xterm-256color" in pairs
    # Keys that aren't set must not appear.
    assert not any(p.startswith("OPENAI_API_KEY=") for p in pairs)


def test_forwarded_env_pairs_empty_when_nothing_set(monkeypatch):
    for k in agentsb.FORWARDED_ENV:
        monkeypatch.delenv(k, raising=False)
    assert agentsb.forwarded_env_pairs() == []


# -------------------- lima_status stdout parsing -------------------------

class _FakeResult:
    def __init__(self, returncode, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def test_lima_status_missing_vm(monkeypatch):
    monkeypatch.setattr(
        agentsb.subprocess, "run",
        lambda *a, **kw: _FakeResult(1, ""),
    )
    assert agentsb.lima_status("nope") == ""


def test_lima_status_running(monkeypatch):
    monkeypatch.setattr(
        agentsb.subprocess, "run",
        lambda *a, **kw: _FakeResult(0, "Running\n"),
    )
    assert agentsb.lima_status("agentsb") == "Running"


def test_lima_status_strips_extra_lines(monkeypatch):
    # If limactl ever emits warnings to stdout, we should take only the first
    # non-empty line.
    monkeypatch.setattr(
        agentsb.subprocess, "run",
        lambda *a, **kw: _FakeResult(0, "Stopped\nnoise\n"),
    )
    assert agentsb.lima_status("agentsb") == "Stopped"
