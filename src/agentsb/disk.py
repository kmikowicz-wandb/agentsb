"""Lima VM disk usage monitoring and resize.

Resize dance (used both for `agentsb resize <vm>` and for draining pending
markers at VM-start time):

  1. Stop the VM (no-op if already stopped).
  2. `truncate` the raw disk image up to the target size on the host. The
     file stays sparse on APFS, so unwritten bytes cost nothing.
  3. Start the VM.
  4. Grow the root partition and filesystem inside the guest with
     `growpart` + `resize2fs`.

The daily check (`agentsb --disk-check`, wired to launchd by the Homebrew
formula) only marks VMs pending; the actual resize runs on next start so
we never yank the rug on a live agent session.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from .errors import AgentsbError
from .vm import LimaVM


# ---- constants ------------------------------------------------------------

THRESHOLD_PCT = 80
GROWTH_FACTOR = 1.5


# ---- helpers --------------------------------------------------------------

def should_resize(usage_pct: int | None, threshold: int = THRESHOLD_PCT) -> bool:
    """Return True if usage_pct indicates resize is needed."""
    return usage_pct is not None and usage_pct >= threshold

from . import AGENTSB_DIR

_LIMA_HOME = Path.home() / ".lima"
_PENDING_DIR = AGENTSB_DIR / "pending-resize"
_QCOW2_MAGIC = b"QFI\xfb"


# ---- paths / sizes --------------------------------------------------------

def _disk_image(vm_name: str) -> Path:
    return _LIMA_HOME / vm_name / "disk"


def _fmt_gib(nbytes: int) -> str:
    return f"{nbytes / (1024**3):.1f} GiB"


def _ensure_raw(path: Path) -> None:
    """Abort if the disk image is qcow2 — truncate would corrupt it."""
    with path.open("rb") as f:
        if f.read(4) == _QCOW2_MAGIC:
            raise AgentsbError(
                f"{path} is qcow2; only raw disk images can be resized by "
                "agentsb. (Lima's vz driver uses raw by default.)"
            )


# ---- pending markers ------------------------------------------------------

def _marker(vm_name: str) -> Path:
    return _PENDING_DIR / vm_name


def mark_pending(vm_name: str, target_bytes: int) -> None:
    _PENDING_DIR.mkdir(parents=True, exist_ok=True)
    _marker(vm_name).write_text(str(target_bytes))


def read_pending(vm_name: str) -> int | None:
    m = _marker(vm_name)
    if not m.exists():
        return None
    try:
        return int(m.read_text().strip())
    except ValueError:
        m.unlink(missing_ok=True)
        return None


def clear_pending(vm_name: str) -> None:
    _marker(vm_name).unlink(missing_ok=True)


# ---- guest-side usage -----------------------------------------------------

def usage_percent(vm: LimaVM) -> int | None:
    """Percent of root filesystem used, or None if the VM isn't running."""
    if vm.status() != "Running":
        return None
    r = subprocess.run(
        ["limactl", "shell", "--workdir", "/", vm.name, "--", "df", "-P", "/"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    # POSIX `df -P /` output:
    #   Filesystem     1024-blocks  Used  Available  Capacity  Mounted on
    #   /dev/vda1        20144580  ...                   7%    /
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    fields = lines[1].split()
    if len(fields) < 5:
        return None
    try:
        return int(fields[4].rstrip("%"))
    except ValueError:
        return None


# ---- resize mechanics -----------------------------------------------------

_GROWFS_SCRIPT = r"""
set -euo pipefail
root_src=$(findmnt -n -o SOURCE /)
parent=$(lsblk -n -o PKNAME "$root_src")
part=${root_src##*[!0-9]}
# growpart returns non-zero when there's nothing to do (partition already
# at end of disk); that's not an error for us. resize2fs is idempotent.
growpart "/dev/$parent" "$part" || true
resize2fs "$root_src"
df -h /
"""


def _do_resize(vm: LimaVM, target_bytes: int, console: Console) -> None:
    disk = _disk_image(vm.name)
    if not disk.exists():
        raise AgentsbError(f"disk image not found: {disk}")
    _ensure_raw(disk)

    current = disk.stat().st_size
    if target_bytes <= current:
        console.print(
            f"[yellow]{vm.name}: target {_fmt_gib(target_bytes)} ≤ "
            f"current {_fmt_gib(current)} — nothing to do.[/yellow]"
        )
        return

    console.print(Panel.fit(
        f"VM       [bold]{vm.name}[/bold]\n"
        f"Current  [bold]{_fmt_gib(current)}[/bold]\n"
        f"Target   [bold]{_fmt_gib(target_bytes)}[/bold]",
        title="[cyan]resize[/cyan]", border_style="cyan",
    ))

    if vm.status() == "Running":
        console.print(f"[cyan]Stopping {vm.name}...[/cyan]")
        vm.stop()

    console.print("[cyan]Growing disk image...[/cyan]")
    subprocess.run(
        ["truncate", "-s", str(target_bytes), str(disk)],
        check=True,
    )

    console.print(f"[cyan]Starting {vm.name}...[/cyan]")
    subprocess.run(["limactl", "start", vm.name], check=True)

    console.print("[cyan]Expanding guest filesystem...[/cyan]")
    rc = vm.exec_script(_GROWFS_SCRIPT, as_root=True)
    if rc != 0:
        raise AgentsbError(
            f"filesystem grow failed (exit {rc}); disk image is "
            f"{_fmt_gib(target_bytes)} but guest fs was not resized."
        )
    console.print(f"[green]{vm.name} resized to {_fmt_gib(target_bytes)}.[/green]")


def resize_cli(vm_name: str, console: Console) -> int:
    """Entry point for `agentsb resize <vm>` — grow by GROWTH_FACTOR now."""
    # Confirm VM exists before we start the dance.
    r = subprocess.run(
        ["limactl", "list", "--format", "{{.Name}}", vm_name],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not r.stdout.strip():
        console.print(f"[red]agentsb:[/red] no such VM: {vm_name}")
        return 1

    vm = LimaVM(vm_name, Path(), Path(), console)
    disk = _disk_image(vm_name)
    if not disk.exists():
        console.print(f"[red]agentsb:[/red] disk image not found: {disk}")
        return 1
    target = int(disk.stat().st_size * GROWTH_FACTOR)
    try:
        _do_resize(vm, target, console)
    except AgentsbError as e:
        console.print(f"[red]agentsb:[/red] {e}")
        return 1
    clear_pending(vm_name)
    return 0


def drain_pending(vm: LimaVM, console: Console) -> None:
    """Run a pending resize before the VM starts. No-op if no marker."""
    target = read_pending(vm.name)
    if target is None:
        return
    try:
        _do_resize(vm, target, console)
    finally:
        clear_pending(vm.name)


# ---- daily check ----------------------------------------------------------

def check_and_mark_all(registry, console: Console) -> None:
    """Iterate registered VMs, mark any >THRESHOLD_PCT for resize-on-next-start.

    Skips stopped VMs (can't `df` without a live guest). They get checked
    the next time they're running during a daily sweep.
    """
    entries = registry.all()
    if not entries:
        console.print("[dim]no registered VMs; nothing to check[/dim]")
        return

    for entry in entries:
        vm = LimaVM(entry.vm_name, Path(), Path(), console)
        if vm.status() != "Running":
            continue
        pct = usage_percent(vm)
        if pct is None:
            continue
        if pct < THRESHOLD_PCT:
            console.print(f"[dim]{entry.vm_name}: {pct}% used — ok[/dim]")
            continue
        disk = _disk_image(entry.vm_name)
        if not disk.exists():
            continue
        current = disk.stat().st_size
        target = int(current * GROWTH_FACTOR)
        mark_pending(entry.vm_name, target)
        console.print(
            f"[yellow]{entry.vm_name}: {pct}% used — marked for resize to "
            f"{_fmt_gib(target)} on next start[/yellow]"
        )
