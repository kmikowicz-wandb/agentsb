"""Load bin/agentsb (a PEP 723 single-file script with no .py extension)
as an importable module named `agentsb` for the test suite.
"""
from __future__ import annotations

import importlib.util
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "agentsb"


def _load_agentsb():
    # bin/agentsb has no .py extension, so we provide an explicit SourceFileLoader.
    loader = SourceFileLoader("agentsb", str(SCRIPT))
    spec = importlib.util.spec_from_loader("agentsb", loader)
    assert spec, f"could not build spec for {SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    sys.modules["agentsb"] = module
    loader.exec_module(module)
    return module


agentsb = _load_agentsb()
