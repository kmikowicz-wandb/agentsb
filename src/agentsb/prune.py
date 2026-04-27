"""Pruner — delete VMs whose source workspace no longer exists on disk."""
from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from rich.console import Console

from .workspace import VMRegistry, VMRegistryEntry


def _limactl_stop(vm_name: str) -> int:
    r = subprocess.run(
        ["limactl", "stop", vm_name],
        capture_output=True,
    )
    return r.returncode


def _limactl_delete(vm_name: str) -> int:
    r = subprocess.run(
        ["limactl", "delete", "-f", vm_name],
        capture_output=True,
    )
    return r.returncode


def _limactl_is_running(vm_name: str) -> bool:
    r = subprocess.run(
        ["limactl", "list", "--format", "{{.Status}}", vm_name],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return False
    return r.stdout.strip().splitlines()[0] == "Running"


def orphan_reason(entry: VMRegistryEntry) -> str | None:
    """Return why this entry is orphaned, or None if it's still live.

    Two kinds of orphan:
      - "directory missing" — the recorded workspace path no longer exists.
      - "inode mismatch"    — the path exists but now resolves to a
        different inode, meaning the original directory was deleted and
        something else took its place (e.g. `rm -rf proj && mkdir proj`).
    """
    try:
        st = Path(entry.workspace_path).stat()
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return "directory missing"
    if st.st_dev != entry.dev or st.st_ino != entry.inode:
        return "inode mismatch (directory replaced)"
    return None


class Pruner:
    """Removes registry entries whose source directory is gone on disk.

    For each orphan, the corresponding Lima VM is destroyed (`limactl
    delete -f`) and the registry entry is removed. VMs whose workspaces
    still exist are left alone, even if the VM itself is in an unusual
    state — use `agentsb --reset` to force-recreate those.
    """

    def __init__(
        self,
        registry: VMRegistry,
        console: Console,
        *,
        is_running_fn: Callable[[str], bool] = _limactl_is_running,
        stop_fn: Callable[[str], int] = _limactl_stop,
        destroy_fn: Callable[[str], int] = _limactl_delete,
    ) -> None:
        self._registry = registry
        self._console = console
        self._is_running = is_running_fn
        self._stop = stop_fn
        self._destroy = destroy_fn

    def prune(self) -> list[VMRegistryEntry]:
        entries = self._registry.all()
        pruned: list[VMRegistryEntry] = []

        for e in entries:
            reason = orphan_reason(e)
            if reason is None:
                continue
            self._console.print(
                f"[yellow]→ pruning[/yellow] [bold]{e.vm_name}[/bold]  "
                f"[dim]{e.workspace_path}[/dim]  [red]({reason})[/red]"
            )
            if self._is_running(e.vm_name):
                self._console.print(f"  [cyan]stopping {e.vm_name}...[/cyan]")
                self._stop(e.vm_name)
            rc = self._destroy(e.vm_name)
            if rc != 0:
                self._console.print(
                    f"  [red]limactl delete exited {rc}; "
                    f"keeping registry entry for investigation[/red]"
                )
                continue
            self._registry.unregister(e.vm_name)
            pruned.append(e)

        if not entries:
            self._console.print("[dim]no registered VMs[/dim]")
        elif not pruned:
            self._console.print("[green]✓[/green] nothing to prune")
        else:
            self._console.print(
                f"[green]✓[/green] pruned {len(pruned)} of {len(entries)} VM(s)"
            )
        return pruned
