"""Shell completion install for bash / zsh / fish.

The canonical completion scripts live in `<repo>/completions/`. This module
copies the right one into a per-user location when the user runs
`agentsb --install-completion`. Homebrew installs them into its own
shared completion dirs via the formula; this command is for everyone else
(dev checkouts, non-Homebrew installs).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from rich.console import Console

from .paths import Paths


_FILE_FOR_SHELL = {
    "bash": "agentsb.bash",
    "zsh": "_agentsb",
    "fish": "agentsb.fish",
}


def _detect_shell() -> str | None:
    s = os.environ.get("SHELL") or ""
    for shell in _FILE_FOR_SHELL:
        if s.endswith("/" + shell):
            return shell
    return None


def _target(shell: str) -> tuple[Path, str | None]:
    """Where to install + a post-install hint (or None)."""
    home = Path.home()
    if shell == "zsh":
        return (
            home / ".zfunc" / "_agentsb",
            "Ensure ~/.zfunc is in fpath. Add to ~/.zshrc if needed:\n"
            "  fpath=(~/.zfunc $fpath)\n"
            "  autoload -U compinit && compinit",
        )
    if shell == "bash":
        return (
            home / ".bash_completion.d" / "agentsb",
            "Add to ~/.bashrc (once) if not already sourcing the dir:\n"
            "  for f in ~/.bash_completion.d/*; do . \"$f\"; done",
        )
    if shell == "fish":
        # fish auto-loads ~/.config/fish/completions/ — no rc edit needed.
        return home / ".config" / "fish" / "completions" / "agentsb.fish", None
    raise ValueError(f"unsupported shell: {shell}")


def install(shell: str, console: Console) -> int:
    """Install the completion script for `shell`. Returns a CLI exit code.

    `shell` is either 'auto' (detect from $SHELL) or one of bash/zsh/fish.
    """
    if shell == "auto":
        detected = _detect_shell()
        if detected is None:
            console.print(
                "[red]agentsb:[/red] could not detect shell from $SHELL — "
                "pass one of: bash, zsh, fish"
            )
            return 1
        shell = detected

    filename = _FILE_FOR_SHELL.get(shell)
    if filename is None:
        console.print(f"[red]agentsb:[/red] unsupported shell: {shell}")
        return 1

    src = Paths().completions_dir / filename
    if not src.exists():
        console.print(f"[red]agentsb:[/red] completion file missing from install: {src}")
        return 1

    target, hint = _target(shell)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, target)
    console.print(f"[green]Installed {shell} completion:[/green] {target}")
    if hint:
        console.print(f"\n[dim]{hint}[/dim]")
    console.print("\n[dim]Open a new shell (or re-source your rc) to activate.[/dim]")
    return 0
