"""Tests for workspace-identity VM resolution."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from rich.console import Console

from agentsb.errors import AgentsbError
from agentsb.workspace import (
    VMRegistry,
    VMRegistryEntry,
    WorkspaceResolver,
    generate_vm_name,
)


console = Console(stderr=True, highlight=False)


# ============================= VMRegistry ==================================

def test_registry_empty_when_missing(tmp_path):
    reg = VMRegistry(path=tmp_path / "registry.json")
    assert reg.all() == []
    assert reg.find_by_inode(1, 1) is None


def test_registry_register_and_find_by_inode(tmp_path):
    reg = VMRegistry(path=tmp_path / "registry.json")
    (tmp_path / "ws").mkdir()
    ws = tmp_path / "ws"

    entry = reg.register("agentsb-test-abcd1234", ws)
    assert entry.vm_name == "agentsb-test-abcd1234"
    assert Path(entry.workspace_path) == ws

    st = ws.stat()
    found = reg.find_by_inode(st.st_dev, st.st_ino)
    assert found is not None
    assert found.vm_name == "agentsb-test-abcd1234"


def test_registry_register_replaces_existing_vm_name(tmp_path):
    reg = VMRegistry(path=tmp_path / "registry.json")
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()

    reg.register("same-name", tmp_path / "a")
    reg.register("same-name", tmp_path / "b")

    entries = reg.all()
    assert len(entries) == 1
    assert entries[0].workspace_path == str(tmp_path / "b")


def test_registry_unregister(tmp_path):
    reg = VMRegistry(path=tmp_path / "registry.json")
    (tmp_path / "ws").mkdir()
    reg.register("vm-1", tmp_path / "ws")
    reg.unregister("vm-1")
    assert reg.all() == []


def test_registry_tolerates_corrupt_json(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text("{ not valid json")
    reg = VMRegistry(path=path)
    # Load returns empty rather than crashing.
    assert reg.all() == []


# ============================= generate_vm_name ============================

def test_generate_vm_name_is_sanitized(tmp_path):
    ws = tmp_path / "Weird Project Name!!"
    ws.mkdir()
    name = generate_vm_name(ws)
    assert name.startswith("agentsb-")
    # No uppercase, no spaces, no special chars.
    assert all(c.isalnum() or c == "-" for c in name)


def test_generate_vm_name_is_stable_for_same_inode(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    a = generate_vm_name(ws)
    b = generate_vm_name(ws)
    assert a == b


def test_generate_vm_name_differs_for_different_inodes(tmp_path):
    a_dir = tmp_path / "a"
    b_dir = tmp_path / "b"
    a_dir.mkdir()
    b_dir.mkdir()
    assert generate_vm_name(a_dir) != generate_vm_name(b_dir)


# ============================= WorkspaceResolver ===========================

class _StubInputs:
    """Scripted answers to console.input()."""
    def __init__(self, answers):
        self._answers = list(answers)
        self.calls = 0

    def __call__(self, prompt=""):
        self.calls += 1
        if not self._answers:
            raise RuntimeError("unexpected extra input() call")
        return self._answers.pop(0)


def _resolver(
    tmp_path,
    *,
    existing_vms=(),
    vm_exists_override=None,
):
    reg = VMRegistry(path=tmp_path / "registry.json")
    vm_exists = vm_exists_override or (lambda name: name in set(existing_vms))
    return WorkspaceResolver(reg, console, vm_exists=vm_exists), reg


def _patch_home(monkeypatch, home: Path) -> None:
    monkeypatch.setenv("HOME", str(home))


def _make_tty(monkeypatch, is_tty: bool) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: is_tty)


def test_resolve_new_workspace_generates_and_registers(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    ws = tmp_path / "proj"
    ws.mkdir()

    resolver, reg = _resolver(tmp_path)
    vm_name, mount = resolver.resolve(ws)

    assert vm_name.startswith("agentsb-")
    assert mount == ws.resolve()
    # Entry persisted
    assert any(e.vm_name == vm_name for e in reg.all())


def test_resolve_exact_match_returns_registered_vm(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    ws = tmp_path / "proj"
    ws.mkdir()

    resolver, reg = _resolver(tmp_path, existing_vms=("agentsb-existing",))
    reg.register("agentsb-existing", ws)

    vm_name, mount = resolver.resolve(ws)
    assert vm_name == "agentsb-existing"
    assert mount == ws.resolve()


def test_resolve_stale_registry_entry_creates_fresh_vm(monkeypatch, tmp_path):
    """If a registry entry points at a VM that no longer exists, treat it
    as absent and create a new VM (possibly with the same name)."""
    _patch_home(monkeypatch, tmp_path)
    ws = tmp_path / "proj"
    ws.mkdir()

    # Existing VMs = none; registry claims agentsb-stale exists.
    resolver, reg = _resolver(tmp_path, existing_vms=())
    reg.register("agentsb-stale", ws)

    vm_name, mount = resolver.resolve(ws)
    assert vm_name != "agentsb-stale"
    assert mount == ws.resolve()


def test_resolve_ancestor_reuse_accepted(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    parent = tmp_path / "proj"
    child = parent / "sub" / "deep"
    child.mkdir(parents=True)

    resolver, reg = _resolver(tmp_path, existing_vms=("agentsb-ancestor",))
    reg.register("agentsb-ancestor", parent)

    # Interactive TTY, user explicitly accepts (prompt defaults to No, so
    # the user must type y/yes to reuse).
    _make_tty(monkeypatch, True)
    monkeypatch.setattr(resolver._console, "input", _StubInputs(["y"]))

    vm_name, mount = resolver.resolve(child)
    assert vm_name == "agentsb-ancestor"
    assert mount == parent.resolve()


def test_resolve_ancestor_reuse_default_declines(monkeypatch, tmp_path):
    """Blank <Enter> on the reuse prompt defaults to No — fresh VM is created."""
    _patch_home(monkeypatch, tmp_path)
    parent = tmp_path / "proj"
    child = parent / "sub"
    child.mkdir(parents=True)

    resolver, reg = _resolver(tmp_path, existing_vms=("agentsb-ancestor",))
    reg.register("agentsb-ancestor", parent)

    _make_tty(monkeypatch, True)
    monkeypatch.setattr(resolver._console, "input", _StubInputs([""]))

    vm_name, mount = resolver.resolve(child)
    assert vm_name != "agentsb-ancestor"
    assert mount == child.resolve()


def test_resolve_ancestor_reuse_declined_creates_new(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    parent = tmp_path / "proj"
    child = parent / "sub"
    child.mkdir(parents=True)

    resolver, reg = _resolver(tmp_path, existing_vms=("agentsb-ancestor",))
    reg.register("agentsb-ancestor", parent)

    _make_tty(monkeypatch, True)
    monkeypatch.setattr(resolver._console, "input", _StubInputs(["n"]))

    vm_name, mount = resolver.resolve(child)
    assert vm_name != "agentsb-ancestor"
    assert mount == child.resolve()


def test_resolve_ancestor_reuse_non_tty_defaults_fresh(monkeypatch, tmp_path):
    """Non-interactive runs don't reuse an ancestor VM — a fresh, scoped VM
    is created instead. Reuse has security implications (the agent sees the
    ancestor's whole mount) so it must be an explicit interactive choice."""
    _patch_home(monkeypatch, tmp_path)
    parent = tmp_path / "proj"
    child = parent / "sub"
    child.mkdir(parents=True)

    resolver, reg = _resolver(tmp_path, existing_vms=("agentsb-ancestor",))
    reg.register("agentsb-ancestor", parent)

    _make_tty(monkeypatch, False)
    vm_name, mount = resolver.resolve(child)
    assert vm_name != "agentsb-ancestor"
    assert mount == child.resolve()


def test_resolve_outside_home_refuses_non_tty(monkeypatch, tmp_path):
    """Non-TTY + outside $HOME should raise, not prompt."""
    # $HOME is elsewhere — tmp_path is outside it.
    other_home = tmp_path / "fake-home"
    other_home.mkdir()
    _patch_home(monkeypatch, other_home)

    ws = tmp_path / "outside"
    ws.mkdir()

    resolver, _ = _resolver(tmp_path)
    _make_tty(monkeypatch, False)

    with pytest.raises(AgentsbError) as excinfo:
        resolver.resolve(ws)
    assert "$HOME" in str(excinfo.value) or "non-interactive" in str(excinfo.value)


def test_resolve_outside_home_tty_accepted(monkeypatch, tmp_path):
    other_home = tmp_path / "fake-home"
    other_home.mkdir()
    _patch_home(monkeypatch, other_home)

    ws = tmp_path / "outside"
    ws.mkdir()

    resolver, _ = _resolver(tmp_path)
    _make_tty(monkeypatch, True)
    monkeypatch.setattr(resolver._console, "input", _StubInputs(["y"]))

    vm_name, mount = resolver.resolve(ws)
    assert vm_name.startswith("agentsb-")
    assert mount == ws.resolve()


def test_resolve_outside_home_tty_declined(monkeypatch, tmp_path):
    other_home = tmp_path / "fake-home"
    other_home.mkdir()
    _patch_home(monkeypatch, other_home)

    ws = tmp_path / "outside"
    ws.mkdir()

    resolver, _ = _resolver(tmp_path)
    _make_tty(monkeypatch, True)
    monkeypatch.setattr(resolver._console, "input", _StubInputs([""]))  # default no

    with pytest.raises(AgentsbError):
        resolver.resolve(ws)


def test_resolve_symlinked_paths_map_to_same_vm(monkeypatch, tmp_path):
    """Two paths that resolve to the same directory on disk should return
    the same VM — the whole point of inode-based identity."""
    _patch_home(monkeypatch, tmp_path)
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)

    resolver, reg = _resolver(tmp_path)
    vm_a, mount_a = resolver.resolve(real)
    vm_b, mount_b = resolver.resolve(link)

    assert vm_a == vm_b
    assert mount_a == mount_b
