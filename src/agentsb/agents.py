"""AgentRegistry + AgentManager — discovery and lazy installation of agents."""
from __future__ import annotations

from pathlib import Path

import yaml
from rich.console import Console

from .paths import Paths
from .provision import ProvisionRunner
from .vm import LimaVM


class AgentRegistry:
    """Discovers which agent fragments exist on disk.

    An agent exists iff `lima/agents/<name>.yaml` exists. Discovery is
    re-done on every call, so dropping in a new fragment takes effect on
    the next invocation — no restart required.
    """

    def __init__(self, paths: Paths) -> None:
        self._paths = paths

    def list(self) -> list[str]:
        if not self._paths.agents_dir.exists():
            return []
        return sorted(p.stem for p in self._paths.agents_dir.glob("*.yaml"))

    def has(self, name: str) -> bool:
        return self._paths.agent_fragment(name).is_file()

    def fragment(self, name: str) -> Path:
        if not self.has(name):
            raise KeyError(f"no fragment for agent: {name}")
        return self._paths.agent_fragment(name)

    def auto_config(self, name: str) -> dict:
        """Return the auto: block from an agent fragment, or {}.

        auto:
          flags: [...]   # prepended to agent argv
          env:   [...]   # K=V pairs added to the launch environment
        """
        data = yaml.safe_load(self.fragment(name).read_text()) or {}
        return data.get("auto") or {}


class AgentManager:
    """Coordinates lazy installation of agents into a Lima VM.

    ### Lazy-load contract

    1. An **agent fragment** at `lima/agents/<name>.yaml` declares how to
       install one agent. It's a provision-only YAML (system- and/or
       user-mode shell scripts). Fragments know nothing about the VM
       template; the VM template knows nothing about any agent.

    2. The base VM (`lima/base.yaml`) is provisioned once and contains
       only shared tooling (node, uv, firewall, common Unix utilities).
       No agent binaries are baked in.

    3. An agent is considered *installed* in a VM iff the command name is
       resolvable by `command -v <name>` inside that VM. This is
       stateless — the VM is the source of truth, so there's no external
       "installed agents" manifest that can drift.

    4. `ensure_installed(name)`:
          a. Check `vm.has_binary(name)`.
          b. If present → no-op, return immediately.
          c. If absent → load the fragment; `ProvisionRunner` executes
             each block sequentially in the live VM. Output streams so
             the user sees apt/npm/curl progress in real time.

    5. Install is **in-place**. The VM is not reset. Other agents, auth
       tokens, workspace edits, and caches are preserved.

    6. On provision failure, an `AgentsbError` bubbles up. Retry is safe
       because fragments are expected to be idempotent (`apt install`,
       `npm install -g`, `curl | sh` installers — all safe to re-run).
    """

    def __init__(
        self,
        registry: AgentRegistry,
        vm: LimaVM,
        runner: ProvisionRunner,
        console: Console,
    ) -> None:
        self._registry = registry
        self._vm = vm
        self._runner = runner
        self._console = console

    def is_installed(self, name: str) -> bool:
        return self._vm.has_binary(name)

    def ensure_installed(self, name: str) -> None:
        if self.is_installed(name):
            return
        fragment = self._registry.fragment(name)   # raises KeyError if unknown
        self._console.print(
            f"[cyan]Installing [bold]{name}[/bold] into VM (first use)…[/cyan]"
        )
        self._runner.run(fragment, label=name)
        self._console.print(f"[green]✓[/green] {name} installed")
