"""Unit tests for the declarative yank formats.

Shapes for the four presets follow the design note's table ("Yanking paths")
— the note marks the per-vendor details ⚠. The render *mechanism* (array
notation, bit select, leading sep, top exclusion, escapes) is exercised
with a deliberately divergent custom format.

Run:  nix develop . -c bash -c "PYTHONPATH=src python -m unittest discover -s tests"
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tren.formats import BY_NAME, DEFAULT, PathFormat, render


DEMO = ["top", "u_core", "g[0]", "u_alu_g"]


class RenderMechanismTests(unittest.TestCase):
    def test_plain_chain(self) -> None:
        self.assertEqual(
            render(PathFormat(name="x"), ["top", "u_core"], "result"),
            "top.u_core.result",
        )

    def test_array_segment_rerendered(self) -> None:
        fmt = PathFormat(name="x", array_notation=".{i}")
        self.assertEqual(render(fmt, ["top", "g[0]"], None), "top.g.0")

    def test_bit_select_appended_to_element(self) -> None:
        fmt = PathFormat(name="x", bit_select="[{i}]")
        self.assertEqual(render(fmt, ["top"], "out", 3), "top.out[3]")

    def test_leading_sep_and_top_excluded(self) -> None:
        fmt = PathFormat(name="x", separator="/", leading_sep=True, include_top=False)
        self.assertEqual(render(fmt, ["top", "u_core"], "x"), "/u_core/x")

    def test_leading_sep_empty_passthrough(self) -> None:
        fmt = PathFormat(name="x", separator="/", leading_sep=True, include_top=False)
        self.assertEqual(render(fmt, ["top"], None), "")

    def test_unknown_escape_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PathFormat(name="x", escape="bogus")

    def test_custom_format_exercises_every_dimension(self) -> None:
        fmt = PathFormat(
            name="custom",
            separator="/",
            leading_sep=False,
            include_top=False,
            array_notation="[{i}]",
            bit_select="<{i}>",
            escape="tcl",
        )
        self.assertEqual(
            render(fmt, DEMO, "result", 7),
            r"u_core/g\[0\]/u_alu_g/result<7>",
        )


class PresetTests(unittest.TestCase):
    def test_default_is_sv(self) -> None:
        self.assertEqual(DEFAULT.name, "sv")

    def test_sv_hierarchical(self) -> None:
        fmt = BY_NAME["sv"]
        self.assertEqual(render(fmt, DEMO, "result"), "top.u_core.g[0].u_alu_g.result")

    def test_vivado_get_pins_top_excluded(self) -> None:
        fmt = BY_NAME["vivado"]
        self.assertEqual(
            render(fmt, DEMO, "result"), r"u_core/g\[0\]/u_alu_g/result"
        )

    def test_vivado_tcl_escaping_of_brackets(self) -> None:
        fmt = BY_NAME["vivado"]
        self.assertEqual(render(fmt, ["top", "g[0]"], None), r"g\[0\]")

    def test_yosys_select(self) -> None:
        fmt = BY_NAME["yosys"]
        self.assertEqual(render(fmt, DEMO, "result"), "top/u_core/g[0]/u_alu_g/result")

    def test_vcd_dot_path(self) -> None:
        fmt = BY_NAME["vcd"]
        self.assertEqual(render(fmt, DEMO, "result"), "top.u_core.g[0].u_alu_g.result")

    def test_all_presets_render_demo_path(self) -> None:
        for fmt in BY_NAME.values():
            out = render(fmt, DEMO, "result")
            self.assertIn("u_alu_g", out)
            self.assertNotIn(" ", out)

    def test_shell_escape_wraps_single_quotes(self) -> None:
        fmt = PathFormat(name="x", escape="shell")
        self.assertEqual(render(fmt, ["a b"], "c"), "'a b.c'")


if __name__ == "__main__":
    unittest.main()