#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pytest>=8",
#     "rich>=13.7",
#     "pyyaml>=6.0",
# ]
# ///
"""Test runner that spins up its own uv-managed venv so the suite runs
without a project-level pyproject.toml. Invoke directly:

    ./tests/run.py                  # run the whole suite
    ./tests/run.py -k test_parse    # filter
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    import pytest
    return pytest.main([str(HERE), "-v", *sys.argv[1:]])


if __name__ == "__main__":
    sys.exit(main())
