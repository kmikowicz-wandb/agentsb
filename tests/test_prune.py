"""Tests for Pruner — cleanup of orphaned registered VMs."""
from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from agentsb.prune import Pruner, orphan_reason
from agentsb.workspace import VMRegistry, VMRegistryEntry


console = Console(stderr=True, highlight=False)


# -------------------- orphan_reason --------------------------------------

def test_orphan_reason_none_when_path_matches(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    st = ws.stat()
    entry = VMRegistryEntry(
        vm_name="vm1",
        workspace_path=str(ws),
        dev=st.st_dev, inode=st.st_ino,
        created_at="2026-01-01T00:00:00+00:00",
    )
    assert orphan_reason(entry) is None


def test_orphan_reason_missing_directory(tmp_path):
    entry = VMRegistryEntry(
        vm_name="vm1",
        workspace_path=str(tmp_path / "never-existed"),
        dev=1, inode=1,
        created_at="2026-01-01T00:00:00+00:00",
    )
    assert orphan_reason(entry) == "directory missing"


def test_orphan_reason_inode_mismatch(tmp_path):
    """Path exists but inode differs (directory was replaced)."""
    ws = tmp_path / "proj"
    ws.mkdir()
    entry = VMRegistryEntry(
        vm_name="vm1",
        workspace_path=str(ws),
        dev=999999, inode=999999,   # deliberate mismatch
        created_at="2026-01-01T00:00:00+00:00",
    )
    assert orphan_reason(entry) is not None
    assert "inode mismatch" in orphan_reason(entry)


# -------------------- Pruner ---------------------------------------------

def _register(reg, vm_name, workspace):
    reg.register(vm_name, workspace)


def _forced_mismatch(reg_path, vm_name, workspace):
    """Write an entry with a deliberately-wrong inode — simulates a
    directory that was deleted and re-created after the registry was
    last written."""
    reg = VMRegistry(path=reg_path)
    reg.register(vm_name, workspace)
    # Overwrite with bogus dev/inode.
    import json
    data = json.loads(reg_path.read_text())
    for e in data["vms"]:
        if e["vm_name"] == vm_name:
            e["dev"] = 1
            e["inode"] = 1
    reg_path.write_text(json.dumps(data))


_not_running = lambda _: False  # noqa: E731 — test helper, VMs are stopped


def test_prune_noop_on_empty_registry(tmp_path):
    reg = VMRegistry(path=tmp_path / "registry.json")
    destroyed: list[str] = []
    p = Pruner(reg, console, is_running_fn=_not_running,
               destroy_fn=lambda n: destroyed.append(n) or 0)
    assert p.prune() == []
    assert destroyed == []


def test_prune_leaves_live_entries_alone(tmp_path):
    reg_path = tmp_path / "registry.json"
    ws = tmp_path / "proj"
    ws.mkdir()
    _register(VMRegistry(path=reg_path), "vm-live", ws)

    destroyed: list[str] = []
    p = Pruner(VMRegistry(path=reg_path), console, is_running_fn=_not_running,
               destroy_fn=lambda n: destroyed.append(n) or 0)
    pruned = p.prune()
    assert pruned == []
    assert destroyed == []
    # Entry remains.
    assert any(e.vm_name == "vm-live" for e in VMRegistry(path=reg_path).all())


def test_prune_removes_entries_with_missing_directories(tmp_path):
    reg_path = tmp_path / "registry.json"
    ws = tmp_path / "gone"
    ws.mkdir()
    _register(VMRegistry(path=reg_path), "vm-dead", ws)
    ws.rmdir()  # directory is gone

    destroyed: list[str] = []
    p = Pruner(VMRegistry(path=reg_path), console, is_running_fn=_not_running,
               destroy_fn=lambda n: destroyed.append(n) or 0)
    pruned = p.prune()

    assert [e.vm_name for e in pruned] == ["vm-dead"]
    assert destroyed == ["vm-dead"]
    assert VMRegistry(path=reg_path).all() == []


def test_prune_removes_entries_with_mismatched_inode(tmp_path):
    reg_path = tmp_path / "registry.json"
    ws = tmp_path / "replaced"
    ws.mkdir()
    _forced_mismatch(reg_path, "vm-replaced", ws)

    destroyed: list[str] = []
    p = Pruner(VMRegistry(path=reg_path), console, is_running_fn=_not_running,
               destroy_fn=lambda n: destroyed.append(n) or 0)
    pruned = p.prune()

    assert [e.vm_name for e in pruned] == ["vm-replaced"]
    assert destroyed == ["vm-replaced"]


def test_prune_keeps_registry_entry_when_destroy_fails(tmp_path):
    """If limactl delete fails, keep the registry entry so the user can
    investigate instead of silently dropping it."""
    reg_path = tmp_path / "registry.json"
    ws = tmp_path / "gone"
    ws.mkdir()
    _register(VMRegistry(path=reg_path), "vm-stubborn", ws)
    ws.rmdir()

    p = Pruner(VMRegistry(path=reg_path), console, is_running_fn=_not_running,
               destroy_fn=lambda n: 1)   # always "fail"
    pruned = p.prune()

    assert pruned == []
    assert any(e.vm_name == "vm-stubborn" for e in VMRegistry(path=reg_path).all())


def test_prune_stops_running_vm_before_destroy(tmp_path):
    """If a VM is running, it should be stopped before being destroyed."""
    reg_path = tmp_path / "registry.json"
    ws = tmp_path / "gone"
    ws.mkdir()
    _register(VMRegistry(path=reg_path), "vm-running", ws)
    ws.rmdir()

    stopped: list[str] = []
    destroyed: list[str] = []
    p = Pruner(VMRegistry(path=reg_path), console,
               is_running_fn=lambda _: True,
               stop_fn=lambda n: stopped.append(n) or 0,
               destroy_fn=lambda n: destroyed.append(n) or 0)
    p.prune()

    assert stopped == ["vm-running"]
    assert destroyed == ["vm-running"]


def test_prune_mixed_live_and_orphaned(tmp_path):
    reg_path = tmp_path / "registry.json"
    live = tmp_path / "live"
    dead = tmp_path / "dead"
    live.mkdir()
    dead.mkdir()

    reg = VMRegistry(path=reg_path)
    reg.register("vm-live", live)
    reg.register("vm-dead", dead)
    dead.rmdir()

    destroyed: list[str] = []
    p = Pruner(VMRegistry(path=reg_path), console, is_running_fn=_not_running,
               destroy_fn=lambda n: destroyed.append(n) or 0)
    pruned = p.prune()

    assert [e.vm_name for e in pruned] == ["vm-dead"]
    assert destroyed == ["vm-dead"]
    remaining = {e.vm_name for e in VMRegistry(path=reg_path).all()}
    assert remaining == {"vm-live"}
