"""The Makefile runs in the current shell; only NIX_DEV=1 spawns nix develop.

Dry runs (`make -n`) keep the check free of side effects: they print the
command lines without executing them.

Run:  nix develop . -c python -m unittest discover -s tests
"""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("make"), "make is not installed")
class MakefileTest(unittest.TestCase):
    def _dry_run(self, target: str, **env: str) -> str:
        environ = {
            k: v for k, v in os.environ.items() if k not in ("NIX_DEV", "TREN_SHELL")
        }
        environ.update(env)
        done = subprocess.run(
            ["make", "-n", target],
            cwd=ROOT,
            env=environ,
            capture_output=True,
            text=True,
            check=True,
        )
        return done.stdout

    def test_default_runs_in_the_current_shell(self) -> None:
        for target in ("test", "drive", "import"):
            with self.subTest(target=target):
                self.assertNotIn("nix develop", self._dry_run(target))

    def test_nix_dev_wraps_the_command(self) -> None:
        out = self._dry_run("test", NIX_DEV="1")
        self.assertIn(
            "nix develop . -c python -m unittest discover -s tests", out
        )

    def test_nix_dev_honours_tren_shell(self) -> None:
        out = self._dry_run("test", NIX_DEV="1", TREN_SHELL=".#tren-yosys-src")
        self.assertIn("nix develop .#tren-yosys-src -c", out)

    def test_tren_comes_from_path(self) -> None:
        self.assertIn("tren open tests/rtl", self._dry_run("open"))
        self.assertIn("tren-tui", self._dry_run("run-fixture"))


if __name__ == "__main__":
    unittest.main()
