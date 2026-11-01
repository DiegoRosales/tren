"""Unit tests for tren.bits over a hand-built netlist that mirrors the
verified yosys JSON shapes (see tren_testing/t1_yosys/findings.md).

Run:  nix develop . -c bash -c "PYTHONPATH=src python -m unittest discover -s tests"
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tren import bits  # noqa: E402

CORE = r"$paramod\core\W=s32'00000000000000000000000000100000"
COREXOR = "$xor$tests/rtl/demo.sv:64$10"
CONCAT = "$concat$tests/rtl/demo.sv:76$9"


def design() -> bits.Netlist:
    """Synthetic netlist shaped like the real demo.sv output:

    top: hi[0:3], lo[4:7], o[8:15] = $concat(A=hi, B=lo, Y=o);
         u_core reads o[8:11]/o[12:15], writes out[66:69].
    core (W=32): alu_result[100:103] = $xor(A[80:83], B[90:93], Y).
    Bit numbers intentionally differ across the boundary (66..69 vs
    100..103) to prove positional-only matching.
    """
    return {
        "creator": "tren unit test",
        "modules": {
            "top": {
                "netnames": {
                    "hi": {"bits": [0, 1, 2, 3], "attributes": {"src": "tests/rtl/demo.sv:71.24-71.26"}},
                    "lo": {"bits": [4, 5, 6, 7], "attributes": {"src": "tests/rtl/demo.sv:72.24-72.26"}},
                    "o": {"bits": [8, 9, 10, 11, 12, 13, 14, 15], "attributes": {"src": "tests/rtl/demo.sv:75.16-75.17"}},
                    "out": {"bits": [66, 67, 68, 69], "attributes": {"src": "tests/rtl/demo.sv:73.24-73.27"}},
                },
                "cells": {
                    CONCAT: {
                        "type": "$concat",
                        "connections": {
                            "A": [0, 1, 2, 3],
                            "B": [4, 5, 6, 7],
                            "Y": [8, 9, 10, 11, 12, 13, 14, 15],
                        },
                        "attributes": {"src": "tests/rtl/demo.sv:76.9-76.21"},
                    },
                    "u_core": {
                        "type": CORE,
                        "port_directions": {"a": "input", "b": "input", "alu_result": "output"},
                        "connections": {
                            "a": [8, 9, 10, 11],
                            "b": [12, 13, 14, 15],
                            "op": ["0", "0", "0", "1"],  # constants, not bits
                            "alu_result": [66, 67, 68, 69],
                        },
                        "attributes": {"src": "tests/rtl/demo.sv:77.14-77.65"},
                    },
                },
                "attributes": {"top": "1", "src": "tests/rtl/demo.sv:70.1-78.9"},
            },
            CORE: {
                "netnames": {
                    "alu_result": {
                        "bits": [100, 101, 102, 103],
                        "attributes": {"src": "tests/rtl/demo.sv:48.24-48.33"},
                    },
                },
                "cells": {
                    COREXOR: {
                        "type": "$xor",
                        "connections": {
                            "A": [80, 81, 82, 83],
                            "B": [90, 91, 92, 93],
                            "Y": [100, 101, 102, 103],
                        },
                        "attributes": {"src": "tests/rtl/demo.sv:64.23-64.34"},
                    },
                },
                "attributes": {"src": "tests/rtl/demo.sv:45.1-68.9"},
            },
        },
    }


class UnmangleTests(unittest.TestCase):
    def test_paramod_name(self) -> None:
        base, params = bits.unmangle(CORE)
        self.assertEqual(base, "core")
        self.assertIn("W", params)
        self.assertTrue(params["W"].startswith("s32'"))

    def test_plain_name(self) -> None:
        self.assertEqual(bits.unmangle("top"), ("top", {}))


class BitMapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.d = design()
        self.top = self.d["modules"]["top"]

    def test_out_is_driven_by_submodule_port(self) -> None:
        out_bits = set(self.top["netnames"]["out"]["bits"])
        rows = bits.drivers_of(self.top, out_bits)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cell"], "u_core")
        self.assertEqual(rows[0]["port"], "alu_result")
        self.assertEqual(rows[0]["dir"], "out")
        self.assertEqual(rows[0]["src"], "tests/rtl/demo.sv:77.14-77.65")

    def test_out_has_no_loads(self) -> None:
        out_bits = set(self.top["netnames"]["out"]["bits"])
        self.assertEqual(bits.loads_of(self.top, out_bits), [])

    def test_o_driven_by_concat(self) -> None:
        o_bits = set(self.top["netnames"]["o"]["bits"])
        rows = bits.drivers_of(self.top, o_bits)
        self.assertEqual(rows[0]["cell"], CONCAT)
        self.assertEqual(rows[0]["port"], "Y")

    def test_constant_connections_ignored(self) -> None:
        o_bits = set(self.top["netnames"]["o"]["bits"])
        for row in bits.rows_hitting_bits(self.top, o_bits):
            self.assertNotEqual(row["port"], "op")

    def test_loads_of_o_are_the_submodule_inputs(self) -> None:
        o_bits = set(self.top["netnames"]["o"]["bits"])
        loads = bits.loads_of(self.top, o_bits)
        ports = {(r["cell"], r["port"]) for r in loads}
        self.assertIn(("u_core", "a"), ports)
        self.assertIn(("u_core", "b"), ports)
        self.assertTrue(all(r["dir"] == "in" for r in loads))


class UnknownDirectionTests(unittest.TestCase):
    """An instance of a module the netlist lacks has no directions.

    The design note's rule: render the direction as unknown, never guess.
    """

    def test_cell_directions_returns_none(self) -> None:
        cell = {"type": "vendor_fifo", "connections": {"dout": [1], "din": [2]}}
        self.assertIsNone(bits.cell_directions(cell, "vendor_fifo"))

    def test_child_module_ports_supply_the_directions(self) -> None:
        cell = {"type": "fifo", "connections": {"dout": [1], "din": [2]}}
        modules = {
            "fifo": {
                "ports": {
                    "dout": {"direction": "output"},
                    "din": {"direction": "input"},
                }
            }
        }
        self.assertEqual(
            bits.cell_directions(cell, "fifo", modules),
            {"dout": "out", "din": "in"},
        )

    def test_an_unknown_row_is_not_dropped(self) -> None:
        module = {
            "cells": {
                "u_fifo": {"type": "vendor_fifo", "connections": {"dout": [1]}}
            },
            "netnames": {"y": {"bits": [1]}},
        }
        rows = bits.rows_hitting_bits(module, {1})
        self.assertEqual(
            [(r["cell"], r["port"], r["dir"]) for r in rows],
            [("u_fifo", "dout", "unknown")],
        )
        self.assertEqual(len(bits.drivers_of(module, {1})), 1)
        self.assertEqual(len(bits.loads_of(module, {1})), 1)


class BoundaryTests(unittest.TestCase):
    def test_trace_crosses_the_boundary(self) -> None:
        chain = bits.trace_drivers(design(), "top", "out")
        self.assertEqual(len(chain), 1)
        step = chain[0]
        self.assertEqual(step["cell"], "u_core")

        into = step["into"]
        self.assertIsNotNone(into)
        self.assertEqual(into["module"], CORE)
        self.assertEqual(into["child_bits"], [100, 101, 102, 103])

        inner = into["drivers"]
        self.assertEqual(len(inner), 1)
        self.assertEqual(inner[0]["cell"], COREXOR)
        self.assertEqual(inner[0]["port"], "Y")
        self.assertIsNotNone(inner[0]["src"], "driver must carry its src attribute")

    def test_crossing_uses_position_not_numbers(self) -> None:
        # Parent presents the port as bits 66..69, child as 100..103: the
        # walk must still find the xor (positional-only correspondence).
        chain = bits.trace_drivers(design(), "top", "out")
        self.assertEqual(chain[0]["into"]["drivers"][0]["cell"], COREXOR)


if __name__ == "__main__":
    unittest.main()