"""Unit tests for clipboard provider selection and the tmux writer.

The provider selection is pure. The tmux writer is tested with a fake
subprocess runner, so no tmux server or clipboard is touched.

Run:  nix develop . -c bash -c "PYTHONPATH=src python -m unittest discover -s tests"
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tren.clipboard import (  # noqa: E402
    ClipboardError,
    resolve_provider,
    write_tmux,
)


def _which_found(name: str) -> str | None:
    return "/usr/bin/tmux" if name == "tmux" else None


def _which_missing(name: str) -> str | None:
    return None


class ResolveProviderTests(unittest.TestCase):
    def test_auto_uses_tmux_inside_a_tmux_session(self) -> None:
        self.assertEqual(
            resolve_provider("auto", env={"TMUX": "/tmp/tmux-1000/default,1,0"},
                             which=_which_found),
            "tmux",
        )

    def test_auto_uses_osc52_outside_tmux(self) -> None:
        self.assertEqual(resolve_provider("auto", env={}, which=_which_found), "osc52")

    def test_auto_falls_back_when_the_tmux_command_is_missing(self) -> None:
        self.assertEqual(
            resolve_provider("auto", env={"TMUX": "set"}, which=_which_missing),
            "osc52",
        )

    def test_explicit_providers_win_over_detection(self) -> None:
        for provider in ("tmux", "osc52"):
            with self.subTest(provider=provider):
                self.assertEqual(
                    resolve_provider(provider, env={"TMUX": "set"},
                                     which=_which_found),
                    provider,
                )

    def test_unknown_provider_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            resolve_provider("xclip", env={}, which=_which_found)


class WriteTmuxTests(unittest.TestCase):
    def test_writes_the_payload_through_load_buffer(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def runner(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        write_tmux("top.u_core.x", runner=runner)
        self.assertEqual(calls[0][0], ["tmux", "load-buffer", "-w", "-"])
        self.assertEqual(calls[0][1]["input"], "top.u_core.x")
        self.assertTrue(calls[0][1]["text"])
        self.assertTrue(calls[0][1]["capture_output"])
        self.assertFalse(calls[0][1]["check"])

    def test_nonzero_exit_raises_with_stderr(self) -> None:
        def runner(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no client")

        with self.assertRaises(ClipboardError) as caught:
            write_tmux("top", runner=runner)
        self.assertIn("no client", str(caught.exception))

    def test_nonzero_exit_without_output_names_the_exit_code(self) -> None:
        def runner(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 7, stdout="", stderr="")

        with self.assertRaises(ClipboardError) as caught:
            write_tmux("top", runner=runner)
        self.assertIn("exit 7", str(caught.exception))

    def test_os_error_becomes_a_clipboard_error(self) -> None:
        def runner(cmd, **kwargs):
            raise FileNotFoundError("tmux")

        with self.assertRaises(ClipboardError) as caught:
            write_tmux("top", runner=runner)
        self.assertIn("cannot run tmux", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
