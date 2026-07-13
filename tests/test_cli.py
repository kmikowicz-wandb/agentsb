"""Tests for the CLI layer: argparse wiring, env forwarding."""
from __future__ import annotations

import shutil
from unittest.mock import MagicMock, patch

import pytest

from agentsb import cli


def _parse(argv, agents=("claude", "codex", "aider", "forge")):
    return cli.parse(list(argv), list(agents))


# -------------------- parse ---------------------------------------------

def test_parse_agent_only():
    ns, subcmd, agent, args = _parse(["claude"])
    assert subcmd is None
    assert agent == "claude"
    assert args == []


def test_parse_agent_with_args():
    ns, subcmd, agent, args = _parse(["claude", "-p", "hello"])
    assert subcmd is None
    assert agent == "claude"
    assert args == ["-p", "hello"]


def test_parse_wrapper_flag_before_agent():
    ns, subcmd, agent, args = _parse(["--with-claude-config", "claude", "-p", "hi"])
    assert ns.with_claude_config is True
    assert subcmd is None
    assert agent == "claude"
    assert args == ["-p", "hi"]


def test_parse_workspace_flag_with_value():
    ns, subcmd, agent, args = _parse(["-w", "/tmp", "claude"])
    assert ns.workspace == "/tmp"
    assert subcmd is None
    assert agent == "claude"


def test_parse_workspace_flag_equals_form():
    ns, subcmd, agent, args = _parse(["--workspace=/tmp", "claude"])
    assert ns.workspace == "/tmp"
    assert subcmd is None
    assert agent == "claude"


def test_parse_shell_subcommand():
    ns, subcmd, agent, args = _parse(["shell"])
    assert subcmd == "shell"
    assert agent is None
    assert args == []


def test_parse_ephemeral_flag():
    ns, subcmd, agent, args = _parse(["--ephemeral", "claude"])
    assert ns.ephemeral is True
    assert subcmd is None
    assert agent == "claude"


def test_parse_rejects_unknown_agent(capsys):
    with pytest.raises(SystemExit) as excinfo:
        _parse(["unknown-agent-name"])
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "unknown command or agent" in err


def test_parse_rejects_unknown_flag_before_agent():
    with pytest.raises(SystemExit):
        _parse(["--totally-bogus", "claude"])


def test_parse_explicit_double_dash_before_agent_args():
    ns, subcmd, agent, args = _parse(["claude", "--", "--help"])
    assert subcmd is None
    assert agent == "claude"
    assert args == ["--help"]


def test_parse_no_args_returns_nones():
    ns, subcmd, agent, args = _parse([])
    assert subcmd is None
    assert agent is None
    assert args == []


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
    monkeypatch.setattr(cli, "_claude_oauth_token", lambda: None)
    assert cli.forwarded_env_pairs() == []


# -------------------- disk-check does not prune -------------------------

def test_disk_check_does_not_call_pruner(monkeypatch):
    """The daily cron job must never delete VMs — only mark for resize."""
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/limactl")
    monkeypatch.setattr("sys.argv", ["agentsb", "disk-check"])

    paths_mock = MagicMock()
    paths_mock.base_template.exists.return_value = True
    agent_registry_mock = MagicMock()
    agent_registry_mock.list.return_value = []
    check_mock = MagicMock()

    with patch("agentsb.cli.Paths", return_value=paths_mock), \
         patch("agentsb.cli.AgentRegistry", return_value=agent_registry_mock), \
         patch("agentsb.cli.VMRegistry"), \
         patch("agentsb.cli.Pruner") as MockPruner, \
         patch("agentsb.cli.check_and_mark_all", check_mock):
        cli.main()

    MockPruner.return_value.prune.assert_not_called()
    check_mock.assert_called_once()
