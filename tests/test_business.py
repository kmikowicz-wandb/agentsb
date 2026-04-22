"""Tests for the business-logic layer."""
from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from agentsb import vm as vm_mod
from agentsb.agents import AgentManager, AgentRegistry
from agentsb.auth import AuthCoordinator
from agentsb.claude_sync import ClaudeConfigSync
from agentsb.errors import AgentsbError
from agentsb.paths import Paths
from agentsb.provision import ProvisionRunner
from agentsb.vm import LimaVM


console = Console(stderr=True, highlight=False)


# -------------------- Paths ---------------------------------------------

def test_paths_default_points_at_repo_root():
    p = Paths()
    assert (p.home / "lima" / "base.yaml").exists()
    assert p.base_template.exists()
    assert p.agents_dir.is_dir()


def test_paths_respects_explicit_home(tmp_path):
    p = Paths(home=tmp_path)
    assert p.home == tmp_path.resolve()
    assert p.lima_dir == tmp_path.resolve() / "lima"


def test_paths_respects_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTSB_HOME", str(tmp_path))
    p = Paths()
    assert p.home == tmp_path.resolve()


def test_paths_agent_fragment_path():
    p = Paths()
    assert p.agent_fragment("claude") == p.agents_dir / "claude.yaml"


# -------------------- AgentRegistry -------------------------------------

def test_registry_lists_ships_defaults():
    reg = AgentRegistry(Paths())
    names = reg.list()
    for expected in ("aider", "claude", "codex", "forge"):
        assert expected in names
    assert "base" not in names


def test_registry_list_is_sorted():
    reg = AgentRegistry(Paths())
    assert reg.list() == sorted(reg.list())


def test_registry_list_empty_when_dir_missing(tmp_path):
    reg = AgentRegistry(Paths(home=tmp_path))
    assert reg.list() == []


def test_registry_has_and_fragment(tmp_path):
    agents_dir = tmp_path / "lima" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "foo.yaml").write_text("provision: []\n")

    reg = AgentRegistry(Paths(home=tmp_path))
    assert reg.has("foo") is True
    assert reg.has("bar") is False
    assert reg.fragment("foo") == agents_dir / "foo.yaml"
    with pytest.raises(KeyError):
        reg.fragment("bar")


# -------------------- LimaVM (subprocess mocked) ------------------------

class _R:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _mock_subprocess(monkeypatch, handler):
    def fake_run(argv, **kwargs):
        return handler(argv, kwargs)

    monkeypatch.setattr(vm_mod.subprocess, "run", fake_run)


def _make_vm():
    return LimaVM(
        name="testvm",
        template=Path("/dev/null"),
        workspace=Path("/tmp"),
        console=console,
    )


def test_lima_vm_status_running(monkeypatch):
    _mock_subprocess(monkeypatch, lambda *_: _R(0, "Running\n"))
    assert _make_vm().status() == "Running"


def test_lima_vm_status_missing(monkeypatch):
    _mock_subprocess(monkeypatch, lambda *_: _R(1, ""))
    assert _make_vm().status() == ""


def test_lima_vm_status_strips_extra_lines(monkeypatch):
    _mock_subprocess(monkeypatch, lambda *_: _R(0, "Stopped\nnoise\n"))
    assert _make_vm().status() == "Stopped"


def test_lima_vm_has_binary_true(monkeypatch):
    _mock_subprocess(monkeypatch, lambda *_: _R(0))
    assert _make_vm().has_binary("claude") is True


def test_lima_vm_has_binary_false(monkeypatch):
    _mock_subprocess(monkeypatch, lambda *_: _R(1))
    assert _make_vm().has_binary("nope") is False


def test_lima_vm_exec_script_uses_sudo_for_system(monkeypatch):
    seen = []
    _mock_subprocess(monkeypatch, lambda argv, kw: (seen.append(argv) or _R(0)))
    _make_vm().exec_script("echo hi", as_root=True)
    assert "sudo" in seen[-1]


def test_lima_vm_exec_script_user_mode_no_sudo(monkeypatch):
    seen = []
    _mock_subprocess(monkeypatch, lambda argv, kw: (seen.append(argv) or _R(0)))
    _make_vm().exec_script("echo hi", as_root=False)
    assert "sudo" not in seen[-1]


# -------------------- ProvisionRunner + AgentManager --------------------

class FakeVM:
    """Stand-in for LimaVM in higher-layer tests."""

    def __init__(self, *, installed=(), exec_rc=0, exec_rcs=None, interactive_rc=0):
        self._installed = set(installed)
        self._exec_rc = exec_rc
        # exec_rcs is a callable(script) → int for per-script exit codes,
        # useful for auth tests where check and login scripts differ.
        self._exec_rcs = exec_rcs
        self._interactive_rc = interactive_rc
        self.exec_calls: list[tuple[bool, str, bool]] = []
        self.interactive_calls: list[str] = []
        self.mkdir_calls: list[str] = []
        self.copy_calls: list[tuple[Path, str]] = []

    def has_binary(self, name):
        return name in self._installed

    def exec_script(self, script, *, as_root, silent=False):
        self.exec_calls.append((as_root, script, silent))
        if self._exec_rcs is not None:
            return self._exec_rcs(script)
        return self._exec_rc

    def run_interactive(self, command):
        self.interactive_calls.append(command)
        return self._interactive_rc

    def mkdir(self, path):
        self.mkdir_calls.append(path)

    def copy_in(self, src, dest):
        self.copy_calls.append((src, dest))


def test_provision_runner_runs_each_block(tmp_path):
    fragment = tmp_path / "frag.yaml"
    fragment.write_text(
        "provision:\n"
        "  - mode: system\n"
        "    script: echo SYSTEM\n"
        "  - mode: user\n"
        "    script: echo USER\n"
    )

    vm = FakeVM()
    ProvisionRunner(vm, console).run(fragment, label="demo")

    assert [(a, s) for (a, s, _) in vm.exec_calls] == [
        (True,  "echo SYSTEM"),
        (False, "echo USER"),
    ]


def test_provision_runner_skips_empty_scripts(tmp_path):
    fragment = tmp_path / "frag.yaml"
    fragment.write_text(
        "provision:\n"
        "  - mode: system\n"
        "    script: ''\n"
        "  - mode: system\n"
        "    script: echo HI\n"
    )

    vm = FakeVM()
    ProvisionRunner(vm, console).run(fragment, label="demo")
    assert [(a, s) for (a, s, _) in vm.exec_calls] == [(True, "echo HI")]


def test_provision_runner_raises_on_nonzero_exit(tmp_path):
    fragment = tmp_path / "frag.yaml"
    fragment.write_text(
        "provision:\n"
        "  - mode: system\n"
        '    script: "exit 1"\n'
    )

    vm = FakeVM(exec_rc=1)
    runner = ProvisionRunner(vm, console)
    with pytest.raises(AgentsbError) as excinfo:
        runner.run(fragment, label="demo")
    assert "failed at step 1" in str(excinfo.value)


def _mk_manager(tmp_path, installed=(), exec_rc=0, fragments=None):
    agents_dir = tmp_path / "lima" / "agents"
    agents_dir.mkdir(parents=True)
    for name, content in (fragments or {}).items():
        (agents_dir / f"{name}.yaml").write_text(content)

    registry = AgentRegistry(Paths(home=tmp_path))
    vm = FakeVM(installed=installed, exec_rc=exec_rc)
    runner = ProvisionRunner(vm, console)
    return AgentManager(registry, vm, runner, console), vm


def test_manager_skips_install_when_binary_present(tmp_path):
    mgr, vm = _mk_manager(
        tmp_path,
        installed={"already-there"},
        fragments={"already-there": "provision:\n  - mode: system\n    script: echo X\n"},
    )
    mgr.ensure_installed("already-there")
    assert vm.exec_calls == []


def test_manager_runs_provision_when_binary_absent(tmp_path):
    frag = "provision:\n  - mode: system\n    script: echo INSTALLING\n"
    mgr, vm = _mk_manager(tmp_path, installed=set(), fragments={"fresh": frag})
    mgr.ensure_installed("fresh")
    assert [(a, s) for (a, s, _) in vm.exec_calls] == [(True, "echo INSTALLING")]


def test_manager_unknown_agent_raises(tmp_path):
    mgr, vm = _mk_manager(tmp_path, installed=set(), fragments={})
    with pytest.raises(KeyError):
        mgr.ensure_installed("no-such-agent")
    assert vm.exec_calls == []


def test_manager_propagates_provision_failure(tmp_path):
    frag = 'provision:\n  - mode: system\n    script: "exit 1"\n'
    mgr, vm = _mk_manager(
        tmp_path, installed=set(), exec_rc=1, fragments={"broken": frag},
    )
    with pytest.raises(AgentsbError):
        mgr.ensure_installed("broken")


# -------------------- ClaudeConfigSync -----------------------------------

def test_claude_sync_noop_when_home_claude_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    vm = FakeVM()
    ClaudeConfigSync(vm, console).sync()
    assert vm.copy_calls == []
    assert vm.mkdir_calls == []


def test_claude_sync_copies_only_present_items(monkeypatch, tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "CLAUDE.md").write_text("hi")
    (tmp_path / ".claude" / "commands").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))

    vm = FakeVM()
    ClaudeConfigSync(vm, console).sync()

    copied_names = [src.name for src, _ in vm.copy_calls]
    assert "CLAUDE.md" in copied_names
    assert "commands" in copied_names
    assert "settings.json" not in copied_names


# -------------------- AuthCoordinator ------------------------------------

def _write_fragment(tmp_path, content: str) -> Path:
    f = tmp_path / "frag.yaml"
    f.write_text(content)
    return f


def test_auth_coordinator_skips_fragment_without_auth_block(tmp_path):
    frag = _write_fragment(
        tmp_path,
        "provision:\n  - mode: system\n    script: echo X\n",
    )
    vm = FakeVM()
    AuthCoordinator(vm, console).ensure_authed("aider", frag)
    assert vm.exec_calls == []
    assert vm.interactive_calls == []


def test_auth_coordinator_noop_when_check_passes(tmp_path):
    frag = _write_fragment(
        tmp_path,
        "auth:\n  check: test -f /creds\n  login: do-login\n",
    )
    vm = FakeVM(exec_rc=0)   # check passes
    AuthCoordinator(vm, console).ensure_authed("claude", frag)
    # one exec for the check, no interactive login
    assert len(vm.exec_calls) == 1
    assert vm.exec_calls[0][2] is True   # silent=True
    assert vm.interactive_calls == []


def test_auth_coordinator_runs_login_when_check_fails(tmp_path):
    frag = _write_fragment(
        tmp_path,
        "auth:\n  check: test -f /creds\n  login: do-login\n",
    )
    # First check fails; after login, second check passes.
    calls = {"n": 0}

    def rcs(script):
        if script == "do-login":
            return 0
        # check script
        calls["n"] += 1
        return 1 if calls["n"] == 1 else 0

    vm = FakeVM(exec_rcs=rcs)
    AuthCoordinator(vm, console).ensure_authed("claude", frag)

    assert vm.interactive_calls == ["do-login"]
    # two check calls: before and after login
    check_calls = [c for c in vm.exec_calls if c[1] == "test -f /creds"]
    assert len(check_calls) == 2


def test_auth_coordinator_raises_when_login_exits_nonzero(tmp_path):
    frag = _write_fragment(
        tmp_path,
        "auth:\n  check: test -f /creds\n  login: do-login\n",
    )
    vm = FakeVM(exec_rc=1, interactive_rc=1)   # check fails, login fails
    with pytest.raises(AgentsbError) as excinfo:
        AuthCoordinator(vm, console).ensure_authed("claude", frag)
    assert "login command exited" in str(excinfo.value)


def test_auth_coordinator_raises_when_check_still_fails_after_login(tmp_path):
    frag = _write_fragment(
        tmp_path,
        "auth:\n  check: test -f /creds\n  login: do-login\n",
    )
    vm = FakeVM(exec_rc=1, interactive_rc=0)   # check always fails, login "succeeds"
    with pytest.raises(AgentsbError) as excinfo:
        AuthCoordinator(vm, console).ensure_authed("claude", frag)
    assert "auth check still fails" in str(excinfo.value)
