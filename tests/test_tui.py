"""Unit tests for the TUI's pure text helpers.

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

from tren.ir import Net, Port
from tren.tui import _parse_src, _window_start, net_leaf_label, port_leaf_label


class ParseSrcYosysSpanTests(unittest.TestCase):
    """yosys attribute spelling: file:line.col[-line.col]."""

    def test_single_line_span(self) -> None:
        self.assertEqual(_parse_src("tests/rtl/demo.sv:77.14-77.65"), ("tests/rtl/demo.sv", 77, 77))

    def test_multi_line_span_keeps_both_ends(self) -> None:
        self.assertEqual(_parse_src("tests/rtl/demo.sv:21.1-35.10"), ("tests/rtl/demo.sv", 21, 35))

    def test_net_declaration_span(self) -> None:
        # net x in the demo: `logic [W-1:0] x, y, rd, r0;`
        self.assertEqual(_parse_src("tests/rtl/demo.sv:50.17-50.18"), ("tests/rtl/demo.sv", 50, 50))


class ParseSrcSlangSpanTests(unittest.TestCase):
    """slang --ast-json spelling: file:line:col — the trailing field is a
    COLUMN. Read as a yosys span it yields the column as the line and leaves
    the line glued to the file name ('tests/rtl/demo.sv:46', 25)."""

    def test_port_declaration(self) -> None:
        self.assertEqual(_parse_src("tests/rtl/demo.sv:46:25"), ("tests/rtl/demo.sv", 46, 46))

    def test_generate_scope(self) -> None:
        # generate scopes carry a slang src too — same trap, different node
        self.assertEqual(_parse_src("tests/rtl/demo.sv:58:42"), ("tests/rtl/demo.sv", 58, 58))

    def test_column_is_not_mistaken_for_the_line(self) -> None:
        file, lo, hi = _parse_src("tests/rtl/demo.sv:46:25")
        self.assertEqual(file, "tests/rtl/demo.sv")
        self.assertNotEqual(lo, 25)


class ParseSrcEdgeTests(unittest.TestCase):
    def test_bare_line_has_no_column_to_strip(self) -> None:
        self.assertEqual(_parse_src("tests/rtl/demo.sv:12"), ("tests/rtl/demo.sv", 12, 12))

    def test_absolute_path_with_directories(self) -> None:
        self.assertEqual(_parse_src("/a/b/c.sv:9:3"), ("/a/b/c.sv", 9, 9))

    def test_missing_and_malformed_return_none(self) -> None:
        for src in (None, "", "garbage", "tests/rtl/demo.sv", "tests/rtl/demo.sv:x.y"):
            with self.subTest(src=src):
                self.assertIsNone(_parse_src(src))


class PortLeafLabelTests(unittest.TestCase):
    """'port <dir> <type> <name>'; the type carries the declared range."""

    def test_input_port(self) -> None:
        port = Port(name="a", direction="in", width=32, type="logic [31:0]")
        self.assertEqual(port_leaf_label(port), "port in    logic [31:0] a")

    def test_output_port(self) -> None:
        port = Port(name="result", direction="out", width=32, type="logic [31:0]")
        self.assertEqual(port_leaf_label(port), "port out   logic [31:0] result")

    def test_inout_port(self) -> None:
        port = Port(name="sda", direction="inout", width=8, type="logic [7:0]")
        self.assertEqual(port_leaf_label(port), "port inout logic [7:0] sda")

    def test_every_direction_starts_the_type_at_one_column(self) -> None:
        labels = [
            port_leaf_label(
                Port(name="n", direction=direction, width=4, type="logic [3:0]")
            )
            for direction in ("in", "out", "inout")
        ]
        self.assertEqual(
            {label.index("logic") for label in labels}, {len("port ") + 6}
        )

    def test_scalar_port_type_reads_without_a_range(self) -> None:
        port = Port(name="clk", direction="in", width=1, type="logic")
        self.assertEqual(port_leaf_label(port), "port in    logic clk")

    def test_a_port_without_a_type_falls_back_to_its_range(self) -> None:
        port = Port(name="mode", direction="in", width=2)
        self.assertEqual(port_leaf_label(port), "port in    mode[1:0]")


class NetLeafLabelTests(unittest.TestCase):
    """'<type> <name>' - a net row reads as the declaration."""

    def test_multi_bit_net(self) -> None:
        net = Net(name="x", width=32, type="logic [31:0]")
        self.assertEqual(net_leaf_label(net), "logic [31:0] x")

    def test_scalar_net(self) -> None:
        self.assertEqual(
            net_leaf_label(Net(name="we", width=1, type="wire")), "wire we"
        )

    def test_a_net_without_a_type_falls_back_to_the_marker(self) -> None:
        self.assertEqual(net_leaf_label(Net(name="x", width=32)), "net x[31:0]")


class DeclaredRangeLabelTests(unittest.TestCase):
    """The slang overlay carries the range the source writes.

    The width alone spells every range [width-1:0], which is wrong for a
    descending range that does not end at 0 and for an ascending one.
    """

    def test_declared_range_rides_in_the_type(self) -> None:
        port = Port(
            name="down", direction="in", width=7, range="[7:1]", type="logic [7:1]"
        )
        self.assertEqual(port_leaf_label(port), "port in    logic [7:1] down")

    def test_ascending_range(self) -> None:
        self.assertEqual(
            net_leaf_label(Net(name="up", width=8, range="[0:7]", type="logic [0:7]")),
            "logic [0:7] up",
        )

    def test_a_one_bit_vector_keeps_its_range(self) -> None:
        # 'logic [0:0] v' is a vector, not a scalar: the source says so.
        port = Port(
            name="v", direction="out", width=1, range="[0:0]", type="logic [0:0]"
        )
        self.assertEqual(port_leaf_label(port), "port out   logic [0:0] v")

    def test_without_the_overlay_the_width_still_spells_it(self) -> None:
        self.assertEqual(
            net_leaf_label(Net(name="n", width=4, range=None)), "net n[3:0]"
        )


class SearchWindowStartTests(unittest.TestCase):
    """Committed search: the first row of the sliding match window.

    The finder materialises MAX_ROWS rows at once; n/N always keep the cursor
    visible, so the window start is the cursor centred and clamped to the
    list. A list that fits needs no window.
    """

    def test_a_list_that_fits_has_no_window(self) -> None:
        self.assertEqual(_window_start(5, 200, 200), 0)
        self.assertEqual(_window_start(5, 9, 200), 0)

    def test_cursor_centred_when_the_list_is_longer(self) -> None:
        # 200 // 2 = 100 rows above the cursor, so the window opens at 300-100
        self.assertEqual(_window_start(300, 500, 200), 200)

    def test_window_clamped_at_the_top(self) -> None:
        self.assertEqual(_window_start(0, 500, 200), 0)
        self.assertEqual(_window_start(99, 500, 200), 0)

    def test_window_clamped_at_the_tail(self) -> None:
        self.assertEqual(_window_start(499, 500, 200), 300)
        self.assertEqual(_window_start(400, 500, 200), 300)

    def test_even_window_window_rounds_down(self) -> None:
        # 200 // 2 = 100 rows above; the cursor sits just past the middle
        self.assertEqual(_window_start(299, 400, 200), 199)


if __name__ == "__main__":
    unittest.main()
