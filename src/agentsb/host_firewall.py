"""Host-side pf egress rules for Lima VMs (macOS only).

Rules match packets sourced from Apple's vmnet shared subnet
(192.168.64.0/24, used by Lima's vzNAT driver) before NAT, so code
running inside the VM cannot modify or bypass them regardless of what
privileges it holds inside the guest.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from rich.console import Console

_ANCHOR = "agentsb"
_ANCHOR_DEST = Path("/etc/pf.anchors/agentsb")
_PF_CONF = Path("/etc/pf.conf")


def _rules_src() -> Path:
    from .paths import Paths
    return Paths().lima_dir / "pf-anchor.conf"


def is_installed() -> bool:
    """Non-root check: anchor file present and matches the current template.

    Returning False when the template has drifted forces a reinstall — the
    alternative (content-blind existence check) would silently leave stale
    rules loaded after we ship a new pf-anchor.conf.
    """
    if not _ANCHOR_DEST.exists():
        return False
    try:
        return _ANCHOR_DEST.read_bytes() == _rules_src().read_bytes()
    except (OSError, PermissionError):
        return True


def install(console: Console) -> None:
    """Install pf anchor and hook it into /etc/pf.conf. Requires sudo."""
    src = _rules_src()
    if not src.exists():
        console.print(f"[red]pf rules template not found: {src}[/red]")
        sys.exit(1)

    # Install the pf anchor rules file.
    console.print("[cyan]Installing pf anchor rules...[/cyan]")
    subprocess.run(["sudo", "mkdir", "-p", "/etc/pf.anchors"], check=True)
    subprocess.run(["sudo", "cp", str(src), str(_ANCHOR_DEST)], check=True)
    subprocess.run(["sudo", "chmod", "644", str(_ANCHOR_DEST)], check=True)

    # Add anchor reference to /etc/pf.conf (idempotent).
    pf_conf = subprocess.run(
        ["sudo", "cat", str(_PF_CONF)],
        capture_output=True, text=True, check=True,
    ).stdout
    patched = f'anchor "{_ANCHOR}"' not in pf_conf
    if patched:
        patch = (
            f'\n# agentsb Lima VM egress filtering\n'
            f'anchor "{_ANCHOR}"\n'
            f'load anchor "{_ANCHOR}" from "{_ANCHOR_DEST}"\n'
        )
        subprocess.run(
            ["sudo", "tee", "-a", str(_PF_CONF)],
            input=patch, text=True, check=True, capture_output=True,
        )

    # Enable pf if not already. `pfctl -e` exits 1 when pf is already on
    # (normal state on modern macOS), so we tolerate non-zero here.
    subprocess.run(["sudo", "pfctl", "-e"], capture_output=True)

    # Reload the main ruleset only when we just modified /etc/pf.conf —
    # avoids the "could result in flushing" warning on every invocation.
    if patched:
        console.print("[cyan]Reloading /etc/pf.conf...[/cyan]")
        subprocess.run(
            ["sudo", "pfctl", "-f", str(_PF_CONF)],
            check=True, capture_output=True,
        )

    # Load rules into our anchor. Scoped to the anchor so the main ruleset
    # is untouched; safe to run every time and picks up pf-anchor.conf
    # edits without a full reload.
    console.print("[cyan]Loading agentsb pf anchor...[/cyan]")
    subprocess.run(
        ["sudo", "pfctl", "-a", _ANCHOR, "-f", str(_ANCHOR_DEST)],
        check=True, capture_output=True,
    )
    console.print("[green]Host firewall installed.[/green]")


def ensure_firewall(console: Console) -> None:
    """Install or refresh host-side pf rules."""
    if not is_installed():
        install(console)
