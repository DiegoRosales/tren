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

from tren.config import Config  # noqa: E402
from tren.ir import (
    BitLink,
    Cell,
    CellPin,
    Design,
    Endpoint,
    Instance,
    Net,
    NetConnectivity,
    Port,
    Scope,
)
from tren.tui import (
    TrenApp,
    TraceNode,
    _cell_row_label,
    _gutter_width,
    _net_slice,
    _normalise_positions,
    _parse_src,
    _pin_slice,
    _src_focus,
    _window_start,
    net_leaf_label,
    port_leaf_label,
)


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


class NetSliceTests(unittest.TestCase):
    """The bit select a parent connection touches on its net.

    yosys lists net bits LSB first and the right bound of a declared range
    is that LSB, so the select reads back in the declared direction.
    """

    def test_descending_net(self) -> None:
        net = Net(name="o", width=64, range="[63:0]")
        self.assertEqual(_net_slice(net, [0, 1]), "[1:0]")

    def test_ascending_net_keeps_the_declared_direction(self) -> None:
        net = Net(name="up", width=8, range="[0:7]")
        self.assertEqual(_net_slice(net, [0, 1, 2, 3]), "[4:7]")

    def test_one_bit_reads_as_a_select(self) -> None:
        net = Net(name="nd", width=8, range="[15:8]")
        self.assertEqual(_net_slice(net, [2]), "[10]")

    def test_a_scalar_has_no_select(self) -> None:
        self.assertEqual(_net_slice(Net(name="we", width=1), [0]), "")


class CellRowLabelTests(unittest.TestCase):
    """A netlist cell row reads as its type, pin and source line."""

    def test_type_pin_and_source_line(self) -> None:
        cell = Cell(name="$xor$...", type="$xor", src="tests/rtl/demo.sv:64.18-64.24")
        self.assertEqual(_cell_row_label(cell, cell.name, "Y"), "$xor.Y  demo.sv:64")

    def test_an_internal_cell_leads_with_the_net_it_sits_on(self) -> None:
        cell = Cell(name="$xor$...", type="$xor", src="tests/rtl/demo.sv:64.18-64.24")
        self.assertEqual(
            _cell_row_label(cell, cell.name, "Y", "alu_result"),
            "alu_result  $xor.Y  demo.sv:64",
        )

    def test_without_metadata_keeps_the_cell_name(self) -> None:
        self.assertEqual(_cell_row_label(None, "u_sram", "rdata"), "u_sram.rdata")

    def test_an_unresolved_instance_reads_by_cell_name(self) -> None:
        cell = Cell(name="mix_fifo", type="axis_fifo", src="hw/fifo.sv:173.1-190.2")
        self.assertEqual(
            _cell_row_label(cell, "mix_fifo", "m_axis_tvalid"),
            "mix_fifo.m_axis_tvalid  fifo.sv:173",
        )

    def test_a_multi_span_cell_keeps_its_first_expression(self) -> None:
        cell = Cell(
            name="$pmux$...",
            type="$pmux",
            src=(
                "tests/rtl/demo.sv:30.31-30.31"
                "|tests/rtl/demo.sv:28.12-33.12"
            ),
        )
        self.assertEqual(_cell_row_label(cell, cell.name, "Y"), "$pmux.Y  demo.sv:30")

    def test_src_focus_reads_the_first_span(self) -> None:
        self.assertEqual(
            _src_focus("tests/rtl/demo.sv:30.31-30.31|tests/rtl/demo.sv:28.12-33.12"),
            ("tests/rtl/demo.sv", 30, 30),
        )


class PinSliceTests(unittest.TestCase):
    """The pin-side select a net covers on a cell connection."""

    def test_a_full_pin_has_no_select(self) -> None:
        pin = CellPin(name="A", direction="in", width=32)
        self.assertEqual(_pin_slice(pin, list(range(32))), "")

    def test_part_of_a_wide_pin_keeps_its_bits(self) -> None:
        pin = CellPin(name="B", direction="in", width=96)
        self.assertEqual(_pin_slice(pin, list(range(64, 96))), "[95:64]")

    def test_one_bit(self) -> None:
        pin = CellPin(name="S", direction="in", width=3)
        self.assertEqual(_pin_slice(pin, [2]), "[2]")


class CellPinSliceTests(unittest.TestCase):
    """A sliced trace keeps the child net positions through a cell pin."""

    def test_pin_net_positions_survive_a_sliced_trace(self) -> None:
        stage = Net(
            name="stage", width=16, bits=tuple(range(16)), range="[15:0]"
        )
        tap = Net(
            name="tap", width=16, bits=tuple(range(100, 116)), range="[15:0]"
        )
        cell = Cell(
            name="$and",
            pins=(
                CellPin("A", direction="in", width=8),
                CellPin("Y", direction="out", width=8),
            ),
        )
        scope = Scope(
            name="top",
            module="top",
            nets=(stage, tap),
            cells=(cell,),
        )
        stage_bits = tuple(
            BitLink(
                position=position,
                loads=(Endpoint("$and", "A", position - 8),)
                if 8 <= position < 16
                else (),
            )
            for position in range(16)
        )
        tap_bits = tuple(
            BitLink(
                position=position,
                drivers=(Endpoint("$and", "Y", position - 8),)
                if 8 <= position < 16
                else (),
            )
            for position in range(16)
        )
        design = Design(
            top="top",
            scopes=(scope,),
            connectivity=(
                NetConnectivity(net="stage", scope="top", bits=stage_bits),
                NetConnectivity(net="tap", scope="top", bits=tap_bits),
            ),
        )
        app = TrenApp(design)
        node = TraceNode("top", "stage", None, positions=tuple(range(8, 16)))
        app._trace_dir = "loads"
        rows = app._trace_rows_of(node)
        cell_row = next(row for row in rows if row["kind"] == "cell")
        self.assertEqual(
            [
                (pin, positions)
                for _name, pin, _label, positions in cell_row["pins"]
            ],
            [("tap", tuple(range(8, 16)))],
        )
        self.assertIsNone(_normalise_positions(scope, "stage", tuple(range(16))))
        self.assertEqual(
            _normalise_positions(scope, "tap", tuple(range(8, 16))),
            tuple(range(8, 16)),
        )


class GutterWidthTests(unittest.TestCase):
    """The line-number column is sized for the whole file.

    A column that follows the visible window would jump between one and two
    digits while the source scrolls.
    """

    def test_single_digit_file(self) -> None:
        self.assertEqual(_gutter_width(9), 1)

    def test_two_digit_file(self) -> None:
        self.assertEqual(_gutter_width(99), 2)

    def test_three_digit_file(self) -> None:
        self.assertEqual(_gutter_width(100), 3)

    def test_an_empty_file_still_gets_one_column(self) -> None:
        self.assertEqual(_gutter_width(0), 1)


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


class UnresolvedHierarchyTests(unittest.IsolatedAsyncioTestCase):
    async def test_unresolved_scope_is_visible_with_error_style(self) -> None:
        top = Scope(
            name="top",
            module="top",
            instances=(
                Instance(name="u_ram", module="ram_model", blackbox=True),
            ),
        )
        missing = Scope(
            name="top.u_ram",
            module="ram_model",
            blackbox=True,
            unresolved=True,
        )
        app = TrenApp(Design(top="top", scopes=(top, missing)), settings=Config())

        async with app.run_test() as pilot:
            await pilot.pause()
            node = next(
                node
                for node in app._visible()
                if node.data
                and node.data.get("scope") is not None
                and node.data["scope"].unresolved
            )

        self.assertEqual(node.label.plain, "u_ram/!")
        self.assertEqual(app._node_kind(node), "unresolved")
        self.assertTrue(app._row_colors["unresolved"])


if __name__ == "__main__":
    unittest.main()
