"""AuthCoordinator — run an agent's login flow before launching it."""
from __future__ import annotations

from pathlib import Path

import yaml
from rich.console import Console
from rich.panel import Panel

from .errors import AgentsbError
from .vm import LimaVM


class AuthCoordinator:
    """Runs an agent's auth check + interactive login if needed.

    Agents that need authentication declare it in their fragment YAML:

        auth:
          check: <shell predicate that exits 0 iff already authed>
          login: <command that drives the interactive login>

    `ensure_authed(fragment)`:
      1. Reads the `auth:` block from the fragment. If absent, returns
         immediately — agents that auth via env var (aider, forge) or
         don't need auth at all are skipped.
      2. Runs the `check` script in the VM, silently. If it exits 0 the
         agent is authed; return.
      3. Otherwise prints a panel explaining what's about to happen, then
         runs the `login` command with the user's TTY attached so they
         can complete the flow (paste OAuth URL, enter code, etc).
      4. Re-runs `check`. If it still fails, raises AgentsbError so the
         caller can decline to launch the agent (rather than launching
         into an immediate exit).

    The login command runs inside the VM, not on the host. Credentials
    land in the VM's filesystem and stay isolated.
    """

    def __init__(self, vm: LimaVM, console: Console) -> None:
        self._vm = vm
        self._console = console

    def ensure_authed(self, agent: str, fragment: Path) -> None:
        auth = self._read_auth(fragment)
        if auth is None:
            return
        check, login = auth

        if self._vm.exec_script(check, as_root=False, silent=True) == 0:
            return

        self._console.print(Panel.fit(
            f"[bold]{agent}[/bold] needs to be authenticated before it can run.\n\n"
            f"agentsb will now run the agent's login flow:\n"
            f"  [cyan]{login.splitlines()[0]}[/cyan]\n\n"
            f"Follow its prompts (paste a code, complete a browser flow, etc).\n"
            f"When it finishes, your [bold]agentsb {agent}[/bold] invocation\n"
            f"will continue automatically.",
            title="[yellow]Agent authentication[/yellow]",
            border_style="yellow",
        ))

        rc = self._vm.run_interactive(login)
        if rc != 0:
            raise AgentsbError(
                f"{agent}: login command exited with status {rc}"
            )

        if self._vm.exec_script(check, as_root=False, silent=True) != 0:
            raise AgentsbError(
                f"{agent}: auth check still fails after login — see the agent's "
                f"output above for what went wrong."
            )

        self._console.print(f"[green]✓[/green] {agent} authenticated")

    @staticmethod
    def _read_auth(fragment: Path) -> tuple[str, str] | None:
        """Extract `(check, login)` from a fragment, or None if missing."""
        data = yaml.safe_load(fragment.read_text()) or {}
        auth = data.get("auth")
        if not isinstance(auth, dict):
            return None
        check = str(auth.get("check") or "").strip()
        login = str(auth.get("login") or "").strip()
        if not check or not login:
            return None
        return check, login
