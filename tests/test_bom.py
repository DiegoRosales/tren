"""Unit tests for the SW BOM generator.

Only the component's *pure* logic is tested here: the per-tool version
extractors, the "not found" fallback, and the README pinned-target parser.
The subprocess probing is exercised instead by running `make bom` (layer 1
unit + generator output, no network).

The generator lives in `tools/`, not the package, so the test inserts the
`tools/` directory into `sys.path` (the package tests insert `src/`).

Run:  nix develop . -c bash -c "PYTHONPATH=src python -m unittest discover -s tests"
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from bom import (  # type: ignore   # tools/ is not a package
    _pinned_targets,
    _vers_cargo,
    _vers_jq,
    _vers_make,
    _vers_python,
    _vers_rg,
    _vers_rustc,
    _vers_slang,
    _vers_tmux,
    _vers_verilator,
    _vers_yosys,
    probe_version,
    render_bom,
)


class VersionExtractorTests(unittest.TestCase):
    def test_yosys(self) -> None:
        self.assertEqual(_vers_yosys("Yosys 0.69+post (git sha1 v0.69, ...)"), "0.69+post")

    def test_slang(self) -> None:
        self.assertEqual(_vers_slang("slang version 11.0.0+0"), "11.0.0+0")

    def test_verilator_keeps_revision_date(self) -> None:
        self.assertEqual(
            _vers_verilator("Verilator 5.052 2026-09-05 rev v5.052"), "5.052 2026-09-05"
        )

    def test_rustc(self) -> None:
        self.assertEqual(_vers_rustc("rustc 1.98.1 (48a229cea 2026-09-01)"), "1.98.1")

    def test_cargo(self) -> None:
        self.assertEqual(_vers_cargo("cargo 1.98.0 (797e8a9bc 2026-08-05)"), "1.98.0")

    def test_jq(self) -> None:
        self.assertEqual(_vers_jq("jq-1.8.2"), "1.8.2")

    def test_make(self) -> None:
        self.assertEqual(_vers_make("GNU Make 4.4.1"), "4.4.1")

    def test_ripgrep(self) -> None:
        self.assertEqual(_vers_rg("ripgrep 15.2.0\n  -S --smart-case ..."), "15.2.0")

    def test_tmux(self) -> None:
        self.assertEqual(_vers_tmux("tmux 3.7c"), "3.7c")

    def test_python(self) -> None:
        self.assertEqual(_vers_python("Python 3.14.7"), "3.14.7")


class ProbeFallbackTests(unittest.TestCase):
    def test_empty_output_is_not_found(self) -> None:
        self.assertEqual(probe_version(["yosys", "-V"], _vers_yosys, ""), "not found")

    def test_whitespace_only_output_is_not_found(self) -> None:
        self.assertEqual(probe_version(["x"], _vers_yosys, "\n  \n"), "not found")

    def test_unparseable_line_is_reported_raw(self) -> None:
        self.assertEqual(probe_version(["x"], _vers_slang, "gibberish"), "gibberish")


class PinnedTargetTests(unittest.TestCase):
    def test_readme_names_the_rewrite_target(self) -> None:
        pinned = dict(_pinned_targets())
        self.assertEqual(pinned.get("ratatui"), "0.30.2")
        self.assertEqual(pinned.get("textual"), "8.2.8")
        self.assertTrue(pinned)  # the parses must keep finding the sentence


class RenderTests(unittest.TestCase):
    def test_document_has_the_section_markers(self) -> None:
        doc = render_bom(commit="abc1234", date="2026-09-20 00:00")
        for marker in (
            "Software bill of materials",
            "Runtime Python packages",
            "Elaboration backends and tooling",
            "Pinned target versions",
        ):
            self.assertIn(marker, doc)


if __name__ == "__main__":
    unittest.main()
