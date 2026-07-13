"""CLI layer: argparse wiring + orchestration of the business-logic layer."""
from __future__ import annotations

import argparse
import atexit
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn

from rich.console import Console

from .agents import AgentManager, AgentRegistry
from .auth import AuthCoordinator
from .claude_sync import ClaudeConfigSync
from .completion import install as install_completion
from .disk import check_and_mark_all, drain_pending, resize_cli
from .errors import AgentsbError
from .paths import Paths
from .provision import ProvisionRunner
from .prune import Pruner
from .vm import LimaVM
from .workspace import VMRegistry, WorkspaceResolver


SUBCOMMANDS = frozenset({"prune", "shell", "stop", "reset", "status", "disk-check"})

FORWARDED_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "OPENAI_API_KEY",
    "NO_COLOR",
    "TERM",
    "TERM_PROGRAM",         # lets agents detect terminal capabilities (e.g. Shift+Enter)
    "TERM_PROGRAM_VERSION",
    "COLORTERM",
)


console = Console(stderr=True, highlight=False)


def _claude_oauth_token() -> str | None:
    """Read Claude OAuth access token from host ~/.claude/.credentials.json."""
    creds = Path.home() / ".claude" / ".credentials.json"
    if not creds.exists():
        return None
    try:
        data = json.loads(creds.read_text())
        token = (data.get("claudeAiOauth") or {}).get("accessToken")
        return str(token) if token else None
    except Exception:
        return None


def forwarded_env_pairs() -> list[str]:
    pairs = [f"{k}={os.environ[k]}" for k in FORWARDED_ENV if os.environ.get(k)]
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        token = _claude_oauth_token()
        if token:
            pairs.append(f"CLAUDE_CODE_OAUTH_TOKEN={token}")
    return pairs


def die(msg: str, code: int = 1) -> NoReturn:
    console.print(f"[red]agentsb:[/red] {msg}")
    sys.exit(code)


def build_parser(agents: list[str]) -> argparse.ArgumentParser:
    agents_line = ", ".join(agents) if agents else "(none configured)"
    p = argparse.ArgumentParser(
        prog="agentsb",
        usage="agentsb [options] COMMAND | AGENT [agent-args]",
        description=(
            "Run coding agents in an isolated Lima VM. One shared VM with lazy "
            "agent install; each agent's install runs on its first invocation."
        ),
        epilog=(
            "Commands:\n"
            "  prune        delete registered VMs whose source directory no longer exists\n"
            "  shell        open an interactive VM shell at /workspace\n"
            "  stop         stop the VM\n"
            "  reset        destroy and recreate the VM\n"
            "  status       show VM status\n"
            "  disk-check   mark VMs over 80%% disk usage for resize on next start\n"
            "  resize VM    resize a VM's disk\n"
            "\n"
            f"Agents available: {agents_line}"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-w", "--workspace", default=os.getcwd(),
                   help="directory bind-mounted at /workspace (default: $PWD)")
    p.add_argument("--ephemeral", action="store_true",
                   help="throwaway VM, destroyed on exit")
    p.add_argument("--auto", action="store_true",
                   help="run agent in fully-automatic mode (skip all permission prompts)")
    p.add_argument("--with-claude-config", action="store_true",
                   help="copy safe subset of host ~/.claude/ into the VM")
    p.add_argument("--install-completion", dest="install_completion",
                   nargs="?", const="auto", default=None, metavar="SHELL",
                   help="install shell completion for bash/zsh/fish "
                        "(auto-detects from $SHELL when SHELL omitted)")
    return p


def parse(
    argv: list[str], agents: list[str],
) -> tuple[argparse.Namespace, str | None, str | None, list[str]]:
    """Return (namespace, subcommand_or_None, agent_or_None, agent_args)."""
    parser = build_parser(agents)
    ns, rest = parser.parse_known_args(argv)

    # Drop bare '--' separators from the front; keep them in agent_args.
    positionals = [t for t in rest if t != "--"]
    if not positionals:
        return ns, None, None, []

    first = positionals[0]

    if first in SUBCOMMANDS:
        stray = positionals[1:]
        if stray:
            die(f"unexpected arguments after '{first}': {stray[0]!r}")
        return ns, first, None, []

    if first in agents:
        # Everything after the agent name (and an optional '--') is passed through.
        idx = rest.index(first)
        agent_args = rest[idx + 1:]
        if agent_args and agent_args[0] == "--":
            agent_args = agent_args[1:]
        return ns, None, first, agent_args

    if first.startswith("-"):
        die(f"unknown option: {first!r}")
    else:
        all_cmds = ", ".join(sorted(SUBCOMMANDS))
        die(f"unknown command or agent: {first!r} "
            f"(commands: {all_cmds}; agents: {', '.join(agents)})")


def main() -> int:
    if not shutil.which("limactl"):
        die("limactl not found — install with: brew install lima")

    # `agentsb resize <vm>` — positional subcommand dispatched before the
    # main flag parser, which doesn't know about it.
    if len(sys.argv) >= 2 and sys.argv[1] == "resize":
        if len(sys.argv) != 3:
            die("usage: agentsb resize <vm-name>")
        return resize_cli(sys.argv[2], console)

    paths = Paths()
    if not paths.base_template.exists():
        die(f"base template missing: {paths.base_template}")

    registry = AgentRegistry(paths)
    ns, subcommand, agent, agent_args = parse(sys.argv[1:], registry.list())

    # Workspace-independent maintenance ops: handle before we require a
    # valid workspace or do any resolver work.
    if ns.install_completion is not None:
        return install_completion(ns.install_completion, console)
    if subcommand == "prune":
        Pruner(VMRegistry(), console).prune()
        return 0
    if subcommand == "disk-check":
        check_and_mark_all(VMRegistry(), console)
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

    mount_type = os.environ.get("AGENTSB_MOUNT_TYPE", "virtiofs")
    vm = LimaVM(vm_name, paths.base_template, mount_path, console, mount_type=mount_type)
    runner = ProvisionRunner(vm, console)
    manager = AgentManager(registry, vm, runner, console)
    auth = AuthCoordinator(vm, console)
    claude_sync = ClaudeConfigSync(vm, console)

    if ns.ephemeral:
        atexit.register(vm.destroy)

    try:
        if agent is not None:
            drain_pending(vm, console)
            vm.ensure_running()
            subprocess.run([str(Paths().lima_dir / "host_firewall.sh")], check=True)
            manager.ensure_installed(agent)
            auth.ensure_authed(agent, registry.fragment(agent))
            if agent == "claude":
                claude_sync.sync_credentials()
            if ns.with_claude_config:
                claude_sync.sync()
            if ns.auto:
                auto_cfg = registry.auto_config(agent)
                auto_flags: list[str] = auto_cfg.get("flags", [])
                auto_env: list[str] = auto_cfg.get("env", [])
            else:
                auto_flags, auto_env = [], []
            vm.launch(
                [agent, *auto_flags, *agent_args],
                workdir=vm_workdir,
                env=forwarded_env_pairs() + auto_env,
            )
        elif subcommand == "shell":
            drain_pending(vm, console)
            vm.ensure_running()
            subprocess.run([str(Paths().lima_dir / "host_firewall.sh")], check=True)
            if ns.with_claude_config:
                claude_sync.sync()
            vm.launch(workdir=vm_workdir)
        elif subcommand == "stop":
            vm.stop()
        elif subcommand == "reset":
            vm.destroy()
            vm.ensure_running()
        elif subcommand == "status":
            print(vm.status() or "(no VM)")
        else:
            die("COMMAND or AGENT required. Run `agentsb --help` for usage.", code=2)
    except AgentsbError as e:
        die(str(e))

    return 0
