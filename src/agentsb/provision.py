"""ProvisionRunner — parse a Lima provision fragment, run it in a VM."""
from __future__ import annotations

from pathlib import Path

import yaml
from rich.console import Console

from .errors import AgentsbError
from .vm import LimaVM


class ProvisionRunner:
    """Executes provision blocks from a Lima fragment YAML.

    Each block has `mode` (system|user) and `script`. `system` runs under
    sudo; `user` runs as the default Lima user. Output streams directly —
    no spinners — so apt/npm/curl progress is visible in real time.
    """

    def __init__(self, vm: LimaVM, console: Console) -> None:
        self._vm = vm
        self._console = console

    def run(self, fragment: Path, label: str) -> None:
        data = yaml.safe_load(fragment.read_text()) or {}
        blocks = data.get("provision") or []
        total = len(blocks)
        for idx, block in enumerate(blocks, start=1):
            mode = block.get("mode", "system")
            # YAML may coerce bare tokens (e.g. `false`) to non-strings;
            # force a string then strip.
            script = str(block.get("script") or "").strip()
            if not script:
                continue
            self._console.rule(
                f"[cyan]Provisioning {label} — step {idx}/{total} ({mode})[/cyan]"
            )
            rc = self._vm.exec_script(script, as_root=(mode == "system"))
            if rc != 0:
                raise AgentsbError(
                    f"provision '{label}' failed at step {idx}/{total} (mode={mode})"
                )
