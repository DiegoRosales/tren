"""Unit tests for the TUI's pure source-span parsing (cards 242/243).

`_parse_src` is the one piece of tui.py with no UI in it, and it has to read
*two* span spellings because two backends feed the IR: yosys writes
`line.col-line.col` attributes, slang --ast-json writes `line:col`. Both
reach the same field, so the parser is the seam where a mix-up turns into a
source pane pointing at a bogus file and line.

Run:  nix develop . -c bash -c "PYTHONPATH=src python -m unittest discover -s tests"
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tren.tui import _parse_src


class ParseSrcYosysSpanTests(unittest.TestCase):
    """yosys attribute spelling: file:line.col[-line.col]."""

    def test_single_line_span(self) -> None:
        self.assertEqual(_parse_src("rtl/demo.sv:77.14-77.65"), ("rtl/demo.sv", 77, 77))

    def test_multi_line_span_keeps_both_ends(self) -> None:
        self.assertEqual(_parse_src("rtl/demo.sv:21.1-35.10"), ("rtl/demo.sv", 21, 35))

    def test_net_declaration_span(self) -> None:
        # net x in the demo: `logic [W-1:0] x, y, rd, r0;`
        self.assertEqual(_parse_src("rtl/demo.sv:50.17-50.18"), ("rtl/demo.sv", 50, 50))


class ParseSrcSlangSpanTests(unittest.TestCase):
    """slang --ast-json spelling: file:line:col — the trailing field is a
    COLUMN. Read as a yosys span it yields the column as the line and leaves
    the line glued to the file name ('rtl/demo.sv:46', 25)."""

    def test_port_declaration(self) -> None:
        self.assertEqual(_parse_src("rtl/demo.sv:46:25"), ("rtl/demo.sv", 46, 46))

    def test_generate_scope(self) -> None:
        # generate scopes carry a slang src too — same trap, different node
        self.assertEqual(_parse_src("rtl/demo.sv:58:42"), ("rtl/demo.sv", 58, 58))

    def test_column_is_not_mistaken_for_the_line(self) -> None:
        file, lo, hi = _parse_src("rtl/demo.sv:46:25")
        self.assertEqual(file, "rtl/demo.sv")
        self.assertNotEqual(lo, 25)


class ParseSrcEdgeTests(unittest.TestCase):
    def test_bare_line_has_no_column_to_strip(self) -> None:
        self.assertEqual(_parse_src("rtl/demo.sv:12"), ("rtl/demo.sv", 12, 12))

    def test_absolute_path_with_directories(self) -> None:
        self.assertEqual(_parse_src("/a/b/c.sv:9:3"), ("/a/b/c.sv", 9, 9))

    def test_missing_and_malformed_return_none(self) -> None:
        for src in (None, "", "garbage", "rtl/demo.sv", "rtl/demo.sv:x.y"):
            with self.subTest(src=src):
                self.assertIsNone(_parse_src(src))


if __name__ == "__main__":
    unittest.main()
