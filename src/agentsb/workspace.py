"""Workspace-identity → VM resolution.

This module decides *which* Lima VM should handle a given working
directory. It's the layer that makes VMs workspace-scoped instead of
global, with three behaviors beyond a simple hash:

  1. **Inode identity** — a workspace is keyed on `(st_dev, st_ino)`
     after resolving symlinks, so `~/proj`, `/Users/me/proj`, and any
     symlink pointing at the same directory map to the same VM.
  2. **Ancestor reuse** — if an ancestor directory already has a live
     registered VM, the user is prompted whether to reuse it (default
     yes). On reuse, the VM's existing mount covers the current dir;
     the caller derives a `vm_workdir` to cd into the right subdir.
  3. **$HOME guard** — workspaces outside the user's home directory
     trigger a red warning panel and a confirmation prompt. Non-TTY
     invocations are refused outright.

State (which VMs exist for which inodes) is persisted on the host at
`~/.agentsb/registry.json`. Registry entries pointing at VMs that no
longer exist (manually `limactl delete`'d, say) are treated as absent.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from .errors import AgentsbError


REGISTRY_DEFAULT = Path.home() / ".agentsb" / "registry.json"


# ============================================================================
# Registry data
# ============================================================================

@dataclass
class VMRegistryEntry:
    """One entry in the host-side VM registry."""
    vm_name: str
    workspace_path: str
    dev: int
    inode: int
    created_at: str


class VMRegistry:
    """Persistent mapping from workspace identity → VM name.

    Identity is `(dev, inode)`, not a string path — this is why
    symlinks and renames don't split a single directory into two VMs.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or REGISTRY_DEFAULT

    def _load(self) -> list[VMRegistryEntry]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text())
        except json.JSONDecodeError:
            return []
        return [VMRegistryEntry(**e) for e in data.get("vms", [])]

    def _save(self, entries: list[VMRegistryEntry]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"vms": [asdict(e) for e in entries]}
        self._path.write_text(json.dumps(payload, indent=2) + "\n")

    def all(self) -> list[VMRegistryEntry]:
        return self._load()

    def find_by_inode(self, dev: int, inode: int) -> VMRegistryEntry | None:
        for e in self._load():
            if e.dev == dev and e.inode == inode:
                return e
        return None

    def register(self, vm_name: str, workspace: Path) -> VMRegistryEntry:
        st = workspace.stat()
        entry = VMRegistryEntry(
            vm_name=vm_name,
            workspace_path=str(workspace),
            dev=st.st_dev,
            inode=st.st_ino,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        # Replace any existing entry with the same vm_name.
        entries = [e for e in self._load() if e.vm_name != vm_name]
        entries.append(entry)
        self._save(entries)
        return entry

    def unregister(self, vm_name: str) -> None:
        entries = [e for e in self._load() if e.vm_name != vm_name]
        self._save(entries)


# ============================================================================
# Helpers
# ============================================================================

def _lima_vm_exists(vm_name: str) -> bool:
    """True iff `limactl list` currently knows about this instance name."""
    r = subprocess.run(
        ["limactl", "list", "--format", "{{.Name}}", vm_name],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return False
    return vm_name in {line.strip() for line in r.stdout.splitlines() if line.strip()}


def _sanitize(name: str) -> str:
    """Lima VM names must match `[a-z0-9][a-z0-9-]*[a-z0-9]`."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s[:20] or "ws")


def _short_hash(dev: int, inode: int) -> str:
    return hashlib.sha1(f"{dev}:{inode}".encode()).hexdigest()[:8]


def generate_vm_name(workspace: Path) -> str:
    st = workspace.stat()
    return f"agentsb-{_sanitize(workspace.name)}-{_short_hash(st.st_dev, st.st_ino)}"


# ============================================================================
# WorkspaceResolver
# ============================================================================

class WorkspaceResolver:
    """Maps a workspace directory to `(vm_name, mount_path)`.

    The caller should pass `mount_path` as the VM's workspace mount. If
    `mount_path != workspace`, an ancestor is being reused; the caller
    should compute `vm_workdir = /workspace/<workspace.relative_to(mount_path)>`.
    """

    def __init__(
        self,
        registry: VMRegistry,
        console: Console,
        *,
        vm_exists: Callable[[str], bool] = _lima_vm_exists,
    ) -> None:
        self._registry = registry
        self._console = console
        self._vm_exists = vm_exists

    def resolve(self, workspace: Path) -> tuple[str, Path]:
        workspace = workspace.resolve()

        if not self._is_under_home(workspace):
            self._guard_outside_home(workspace)  # raises if declined

        st = workspace.stat()

        # 1. Exact match
        exact = self._registry.find_by_inode(st.st_dev, st.st_ino)
        if exact is not None and self._vm_exists(exact.vm_name):
            return exact.vm_name, Path(exact.workspace_path)

        # 2. Ancestor match
        ancestor = self._find_live_ancestor(workspace)
        if ancestor is not None and self._confirm_reuse_ancestor(workspace, ancestor):
            return ancestor.vm_name, Path(ancestor.workspace_path)

        # 3. New VM
        vm_name = generate_vm_name(workspace)
        self._registry.register(vm_name, workspace)
        return vm_name, workspace

    # ---- checks -----------------------------------------------------

    @staticmethod
    def _is_under_home(workspace: Path) -> bool:
        try:
            return workspace.is_relative_to(Path.home())
        except ValueError:
            return False

    def _find_live_ancestor(self, workspace: Path) -> VMRegistryEntry | None:
        for parent in workspace.parents:
            try:
                st = parent.stat()
            except (FileNotFoundError, PermissionError, OSError):
                continue
            entry = self._registry.find_by_inode(st.st_dev, st.st_ino)
            if entry is None:
                continue
            if self._vm_exists(entry.vm_name):
                return entry
        return None

    # ---- prompts ----------------------------------------------------

    def _guard_outside_home(self, workspace: Path) -> None:
        self._console.print(Panel.fit(
            f"[bold red]Workspace is outside your home directory.[/bold red]\n\n"
            f"Workspace: [yellow]{workspace}[/yellow]\n"
            f"Home:      [yellow]{Path.home()}[/yellow]\n\n"
            f"agentsb is designed for project directories under $HOME.\n"
            f"Mounting system paths ([yellow]/[/yellow], "
            f"[yellow]/etc[/yellow], [yellow]/tmp[/yellow], "
            f"[yellow]/Volumes[/yellow], …) is usually a\n"
            f"mistake and may expose files the agent shouldn't see.",
            title="[red on white]  ⚠  OUTSIDE $HOME  ⚠  [/red on white]",
            border_style="red",
        ))
        if not sys.stdin.isatty():
            raise AgentsbError(
                "workspace is outside $HOME; refusing in non-interactive shell."
            )
        answer = self._console.input(
            "[red]Continue anyway? [y/N] [/red]"
        ).strip().lower()
        if answer not in ("y", "yes"):
            raise AgentsbError("aborted (workspace outside $HOME)")

    def _confirm_reuse_ancestor(
        self, workspace: Path, entry: VMRegistryEntry
    ) -> bool:
        self._console.print(Panel(
            f"A VM already covers an ancestor of this directory.\n\n"
            f"Workspace:  [yellow]{workspace}[/yellow]\n"
            f"Ancestor:   [yellow]{entry.workspace_path}[/yellow]\n"
            f"VM:         [bold]{entry.vm_name}[/bold]\n\n"
            f"Reusing keeps you on the same VM as the ancestor — same\n"
            f"auth state, same caches, same other agents. The ancestor's\n"
            f"mount already covers your current directory.\n\n"
            f"Decline to get a fresh, isolated VM for this exact directory.",
            title="[cyan]Reuse ancestor VM?[/cyan]",
            border_style="cyan",
        ))
        if not sys.stdin.isatty():
            self._console.print("[dim]Non-interactive — defaulting to reuse.[/dim]")
            return True
        answer = self._console.input(
            "[cyan]Reuse existing VM? [Y/n] [/cyan]"
        ).strip().lower()
        return answer not in ("n", "no")
