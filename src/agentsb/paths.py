"""Filesystem layout resolution for both dev checkout and installed tree."""
from __future__ import annotations

import os
from pathlib import Path


class Paths:
    """Resolves the on-disk install layout.

    Resolution order:
      1. `home` argument (for tests and explicit overrides).
      2. `AGENTSB_HOME` env var (Homebrew's bin/ shim sets this to libexec).
      3. Two levels above this module file — the dev checkout / repo root.

    From there, `lima/`, `lima/agents/`, and `lima/base.yaml` are derived.
    """

    def __init__(self, home: Path | None = None) -> None:
        if home is not None:
            resolved = Path(home)
        elif env_home := os.environ.get("AGENTSB_HOME"):
            resolved = Path(env_home)
        else:
            # src/agentsb/paths.py → ../../  == repo root
            resolved = Path(__file__).resolve().parent.parent.parent
        self.home: Path = resolved.resolve()
        self.lima_dir: Path = self.home / "lima"
        self.agents_dir: Path = self.lima_dir / "agents"
        self.base_template: Path = self.lima_dir / "base.yaml"
        self.completions_dir: Path = self.home / "completions"

    def agent_fragment(self, name: str) -> Path:
        return self.agents_dir / f"{name}.yaml"
