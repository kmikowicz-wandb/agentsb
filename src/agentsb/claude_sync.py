"""ClaudeConfigSync — copy a safe subset of host ~/.claude/ into the VM."""
from __future__ import annotations

from pathlib import Path

from rich.console import Console

from .vm import LimaVM


class ClaudeConfigSync:
    """Copies host Claude customizations into the VM without touching secrets.

    **Copied:** `CLAUDE.md`, `commands/`, `agents/`, `plugins/`, `settings.json`.

    **Excluded:** `config.json`, `.credentials.json`, `projects/` (per-project
    memory and history), `backups/`, `cache/`, `debug/`, everything else —
    anything that could carry auth state or project-specific content.
    """

    ITEMS = ("CLAUDE.md", "commands", "agents", "plugins", "settings.json")

    def __init__(self, vm: LimaVM, console: Console) -> None:
        self._vm = vm
        self._console = console

    def sync_credentials(self) -> None:
        """Copy host ~/.claude/.credentials.json into the VM for claude.ai OAuth auth."""
        src = Path.home() / ".claude" / ".credentials.json"
        if not src.exists():
            return
        self._vm.mkdir(".claude")
        self._vm.copy_in(src, ".claude/")

    def sync(self) -> None:
        src_root = Path.home() / ".claude"
        if not src_root.is_dir():
            return
        present = [src_root / i for i in self.ITEMS if (src_root / i).exists()]
        if not present:
            return
        self._vm.mkdir(".claude")
        self._console.print("[cyan]Syncing ~/.claude subset into VM…[/cyan]")
        for src in present:
            self._vm.copy_in(src, ".claude/")
        self._console.print("[green]✓[/green] ~/.claude subset synced")
