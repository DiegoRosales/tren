"""Tests for the elaboration runner (card 208).

Parser unit tests run without tools; the acceptance integration test shells
out to yosys/slang-hier/slang and is skipped outside the nix dev shell.

Run:  nix develop . -c bash -c "PYTHONPATH=src python -m unittest discover -s tests"
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tren.cache import IrCache  # noqa: E402
from tren.elaborate import (  # noqa: E402
    _bit_key,
    _decl_default,
    _nets_for,
    _src_pos,
    _tool_diagnostics,
    _type_range,
    _type_string,
    build_design,
    elaborate,
    parse_hier_line,
    run_slang_ast,
    run_slang_hier,
    run_yosys,
)
from tren.ir import BoundaryLink, Design, NetConnectivity, Port, Scope  # noqa: E402
from tren.manifest import Manifest  # noqa: E402
from tren.tui import net_leaf_label, port_leaf_label  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


class HierParseTests(unittest.TestCase):
    def test_line_with_params(self) -> None:
        node = parse_hier_line(
            'Module="core" Instance="top.u_core" File="rtl/demo.sv" '
            "Parameters: W=32'd32, OP=4'b11"
        )
        self.assertIsNotNone(node)
        assert node is not None
        self.assertEqual(node["inst"], "top.u_core")
        self.assertEqual(node["module"], "core")
        self.assertEqual(node["params"], {"W": "32'd32", "OP": "4'b11"})

    def test_line_without_params(self) -> None:
        node = parse_hier_line('Module="top" Instance="top" File="rtl/demo.sv"')
        self.assertIsNotNone(node)
        assert node is not None
        self.assertEqual(node["params"], {})

    def test_generate_instance_path_kept(self) -> None:
        node = parse_hier_line(
            'Module="alu" Instance="top.u_core.g[1].u_alu_g" File="rtl/demo.sv"'
        )
        assert node is not None
        self.assertEqual(node["inst"], "top.u_core.g[1].u_alu_g")

    def test_non_matching_line_is_none(self) -> None:
        self.assertIsNone(parse_hier_line("Build succeeded: 0 errors, 0 warnings"))


class BackendFlagTests(unittest.TestCase):
    """Card 259: manifest flags reach every runner, without running tools."""

    MANIFEST = Manifest(
        sources=("rtl/demo.sv",),
        slang_flags=("--timescale=1ns/1ps", "--allow-use-before-declare", "-Wno-redefinition"),
        yosys_flags=("-DEXTRA=1",),
    )

    def _capture(self, runner: object) -> list[str]:
        import json

        captured: list[str] = []

        def fake_run(cmd, cwd):
            captured.append(cmd)
            for i, a in enumerate(cmd):
                if a == "--ast-json" and i + 1 < len(cmd):
                    json.dump({}, open(cmd[i + 1], "w"))
            return "Build succeeded: 0 errors, 0 warnings"

        import tren.elaborate as elab

        original = elab._run
        try:
            elab._run = fake_run
            runner()  # type: ignore[operator]
        finally:
            elab._run = original
        return captured

    def test_slang_hier_carries_defaults_and_manifest_flags(self) -> None:
        cmd = self._capture(lambda: run_slang_hier(self.MANIFEST, REPO))[0]
        self.assertIn("--ignore-unknown-modules", cmd)
        self.assertIn("--timescale=1ns/1ps", cmd)
        self.assertIn("--allow-use-before-declare", cmd)
        self.assertIn("-Wno-redefinition", cmd)
        self.assertIn("rtl/demo.sv", cmd)

    def test_slang_ast_carries_defaults_and_manifest_flags(self) -> None:
        cmd = self._capture(
            lambda: run_slang_ast(self.MANIFEST, REPO, "/tmp/ast.json")
        )[0]
        self.assertIn("--ignore-unknown-modules", cmd)
        self.assertIn("--timescale=1ns/1ps", cmd)
        self.assertIn("--ast-json-source-info", cmd)

    def test_yosys_carries_defaults_and_manifest_flags(self) -> None:
        script = self._capture(
            lambda: run_yosys(self.MANIFEST, REPO, "/tmp/netlist.json")
        )[0][3]
        self.assertIn("read_verilog -sv -nolatches -DEXTRA=1 rtl/demo.sv", script)
        self.assertIn("hierarchy -top top", script)
        self.assertIn("proc;", script)

    def test_default_slags_named_not_forced_off(self) -> None:
        bare = Manifest(sources=("rtl/demo.sv",))
        cmd = self._capture(lambda: run_slang_hier(bare, REPO))[0]
        self.assertIn("--ignore-unknown-modules", cmd)
        self.assertNotIn("--timescale", cmd)


class DiagnosticTests(unittest.TestCase):
    """Card 259: a failed backend shows its stderr, not a raw argv."""

    def test_diagnostics_trim_long_output(self) -> None:
        err = subprocess.CalledProcessError(
            1,
            ["slang-hier"],
            output="",
            stderr="\n".join(f"line {i}" for i in range(100)),
        )
        diag = _tool_diagnostics(err)
        self.assertLessEqual(len(diag.splitlines()), 40)
        self.assertIn("line 99", diag)

    def test_diagnostics_prefers_stderr(self) -> None:
        err = subprocess.CalledProcessError(
            1, ["yosys"], output="stdout-noise", stderr="real: syntax error"
        )
        self.assertEqual(_tool_diagnostics(err), "real: syntax error")

    def test_diagnostics_handles_empty_stderr(self) -> None:
        err = subprocess.CalledProcessError(1, ["yosys"], output="oops")
        self.assertEqual(_tool_diagnostics(err), "oops")


class BuildDesignUnitTests(unittest.TestCase):
    """build_design against tiny inline inputs (no subprocesses)."""

    MANIFEST = Manifest(name="demo", sources=("rtl/demo.sv",), top="top")

    NETLIST = {
        "modules": {
            "top": {
                "attributes": {"src": "rtl/demo.sv:70.1-78.10"},
                "ports": {
                    "out": {"direction": "output", "bits": [66, 67, 68]},
                },
                "netnames": {"o": {"bits": [2, 3], "attributes": {"src": "rtl/demo.sv:75"}}},
                "cells": {
                    "u_core": {
                        "type": r"$paramod\core\W=s32'32",
                        "attributes": {"src": "rtl/demo.sv:77"},
                        "port_directions": {"a": "input", "out": "output"},
                        "connections": {"out": [66, 67, 68]},
                    }
                },
            },
            r"$paramod\core\W=s32'32": {
                "attributes": {"src": "rtl/demo.sv:45.1-68.11"},
                "ports": {
                    "a": {"direction": "input", "bits": [0, 1, 2]},
                    "out": {"direction": "output", "bits": [10, 11, 12]},
                },
                "netnames": {"out": {"bits": [10, 11, 12]}},
                "cells": {},
            },
        }
    }

    HIER = [
        {"inst": "top", "module": "top", "file": "rtl/demo.sv", "params": {}},
        {
            "inst": "top.u_core",
            "module": "core",
            "file": "rtl/demo.sv",
            "params": {"W": "32'd32"},
        },
    ]

    def test_module_and_instance_scopes(self) -> None:
        design = build_design(
            self.MANIFEST, self.HIER, self.NETLIST, ({}, {}), REPO
        )
        top = design.scope("top")
        self.assertIsNotNone(top)
        assert top is not None
        self.assertEqual(top.module, "top")
        self.assertEqual([p.name for p in top.ports], ["out"])
        self.assertEqual([i.name for i in top.instances], ["u_core"])

        core = design.scope("top.u_core")
        self.assertIsNotNone(core)
        assert core is not None
        self.assertEqual(core.module, "core")
        self.assertEqual(core.params[0].value, "32'd32")

    def test_boundary_and_connectivity_built(self) -> None:
        design = build_design(
            self.MANIFEST, self.HIER, self.NETLIST, ({}, {}), REPO
        )
        self.assertIn(
            BoundaryLink(
                instance="u_core",
                port="out",
                parent_scope="top",
                child_scope="top.u_core",
            ),
            design.boundaries,
        )
        self.assertIn(
            ("top", "o"),
            [(c.scope, c.net) for c in design.connectivity],
        )

    def test_deterministic(self) -> None:
        a = build_design(self.MANIFEST, self.HIER, self.NETLIST, ({}, {}), REPO)
        b = build_design(self.MANIFEST, self.HIER, self.NETLIST, ({}, {}), REPO)
        self.assertEqual(a, b)


class SlangOverlayUnitTests(unittest.TestCase):
    """Pure-function tests for the slang decl overlay (card 219)."""

    def test_src_pos(self) -> None:
        self.assertEqual(_src_pos("rtl/demo.sv:45:38"), ("rtl/demo.sv", 45, 38))
        self.assertIsNone(_src_pos(None))
        self.assertIsNone(_src_pos("rtl/demo.sv:45"))
        self.assertIsNone(_src_pos("rtl/demo.sv:xx:38"))

    def test_type_string(self) -> None:
        packed = {"kind": "PackedArrayType", "range": "[31:0]",
                  "elementType": {"name": "logic", "kind": "ScalarType"}}
        self.assertEqual(_type_string(packed), "logic [31:0]")
        self.assertEqual(_type_string({"kind": "ScalarType", "name": "logic"}), "logic")
        self.assertEqual(_type_string({"kind": "PredefinedIntegerType", "name": "int"}), "int")
        self.assertIsNone(_type_string(None))
        self.assertIsNone(_type_string({"kind": "WeirdKind"}))

    def test_decl_default(self) -> None:
        lines = [
            "module core #(parameter int unsigned W = 8)",
            "",
            "  localparam logic [3:0] OP = 3;",
            "  parameter int unsigned wider = 2*W;",
        ]
        self.assertEqual(_decl_default(lines, "rtl/demo.sv:1:38"), "8")
        self.assertEqual(_decl_default(lines, "rtl/demo.sv:3:27"), "3")
        self.assertEqual(_decl_default(lines, "rtl/demo.sv:4:30"), "2*W")
        self.assertEqual(_decl_default(lines, "rtl/demo.sv:1:5"), "8")  # fallback: whole-line '='
        self.assertIsNone(_decl_default([], "rtl/demo.sv:1:38"))
        self.assertIsNone(_decl_default(lines, None))

    def test_decl_default_keeps_a_bracketed_expression(self) -> None:
        # HLS writes its width parameters as expressions. A '(' that the
        # default opens itself must not end it.
        lines = [
            "parameter C_S_AXI_WSTRB_WIDTH = (32 / 8);",
            "parameter NESTED = ((4 + 2) * 2);",
            "parameter PICK = WIDTHS[1:0];",
            "module m #(parameter int W = (8), parameter int V = 2)",
        ]
        self.assertEqual(_decl_default(lines, "f.v:1:11"), "(32 / 8)")
        self.assertEqual(_decl_default(lines, "f.v:2:11"), "((4 + 2) * 2)")
        self.assertEqual(_decl_default(lines, "f.v:3:11"), "WIDTHS[1:0]")
        # the ')' that closes the parameter port list is unbalanced: it ends
        # the default, and the ',' ends the first one
        self.assertEqual(_decl_default(lines, "f.v:4:26"), "(8)")
        self.assertEqual(_decl_default(lines, "f.v:4:49"), "2")

    def test_type_range(self) -> None:
        packed = {"kind": "PackedArrayType", "range": "[7:1]",
                  "elementType": {"name": "logic", "kind": "ScalarType"}}
        self.assertEqual(_type_range(packed), "[7:1]")
        self.assertEqual(_type_range({**packed, "range": "[0:7]"}), "[0:7]")
        self.assertIsNone(_type_range({"kind": "ScalarType", "name": "logic"}))
        self.assertIsNone(_type_range({**packed, "range": ""}))
        self.assertIsNone(_type_range(None))

    def test_nets_for_reads_the_declared_range(self) -> None:
        mod = {"netnames": {
            "nd": {"bits": [1, 2], "attributes": {"src": "f.sv:8"}},
            "unknown": {"bits": [3, 4], "attributes": {}},
        }}
        decl = {"nets": {"nd": {"range": "[15:8]", "type": "logic [15:8]"}}}
        nets = {n.name: n for n in _nets_for(mod, decl)}
        self.assertEqual(nets["nd"].range, "[15:8]")
        # a net the AST walk never reached keeps None and falls back to width
        self.assertIsNone(nets["unknown"].range)


class BitKeyTests(unittest.TestCase):
    """Yosys JSON uses non-integer tokens for constant / don't-care bits."""

    def test_bit_key(self) -> None:
        self.assertEqual(_bit_key(3), 3)
        self.assertEqual(_bit_key("0"), 0)
        self.assertEqual(_bit_key("x"), -1)
        self.assertEqual(_bit_key(None), -1)

    def test_nets_for_tolerates_constant_bits(self) -> None:
        mod = {"netnames": {"o": {"bits": [1, "0", "x"], "attributes": {"src": "f.sv:1"}}}}
        nets = _nets_for(mod)
        self.assertEqual(len(nets), 1)
        net = nets[0]
        self.assertEqual(net.width, 3)  # constant bits still occupy a slot
        self.assertEqual(net.bits, (1, 0, -1))


@unittest.skipUnless(
    shutil.which("yosys") and shutil.which("slang-hier") and shutil.which("slang"),
    "requires the nix dev shell (yosys/slang on PATH)",
)
class DemoAcceptanceTests(unittest.TestCase):
    """Card 208 acceptance: rtl/demo.sv -> IR with generate scopes, resolved
    params, black-box ports; deterministic and cached."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = Manifest.read(REPO / "rtl" / "demo.manifest.json")

    def test_acceptance_criteria(self) -> None:
        design = elaborate(self.manifest, REPO, cache=None)
        self.assertEqual(design.top, "top")
        top = design.scope("top")
        assert top is not None

        # generate scopes g[0]/g[1
        self.assertIsNotNone(design.scope("top.u_core.g[0]"))
        self.assertIsNotNone(design.scope("top.u_core.g[1]"))
        g0 = design.scope("top.u_core.g[0]")
        assert g0 is not None
        self.assertIsNone(g0.module)
        self.assertEqual([i.name for i in g0.instances], ["u_alu_g"])

        # resolved parameters W=32'd32 (parameter) + OP (localparam)
        core = design.scope("top.u_core")
        assert core is not None
        by_name = {p.name: p for p in core.params}
        self.assertEqual(by_name["W"].value, "32'd32")
        self.assertEqual(by_name["W"].kind, "parameter")
        self.assertEqual(by_name["OP"].kind, "localparam")

        # card 219: declared defaults + override at full fidelity
        self.assertEqual(by_name["W"].default, "8")
        self.assertTrue(by_name["W"].overridden, "W resolves 32 vs default 8")
        self.assertEqual(by_name["OP"].default, "3")
        self.assertFalse(by_name["OP"].overridden, "localparams cannot be overridden")
        alu0 = design.scope("top.u_core.u_alu0")
        assert alu0 is not None
        alu_w = {p.name: p for p in alu0.params}["W"]
        self.assertEqual((alu_w.value, alu_w.default), ("32'd32", "8"))
        self.assertTrue(alu_w.overridden)

        # card 219: source-faithful port types from slang --ast-json
        self.assertEqual(
            [(p.name, p.type) for p in top.ports],
            [("hi", "logic [31:0]"), ("lo", "logic [31:0]"), ("out", "logic [31:0]")],
        )
        op = {p.name: p for p in alu0.ports}["op"]
        self.assertEqual(op.type, "logic [3:0]")

        # black-box sram_stub with ports
        sram = design.scope("top.u_core.u_sram")
        assert sram is not None
        self.assertTrue(sram.blackbox)
        self.assertEqual([p.name for p in sram.ports], ["addr", "wdata", "we", "rdata"])

        # hierarchy wiring: u_core's children include generate + black box,
        # ordered by source line (u_alu0 @56, g @58, u_sram @67)
        self.assertEqual(
            [i.name for i in core.instances],
            ["u_alu0", "g[0]", "g[1]", "u_sram"],
        )

        # boundaries reach the generate child instance
        self.assertIn(
            BoundaryLink(
                instance="g[0].u_alu_g",
                port="result",
                parent_scope="top.u_core",
                child_scope="top.u_core.g[0].u_alu_g",
            ),
            design.boundaries,
        )

    def test_deterministic(self) -> None:
        a = elaborate(self.manifest, REPO, cache=None)
        b = elaborate(self.manifest, REPO, cache=None)
        self.assertEqual(a, b)

    def test_cache_hit_round_trips(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tren-elab-cache-") as td:
            cache = IrCache(Path(td) / "cache", root=REPO)
            first = elaborate(self.manifest, REPO, cache=cache)
            second = elaborate(self.manifest, REPO, cache=cache)
        self.assertEqual(first, second)

    def test_overlay_survives_the_cache(self) -> None:
        """Card 219 end-to-end: the overlay is only delivered if it survives
        a cache hit. It did not - blobs written before `Port.type` existed kept
        hitting, so the I/O pane showed no types on any warm cache."""
        with tempfile.TemporaryDirectory(prefix="tren-elab-overlay-") as td:
            cache = IrCache(Path(td) / "cache", root=REPO)
            elaborate(self.manifest, REPO, cache=cache)
            cached = elaborate(self.manifest, REPO, cache=cache)

        top = cached.scope("top")
        assert top is not None
        self.assertEqual(
            [(p.name, p.direction, p.type) for p in top.ports],
            [
                ("hi", "in", "logic [31:0]"),
                ("lo", "in", "logic [31:0]"),
                ("out", "out", "logic [31:0]"),
            ],
        )
        core = cached.scope("top.u_core")
        assert core is not None
        w = {p.name: p for p in core.params}["W"]
        self.assertEqual((w.value, w.default, w.kind), ("32'd32", "8", "parameter"))
        self.assertTrue(w.overridden)

    def test_scopes_are_connected(self) -> None:
        design = elaborate(self.manifest, REPO, cache=None)
        by_scope = {c.scope: c for c in design.connectivity}
        self.assertIn("alu", by_scope)
        self.assertGreater(len(design.connectivity), 0)
        self.assertGreater(len(design.boundaries), 0)

    def test_declared_ranges_reach_the_demo_ir(self) -> None:
        design = elaborate(self.manifest, REPO, cache=None)
        alu0 = design.scope("top.u_core.u_alu0")
        core = design.scope("top.u_core")
        assert alu0 is not None and core is not None
        self.assertEqual({p.name: p.range for p in alu0.ports}["op"], "[3:0]")
        nets = {n.name: n for n in core.nets}
        self.assertEqual(nets["x"].range, "[31:0]")
        # a generate block's net joins the two layers on the name yosys
        # gives it, 'g[0].gr'
        self.assertEqual(nets["g[0].gr"].range, "[31:0]")
        self.assertEqual(nets["g[1].gr"].range, "[31:0]")


@unittest.skipUnless(
    shutil.which("yosys") and shutil.which("slang-hier") and shutil.which("slang"),
    "requires the nix dev shell (yosys/slang on PATH)",
)
class DeclaredRangeAcceptanceTests(unittest.TestCase):
    """Card 219: the range a leaf paints is the one the source declares.

    yosys carries a bit count only, so the range used to be derived as
    [width-1:0] and every descending range that does not end at 0, and every
    ascending range, read wrong. rtl/ranges.sv holds exactly those cases.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.design = elaborate(
            Manifest.read(REPO / "rtl" / "ranges.manifest.json"), REPO, cache=None
        )

    def test_ports_keep_the_declared_range(self) -> None:
        scope = self.design.scope("ranges")
        assert scope is not None
        ports = {p.name: p for p in scope.ports}
        self.assertEqual((ports["down"].range, ports["down"].width), ("[7:1]", 7))
        self.assertEqual((ports["up"].range, ports["up"].width), ("[0:7]", 8))
        self.assertIsNone(ports["sc"].range, "a scalar declares no range")

    def test_nets_keep_the_declared_range(self) -> None:
        scope = self.design.scope("ranges")
        assert scope is not None
        nets = {n.name: n for n in scope.nets}
        self.assertEqual((nets["nd"].range, nets["nd"].width), ("[15:8]", 8))
        self.assertEqual((nets["nu"].range, nets["nu"].width), ("[0:3]", 4))
        self.assertIsNone(nets["ns"].range)

    def test_the_leaves_read_as_the_source_writes_them(self) -> None:
        scope = self.design.scope("ranges")
        assert scope is not None
        ports = {p.name: p for p in scope.ports}
        nets = {n.name: n for n in scope.nets}
        self.assertEqual(port_leaf_label(ports["down"]), "port in    down[7:1]")
        self.assertEqual(port_leaf_label(ports["up"]), "port in    up[0:7]")
        self.assertEqual(port_leaf_label(ports["sc"]), "port in    sc")
        self.assertEqual(net_leaf_label(nets["nd"]), "net nd[15:8]")
        self.assertEqual(net_leaf_label(nets["nu"]), "net nu[0:3]")


if __name__ == "__main__":
    unittest.main()
