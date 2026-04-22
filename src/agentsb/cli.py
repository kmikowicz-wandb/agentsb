"""CLI layer: argparse wiring + orchestration of the business-logic layer."""
from __future__ import annotations

import argparse
import atexit
import os
import shutil
import sys
import time
from pathlib import Path
from typing import NoReturn

from rich.console import Console

from .agents import AgentManager, AgentRegistry
from .auth import AuthCoordinator
from .claude_sync import ClaudeConfigSync
from .errors import AgentsbError
from .paths import Paths
from .provision import ProvisionRunner
from .prune import Pruner
from .vm import LimaVM
from .workspace import VMRegistry, WorkspaceResolver


FORWARDED_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "OPENAI_API_KEY",
    "NO_COLOR",
    "TERM",
)


console = Console(stderr=True, highlight=False)


def forwarded_env_pairs() -> list[str]:
    return [f"{k}={os.environ[k]}" for k in FORWARDED_ENV if os.environ.get(k)]


def die(msg: str, code: int = 1) -> NoReturn:
    console.print(f"[red]agentsb:[/red] {msg}")
    sys.exit(code)


def build_parser(agents: list[str]) -> argparse.ArgumentParser:
    agents_line = ", ".join(agents) if agents else "(none configured)"
    p = argparse.ArgumentParser(
        prog="agentsb",
        description=(
            "Run coding agents in an isolated Lima VM. One shared VM with lazy "
            "agent install; each agent's install runs on its first invocation."
        ),
        epilog=f"Agents available: {agents_line}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-w", "--workspace", default=os.getcwd(),
                   help="directory bind-mounted at /workspace (default: $PWD)")
    p.add_argument("--ephemeral", action="store_true",
                   help="throwaway VM, destroyed on exit")
    p.add_argument("--with-claude-config", action="store_true",
                   help="copy safe subset of host ~/.claude/ into the VM")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--shell", action="store_const", dest="mode", const="shell",
                   help="open interactive VM shell at /workspace")
    g.add_argument("--stop", action="store_const", dest="mode", const="stop",
                   help="stop the VM")
    g.add_argument("--reset", action="store_const", dest="mode", const="reset",
                   help="destroy and recreate the VM")
    g.add_argument("--status", action="store_const", dest="mode", const="status",
                   help="show VM status")
    g.add_argument("--prune", action="store_const", dest="mode", const="prune",
                   help="delete registered VMs whose source directory no longer exists")
    p.set_defaults(mode="run")
    return p


def parse(argv: list[str], agents: list[str]) -> tuple[argparse.Namespace, str | None, list[str]]:
    parser = build_parser(agents)
    ns, rest = parser.parse_known_args(argv)

    agent: str | None = None
    agent_idx: int | None = None
    for i, tok in enumerate(rest):
        if tok in agents:
            agent = tok
            agent_idx = i
            break

    if agent_idx is None:
        stray = [t for t in rest if t != "--"]
        if stray:
            die(f"unknown flag or agent: {stray[0]!r} (agents: {', '.join(agents)})")
        return ns, None, []

    before = [t for t in rest[:agent_idx] if t != "--"]
    if before:
        die(f"unknown flag: {before[0]!r}")

    agent_args = rest[agent_idx + 1:]
    if agent_args and agent_args[0] == "--":
        agent_args = agent_args[1:]
    return ns, agent, agent_args


def main() -> int:
    if not shutil.which("limactl"):
        die("limactl not found — install with: brew install lima")

    paths = Paths()
    if not paths.base_template.exists():
        die(f"base template missing: {paths.base_template}")

    registry = AgentRegistry(paths)
    ns, agent, agent_args = parse(sys.argv[1:], registry.list())

    # --prune is a workspace-independent maintenance op; handle it before
    # we require a valid workspace or do any resolver work.
    if ns.mode == "prune":
        Pruner(VMRegistry(), console).prune()
        return 0

    workspace = Path(ns.workspace).expanduser().resolve()
    if not workspace.is_dir():
        die(f"workspace not accessible: {workspace}")

    # Resolve VM name + mount path. Priority:
    #   --ephemeral  → unique throwaway name, workspace-as-mount, no registry.
    #   AGENTSB_VM   → explicit override, workspace-as-mount, no registry.
    #   default      → WorkspaceResolver (inode lookup, ancestor reuse, $HOME guard).
    try:
        if ns.ephemeral:
            vm_name = f"agentsb-eph-{int(time.time())}-{os.getpid()}"
            mount_path = workspace
        elif env_vm := os.environ.get("AGENTSB_VM"):
            vm_name = env_vm
            mount_path = workspace
        else:
            vm_registry = VMRegistry()
            resolver = WorkspaceResolver(vm_registry, console)
            vm_name, mount_path = resolver.resolve(workspace)
    except AgentsbError as e:
        die(str(e))

    # If the mount covers an ancestor, compute the in-VM working directory
    # so the agent starts in the right place.
    if mount_path != workspace:
        rel = workspace.relative_to(mount_path)
        vm_workdir = f"/workspace/{rel}".rstrip("/") or "/workspace"
    else:
        vm_workdir = "/workspace"

    vm = LimaVM(vm_name, paths.base_template, mount_path, console)
    runner = ProvisionRunner(vm, console)
    manager = AgentManager(registry, vm, runner, console)
    auth = AuthCoordinator(vm, console)
    claude_sync = ClaudeConfigSync(vm, console)

    if ns.ephemeral:
        atexit.register(vm.destroy)

    try:
        if ns.mode == "run":
            if agent is None:
                die("AGENT required. Run `agentsb --help` for usage.", code=2)
            vm.ensure_running()
            manager.ensure_installed(agent)
            auth.ensure_authed(agent, registry.fragment(agent))
            if ns.with_claude_config:
                claude_sync.sync()
            vm.launch(
                [agent, *agent_args],
                workdir=vm_workdir,
                env=forwarded_env_pairs(),
            )
        elif ns.mode == "shell":
            vm.ensure_running()
            if ns.with_claude_config:
                claude_sync.sync()
            vm.launch(workdir=vm_workdir)
        elif ns.mode == "stop":
            vm.stop()
        elif ns.mode == "reset":
            vm.destroy()
            vm.ensure_running()
        elif ns.mode == "status":
            print(vm.status() or "(no VM)")
    except AgentsbError as e:
        die(str(e))

    return 0
