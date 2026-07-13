"""LimaVM — a thin wrapper around `limactl` for one named instance."""
from __future__ import annotations

import os
import shlex
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import NoReturn

from rich.console import Console
from rich.panel import Panel

from .errors import AgentsbError


class LimaVM:
    """Lifecycle + command execution for a single Lima VM by name.

    Knows nothing about agents — that's the layer above. Responsibilities:
      - query/create/start/stop/destroy the VM
      - execute scripts and commands inside it
      - copy files in
    """

    def __init__(
        self,
        name: str,
        template: Path,
        workspace: Path,
        console: Console,
        *,
        mount_type: str = "virtiofs",
    ) -> None:
        self.name = name
        self._template = template
        self._workspace = workspace
        self._console = console
        self._mount_type = mount_type

    # ---- lifecycle ----------------------------------------------------

    def status(self) -> str:
        """Return 'Running' / 'Stopped' / 'Broken' / '' (missing)."""
        r = subprocess.run(
            ["limactl", "list", "--format", "{{.Status}}", self.name],
            capture_output=True, text=True,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return ""
        return r.stdout.strip().splitlines()[0]

    def ensure_running(self) -> None:
        s = self.status()
        if s == "Running":
            return
        if s == "Stopped":
            self._console.rule(f"[cyan]Starting VM {self.name}[/cyan]")
            subprocess.run(["limactl", "start", self.name], check=True)
            return
        if s in ("", "Broken"):
            self._console.print(Panel.fit(
                f"Creating VM  [bold]{self.name}[/bold]\n"
                f"Workspace    [bold]{self._workspace}[/bold]\n"
                f"First boot ~2–4 min. Agents install on first use.",
                title="[cyan]agentsb[/cyan]", border_style="cyan",
            ))
            self._console.rule("[cyan]limactl start (streaming)[/cyan]")
            subprocess.run(
                ["limactl", "start", "--tty=false",
                 "--name", self.name,
                 "--set", f'.param.WORKSPACE = "{self._workspace}"',
                 "--set", f'.mountType = "{self._mount_type}"',
                 str(self._template)],
                check=True,
            )
            return
        raise AgentsbError(f"VM {self.name} in unexpected state: {s}")

    def stop(self) -> None:
        subprocess.run(["limactl", "stop", self.name])

    def destroy(self) -> None:
        subprocess.run(["limactl", "delete", "-f", self.name], capture_output=True)

    # ---- inspection ----------------------------------------------------

    def has_binary(self, name: str) -> bool:
        r = subprocess.run(
            [*self._shell_prefix(), "--",
             "sh", "-c", f"command -v {shlex.quote(name)} >/dev/null"],
            capture_output=True,
        )
        return r.returncode == 0

    @contextmanager
    def running(self):
        """Context manager: ensures VM is running, yields control, leaves running."""
        was_running = self.status() == "Running"
        if not was_running:
            self.ensure_running()
        try:
            yield
        finally:
            # Only stop if we started it (don't interrupt user's running VM)
            if not was_running:
                self.stop()

    # ---- execution -----------------------------------------------------

    def _shell_prefix(self, workdir: str = "/") -> list[str]:
        """`limactl shell` invocation prefix with an explicit workdir.

        Without --workdir, limactl tries to cd into the host's $PWD inside
        the guest, which fails noisily when the host path isn't mounted
        (the host's /Users/... tree doesn't exist in the Ubuntu guest).
        Passing --workdir suppresses that auto-cd.
        """
        return ["limactl", "shell", "--workdir", workdir, self.name]

    def exec_script(self, script: str, *, as_root: bool, silent: bool = False) -> int:
        """Pipe a shell script into bash inside the VM. Returns exit code.

        With silent=False (default) stdout/stderr stream through to the user —
        use for long-running installs so progress is visible. With silent=True
        output is captured and discarded — use for fast, noisy predicate
        checks (e.g. auth_check).
        """
        cmd = [*self._shell_prefix(), "--"]
        cmd += ["sudo", "bash", "-s"] if as_root else ["bash", "-s"]
        r = subprocess.run(
            cmd, input=script, text=True,
            capture_output=silent,
        )
        return r.returncode

    def run_interactive(self, command: str, *, workdir: str = "/workspace") -> int:
        """Run a shell command inside the VM with the user's TTY attached.

        Unlike exec_script (which pipes the script through stdin), this uses
        `bash -c` so the command's own stdin remains the user's terminal.
        Required for interactive flows that prompt the user — OAuth code
        paste, password entry, browser-completed login, etc.
        """
        cmd = [*self._shell_prefix(workdir), "--", "bash", "-c", command]
        return subprocess.run(cmd).returncode

    def mkdir(self, path: str) -> None:
        subprocess.run(
            [*self._shell_prefix(), "--", "mkdir", "-p", path],
            check=True, capture_output=True,
        )

    def copy_in(self, src: Path, dest: str) -> None:
        cmd = ["limactl", "copy"]
        if src.is_dir():
            cmd.append("-r")
        cmd += [str(src), f"{self.name}:{dest}"]
        subprocess.run(cmd, check=True, capture_output=True)

    def launch(
        self,
        argv: list[str] | None = None,
        *,
        workdir: str = "/workspace",
        env: list[str] | None = None,
    ) -> NoReturn:
        """Replace the current process with limactl shell.

        `argv=None` opens an interactive shell. `env` is a list of K=V
        strings injected via limactl shell's `env` prefix.
        """
        cmd = ["limactl", "shell", "--workdir", workdir, self.name]
        if env:
            cmd += ["env", *env]
        if argv:
            cmd += argv
        os.execvp(cmd[0], cmd)
