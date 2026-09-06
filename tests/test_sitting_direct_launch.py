"""Regression coverage for the documented direct case-sitting launcher."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_documented_direct_launch_resolves_repo_packages() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "webapp/sitting.py", "--list"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
