"""Sync host Aider configuration into the VM without touching secrets."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from .vm import LimaVM


class AiderConfigSync:
    """Copies host Aider configuration into the VM.

    Aider reads `.aider.conf.yml` from the current working directory or the
    user's home directory. We copy the host's home-directory config (if any)
    into `/workspace` so it is picked up when Aider runs inside the VM.

    Only the explicit config file is copied; credential-bearing files such as
    `.env` or `.aider.model.settings.yml` are intentionally excluded.
    """

    CONFIG_NAME = ".aider.conf.yml"
    VM_DEST = "/workspace/.aider.conf.yml"

    def __init__(self, vm: LimaVM, console: Console) -> None:
        self._vm = vm
        self._console = console

    def sync(self) -> None:
        source = Path.home() / self.CONFIG_NAME
        if not source.exists():
            return

        self._console.print(f"[cyan]Copying Aider config from {source} into VM…[/cyan]")
        self._vm.copy_in(source, self.VM_DEST)
        self._console.print("[green]✓[/green] Aider config synced")
