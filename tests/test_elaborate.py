"""Tests for the elaboration runner.

Parser unit tests run without tools; the acceptance integration test shells
out to yosys/slang-hier/slang and is skipped outside the nix dev shell.

Run:  nix develop . -c bash -c "PYTHONPATH=src python -m unittest discover -s tests"
"""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tren.cache import IrCache  # noqa: E402
from tren.config import Config  # noqa: E402
from tren.importer import import_manifest  # noqa: E402
from tren.elaborate import (  # noqa: E402
    _bit_key,
    _build_connectivity,
    _decl_default,
    _effective_incdirs,
    _module_base,
    _net_type_string,
    _nets_for,
    _parse_ast_decls,
    _src_pos,
    _tool_diagnostics,
    _type_range,
    _type_string,
    build_design,
    elaborate,
    main as elaborate_main,
    module_key,
    parse_hier_line,
    run_slang_ast,
    run_slang_hier,
    run_yosys,
)
from tren.ir import (  # noqa: E402
    BoundaryLink,
    Design,
    Endpoint,
    NetConnectivity,
    Port,
    Scope,
    parse_src,
)
from tren.manifest import Manifest  # noqa: E402
from tren.tui import net_leaf_label, port_leaf_label  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


class HierParseTests(unittest.TestCase):
    def test_line_with_params(self) -> None:
        node = parse_hier_line(
            'Module="core" Instance="top.u_core" File="tests/rtl/demo.sv" '
            "Parameters: W=32'd32, OP=4'b11"
        )
        self.assertIsNotNone(node)
        assert node is not None
        self.assertEqual(node["inst"], "top.u_core")
        self.assertEqual(node["module"], "core")
        self.assertEqual(node["params"], {"W": "32'd32", "OP": "4'b11"})

    def test_line_without_params(self) -> None:
        node = parse_hier_line('Module="top" Instance="top" File="tests/rtl/demo.sv"')
        self.assertIsNotNone(node)
        assert node is not None
        self.assertEqual(node["params"], {})

    def test_generate_instance_path_kept(self) -> None:
        node = parse_hier_line(
            'Module="alu" Instance="top.u_core.g[1].u_alu_g" File="tests/rtl/demo.sv"'
        )
        assert node is not None
        self.assertEqual(node["inst"], "top.u_core.g[1].u_alu_g")

    def test_non_matching_line_is_none(self) -> None:
        self.assertIsNone(parse_hier_line("Build succeeded: 0 errors, 0 warnings"))


class BackendFlagTests(unittest.TestCase):
    """Manifest flags reach every runner, without running tools."""

    MANIFEST = Manifest(
        sources=("tests/rtl/demo.sv",),
        defines=(("SYNTHESIS", "1"),),
        params=(("WIDTH", "16"),),
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
        self.assertIn("-D", cmd)
        self.assertIn("SYNTHESIS=1", cmd)
        self.assertIn("-G", cmd)
        self.assertIn("WIDTH=16", cmd)
        self.assertIn("tests/rtl/demo.sv", cmd)

    def test_slang_ast_carries_defaults_and_manifest_flags(self) -> None:
        cmd = self._capture(
            lambda: run_slang_ast(self.MANIFEST, REPO, "/tmp/ast.json")
        )[0]
        self.assertIn("--ignore-unknown-modules", cmd)
        self.assertIn("--timescale=1ns/1ps", cmd)
        self.assertIn("--ast-json-source-info", cmd)
        self.assertIn("-D", cmd)
        self.assertIn("SYNTHESIS=1", cmd)
        self.assertIn("-G", cmd)
        self.assertIn("WIDTH=16", cmd)

    def test_yosys_carries_defaults_and_manifest_flags(self) -> None:
        script = self._capture(
            lambda: run_yosys(self.MANIFEST, REPO, "/tmp/netlist.json")
        )[0][3]
        self.assertIn(
            "read_verilog -sv -nolatches -DEXTRA=1 -DSYNTHESIS=1 "
            "-Itests/rtl tests/rtl/demo.sv",
            script,
        )
        self.assertIn("chparam -set WIDTH 16 top; hierarchy -top top", script)
        self.assertIn("proc;", script)

    def test_yosys_has_no_chparam_without_overrides(self) -> None:
        bare = Manifest(sources=("tests/rtl/demo.sv",))
        script = self._capture(
            lambda: run_yosys(bare, REPO, "/tmp/netlist.json")
        )[0][3]
        self.assertNotIn("chparam", script)

    def test_duplicate_parameters_use_the_last_value(self) -> None:
        manifest = Manifest(
            sources=("tests/rtl/demo.sv",),
            params=(("WIDTH", "8"), ("WIDTH", "16")),
        )
        hier = self._capture(lambda: run_slang_hier(manifest, REPO))[0]
        self.assertIn("WIDTH=16", hier)
        self.assertNotIn("WIDTH=8", hier)
        ast = self._capture(
            lambda: run_slang_ast(manifest, REPO, "/tmp/ast.json")
        )[0]
        self.assertIn("WIDTH=16", ast)
        self.assertNotIn("WIDTH=8", ast)
        script = self._capture(
            lambda: run_yosys(manifest, REPO, "/tmp/netlist.json")
        )[0][3]
        self.assertIn("chparam -set WIDTH 16 top", script)
        self.assertNotIn("chparam -set WIDTH 8", script)

    def test_duplicate_defines_use_the_last_value(self) -> None:
        manifest = Manifest(
            sources=("tests/rtl/demo.sv",),
            defines=(("W", "8"), ("W", "16")),
        )
        hier = self._capture(lambda: run_slang_hier(manifest, REPO))[0]
        self.assertIn("-D", hier)
        self.assertIn("W=16", hier)
        self.assertNotIn("W=8", hier)
        ast = self._capture(
            lambda: run_slang_ast(manifest, REPO, "/tmp/ast.json")
        )[0]
        self.assertIn("W=16", ast)
        self.assertNotIn("W=8", ast)
        script = self._capture(
            lambda: run_yosys(manifest, REPO, "/tmp/netlist.json")
        )[0][3]
        self.assertIn("-DW=16", script)
        self.assertNotIn("-DW=8", script)

    def test_effective_incdirs_adds_source_dirs(self) -> None:
        """A hand-written manifest listing only sources gets the
        source directories, including '.' for a root source."""
        manifest = Manifest(sources=("top.sv", "rtl/sub.sv"), incdirs=("inc",))
        self.assertEqual(_effective_incdirs(manifest), (".", "inc", "rtl"))

    def test_default_slags_named_not_forced_off(self) -> None:
        bare = Manifest(sources=("tests/rtl/demo.sv",))
        cmd = self._capture(lambda: run_slang_hier(bare, REPO))[0]
        self.assertIn("--ignore-unknown-modules", cmd)
        self.assertNotIn("--timescale", cmd)


class FrontendSelectionTests(unittest.TestCase):
    """Which yosys frontend the netlist pass tries, and in what order."""

    MANIFEST = Manifest(
        sources=("tests/rtl/demo.sv",),
        defines=(("SYNTHESIS", "1"),),
        params=(("WIDTH", "16"),),
        slang_flags=("--ignore-unknown-modules", "--timescale=1ns/1ps"),
        yosys_flags=("-DEXTRA=1",),
    )

    def _run_with(self, failing: str | None = None):
        """Run run_yosys with a fake tool; return (frontend, scripts)."""
        import tren.elaborate as elab

        scripts: list[str] = []

        def fake_run(cmd, cwd):
            script = cmd[3]
            scripts.append(script)
            if failing is not None and script.startswith(failing):
                raise subprocess.CalledProcessError(1, cmd, output="", stderr="boom")
            return ""

        original = elab._run
        try:
            elab._run = fake_run
            used = run_yosys(self.MANIFEST, REPO, "/tmp/netlist.json")
        finally:
            elab._run = original
        return used, scripts

    def test_plain_design_stays_on_read_verilog(self) -> None:
        used, scripts = self._run_with()
        self.assertEqual(used, "verilog")
        self.assertEqual(len(scripts), 1)
        self.assertTrue(scripts[0].startswith("read_verilog -sv"))
        self.assertIn("chparam -set WIDTH 16 top; hierarchy -top top", scripts[0])
        self.assertIn("proc;", scripts[0])

    def test_read_verilog_failure_falls_back_to_read_slang(self) -> None:
        used, scripts = self._run_with(failing="read_verilog")
        self.assertEqual(used, "slang")
        self.assertEqual(len(scripts), 2)
        self.assertTrue(scripts[1].startswith("read_slang "))
        self.assertIn("hierarchy -top top; proc;", scripts[1])

    def test_slang_script_keeps_the_hierarchy(self) -> None:
        _, scripts = self._run_with(failing="read_verilog")
        script = scripts[1]
        self.assertIn("--keep-hierarchy", script)
        self.assertIn("--empty-blackboxes", script)
        # sv-elab hard-errors on this flag; it must not reach read_slang
        self.assertNotIn("--ignore-unknown-modules", script)
        # a read_verilog-only flag must not reach it either
        self.assertNotIn("-nolatches", script)
        self.assertNotIn("chparam", script)

    def test_slang_script_carries_slang_flags_defines_and_params(self) -> None:
        _, scripts = self._run_with(failing="read_verilog")
        script = scripts[1]
        self.assertIn("--timescale=1ns/1ps", script)
        self.assertIn("-D", script)
        self.assertIn("SYNTHESIS=1", script)
        self.assertIn("-G", script)
        self.assertIn("WIDTH=16", script)
        self.assertIn("-I tests/rtl", script)

    def test_forced_frontend_does_not_fall_back(self) -> None:
        manifest = Manifest(sources=("tests/rtl/demo.sv",), frontend="slang")
        import tren.elaborate as elab

        scripts: list[str] = []

        def fake_run(cmd, cwd):
            scripts.append(cmd[3])
            return ""

        original = elab._run
        try:
            elab._run = fake_run
            used = run_yosys(manifest, REPO, "/tmp/netlist.json")
        finally:
            elab._run = original
        self.assertEqual(used, "slang")
        self.assertTrue(scripts[0].startswith("read_slang "))

    def test_both_frontends_failing_raises_the_last_error(self) -> None:
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            self._run_with(failing="read_")
        self.assertIn("read_slang", caught.exception.cmd[3])

    def test_slang_script_puts_options_before_the_files(self) -> None:
        import tren.elaborate as elab

        scripts: list[str] = []

        def fake_run(cmd, cwd):
            scripts.append(cmd[3])
            return ""

        original = elab._run
        try:
            elab._run = fake_run
            run_yosys(
                self.MANIFEST,
                REPO,
                "/tmp/netlist.json",
                "slang",
                lambda: "/tmp/stubs.sv",
            )
        finally:
            elab._run = original
        script = scripts[0]
        self.assertTrue(script.startswith("read_slang --keep-hierarchy"))
        self.assertLess(
            script.index("/tmp/stubs.sv"), script.index("tests/rtl/demo.sv")
        )


class ModuleKeyTests(unittest.TestCase):
    """The two netlist naming schemes, and the join to a scope path."""

    def test_module_base_of_a_paramod_key(self) -> None:
        self.assertEqual(
            _module_base("$paramod\\core\\W=s32'00000000000000000000000000100000"),
            "core",
        )

    def test_module_base_of_a_read_slang_key(self) -> None:
        self.assertEqual(_module_base("core$top.u_core"), "core")
        self.assertEqual(_module_base("alu$top.u_core.g[0].u_alu_g"), "alu")
        self.assertEqual(_module_base("top"), "top")

    def test_module_key_prefers_the_instance_copy(self) -> None:
        modules = {"top": {}, "alu$top.u0": {}, "alu$top.u1": {}}
        self.assertEqual(module_key(modules, "alu", "top.u0"), "alu$top.u0")
        self.assertEqual(module_key(modules, "alu", "top.u1"), "alu$top.u1")

    def test_module_key_accepts_a_shared_module(self) -> None:
        modules = {"top": {}, "sram_stub": {}}
        self.assertEqual(module_key(modules, "sram_stub", "top.u_sram"), "sram_stub")
        self.assertEqual(module_key(modules, "top", "top"), "top")

    def test_module_key_uses_the_base_map_for_mangled_names(self) -> None:
        modules = {"top": {}, "$paramod\\core\\W=s32'00001000": {}}
        base_to_key = {"core": "$paramod\\core\\W=s32'00001000"}
        self.assertEqual(
            module_key(modules, "core", "top.u_core", base_to_key),
            "$paramod\\core\\W=s32'00001000",
        )
        self.assertIsNone(module_key(modules, "absent", "top.u_x", base_to_key))


class ElaborateMainWarningTests(unittest.TestCase):
    def test_duplicate_defines_and_params_warn(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "manifest.json"
            Manifest(
                name="d",
                sources=("a.sv",),
                defines=(("W", "8"), ("W", "16")),
                params=(("P", "8"), ("P", "16")),
                top="top",
            ).write(path)
            err = io.StringIO()
            with mock.patch(
                "tren.elaborate.load_config",
                return_value=mock.Mock(config=Config()),
            ), mock.patch(
                "tren.elaborate.elaborate", return_value=Design(top="top")
            ) as build, redirect_stderr(err), redirect_stdout(io.StringIO()):
                code = elaborate_main([str(path)])
        self.assertEqual(code, 0)
        self.assertIn("macro W is defined more than once", err.getvalue())
        self.assertIn("parameter P is defined more than once", err.getvalue())
        built = build.call_args.args[0]
        self.assertEqual(built.defines, (("W", "16"),))
        self.assertEqual(built.params, (("P", "16"),))


class DiagnosticTests(unittest.TestCase):
    """A failed backend shows its stderr, not a raw argv."""

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

    MANIFEST = Manifest(name="demo", sources=("tests/rtl/demo.sv",), top="top")

    NETLIST = {
        "modules": {
            "top": {
                "attributes": {"src": "tests/rtl/demo.sv:70.1-78.10"},
                "ports": {
                    "out": {"direction": "output", "bits": [66, 67, 68]},
                },
                "netnames": {"o": {"bits": [2, 3], "attributes": {"src": "tests/rtl/demo.sv:75"}}},
                "cells": {
                    "u_core": {
                        "type": r"$paramod\core\W=s32'32",
                        "attributes": {"src": "tests/rtl/demo.sv:77"},
                        "port_directions": {"a": "input", "out": "output"},
                        "connections": {"out": [66, 67, 68]},
                    }
                },
            },
            r"$paramod\core\W=s32'32": {
                "attributes": {"src": "tests/rtl/demo.sv:45.1-68.11"},
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
        {"inst": "top", "module": "top", "file": "tests/rtl/demo.sv", "params": {}},
        {
            "inst": "top.u_core",
            "module": "core",
            "file": "tests/rtl/demo.sv",
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


@unittest.skipUnless(
    shutil.which("yosys") and shutil.which("slang-hier") and shutil.which("slang"),
    "requires the nix dev shell (yosys/slang on PATH)",
)
class UnknownDirectionAcceptanceTests(unittest.TestCase):
    """An unresolved IP keeps its direction unknown through elaboration.

    `vendor_ip` has no definition in the fixture's source set, so yosys
    emits no port_directions. The endpoint must reach the IR as `unknown`,
    not as a guessed driver or load (see tests/rtl/unknown_direction.sv).
    """

    def test_unresolved_instance_reaches_the_ir_as_unknown(self) -> None:
        manifest = Manifest.read(
            REPO / "tests" / "rtl" / "unknown_direction.manifest.json"
        )
        design = elaborate(manifest, REPO, cache=None)
        scope = design.scope("unknown_direction")
        assert scope is not None
        cell = {c.name: c for c in scope.cells}["u_ip"]
        self.assertEqual({pin.direction for pin in cell.pins}, {"unknown"})
        self.assertEqual(
            [(i.name, i.module, i.blackbox) for i in scope.instances],
            [("u_ip", "vendor_ip", True)],
        )
        missing = design.scope("unknown_direction.u_ip")
        assert missing is not None
        self.assertTrue(missing.unresolved)
        conn = {
            c.net: c for c in design.connectivity if c.scope == "unknown_direction"
        }
        link = conn["y"].bits[0]
        self.assertEqual((link.drivers, link.loads), ((), ()))
        self.assertEqual(
            [(endpoint.cell, endpoint.port) for endpoint in link.unknown],
            [("u_ip", "dout")],
        )


@unittest.skipUnless(
    shutil.which("yosys") and shutil.which("slang-hier") and shutil.which("slang"),
    "requires the nix dev shell (yosys/slang on PATH)",
)
class ParameterOverrideAcceptanceTests(unittest.TestCase):
    """A top-level override reaches both backends and the IR."""

    def test_override_changes_the_port_width_and_range(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "param_top.sv").write_text(
                "module top #(parameter int WIDTH = 8)\n"
                "  (input logic [WIDTH-1:0] a, output logic [WIDTH-1:0] y);\n"
                "  assign y = a;\n"
                "endmodule\n"
            )
            manifest = Manifest(
                name="param_top",
                sources=("param_top.sv",),
                params=(("WIDTH", "16"),),
                top="top",
            )
            design = elaborate(manifest, root, cache=None)
        scope = design.scope("top")
        assert scope is not None
        ports = {port.name: port for port in scope.ports}
        self.assertEqual((ports["a"].width, ports["y"].width), (16, 16))
        self.assertEqual(ports["a"].range, "[15:0]")
        self.assertEqual(ports["y"].range, "[15:0]")
        self.assertIn("16", {p.name: p.value for p in scope.params}["WIDTH"])


@unittest.skipUnless(
    shutil.which("yosys") and shutil.which("slang-hier") and shutil.which("slang"),
    "requires the nix dev shell (yosys/slang on PATH)",
)
class DefineAcceptanceTests(unittest.TestCase):
    """A macro define reaches both backends and the IR."""

    def test_define_changes_the_port_width(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "define_top.sv").write_text(
                "module top\n"
                "  (input logic [`TOPW-1:0] a, output logic [`TOPW-1:0] y);\n"
                "  assign y = a;\n"
                "endmodule\n"
            )
            manifest = Manifest(
                name="define_top",
                sources=("define_top.sv",),
                defines=(("TOPW", "16"),),
                top="top",
            )
            design = elaborate(manifest, root, cache=None)
        scope = design.scope("top")
        assert scope is not None
        ports = {port.name: port for port in scope.ports}
        self.assertEqual((ports["a"].width, ports["y"].width), (16, 16))
        self.assertEqual(ports["a"].range, "[15:0]")
        self.assertEqual(ports["y"].range, "[15:0]")


class SlangOverlayUnitTests(unittest.TestCase):
    """Pure-function tests for the slang decl overlay."""

    def test_src_pos(self) -> None:
        self.assertEqual(_src_pos("tests/rtl/demo.sv:45:38"), ("tests/rtl/demo.sv", 45, 38))
        self.assertIsNone(_src_pos(None))
        self.assertIsNone(_src_pos("tests/rtl/demo.sv:45"))
        self.assertIsNone(_src_pos("tests/rtl/demo.sv:xx:38"))

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
        self.assertEqual(_decl_default(lines, "tests/rtl/demo.sv:1:38"), "8")
        self.assertEqual(_decl_default(lines, "tests/rtl/demo.sv:3:27"), "3")
        self.assertEqual(_decl_default(lines, "tests/rtl/demo.sv:4:30"), "2*W")
        self.assertEqual(_decl_default(lines, "tests/rtl/demo.sv:1:5"), "8")  # fallback: whole-line '='
        self.assertIsNone(_decl_default([], "tests/rtl/demo.sv:1:38"))
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

    def test_nets_for_reads_the_declared_type_and_range(self) -> None:
        mod = {"netnames": {
            "nd": {"bits": [1, 2], "attributes": {"src": "f.sv:8"}},
            "unknown": {"bits": [3, 4], "attributes": {}},
        }}
        decl = {"nets": {"nd": {"range": "[15:8]", "type": "logic [15:8]"}}}
        nets = {n.name: n for n in _nets_for(mod, decl)}
        self.assertEqual(nets["nd"].type, "logic [15:8]")
        self.assertEqual(nets["nd"].range, "[15:8]")
        # a net the AST walk never reached keeps None and falls back to width
        self.assertIsNone(nets["unknown"].type)
        self.assertIsNone(nets["unknown"].range)

    def test_net_type_string_reads_the_declared_keyword(self) -> None:
        packed = {
            "kind": "PackedArrayType",
            "elementType": {"name": "logic"},
            "range": "[3:0]",
        }
        wire = {"kind": "Net", "type": packed, "netType": {"name": "wire"}}
        reg = {
            "kind": "Variable",
            "type": {**packed, "elementType": {"name": "reg"}},
        }
        integer = {"kind": "Variable", "type": {"kind": "PredefinedIntegerType", "name": "integer"}}
        self.assertEqual(_net_type_string(wire), "wire [3:0]")
        self.assertEqual(_net_type_string(reg), "reg [3:0]")
        self.assertEqual(_net_type_string(integer), "integer")


def _decl_ast_instance(module: str, ports: list[str]) -> dict:
    """One top-level Instance as slang --ast-json spells it."""
    return {
        "kind": "Instance",
        "name": module,
        "body": {
            "kind": "InstanceBody",
            "name": module,
            "members": [
                {
                    "kind": "Port",
                    "name": port,
                    "direction": "In",
                    "source_file": f"rtl/{module}.sv",
                    "source_line": i + 2,  # below the module header line
                    "source_column": 12,
                    "type": {"kind": "ScalarType", "name": "logic"},
                }
                for i, port in enumerate(ports)
            ],
        },
    }


class AstDeclWalkTests(unittest.TestCase):
    """The slang decl overlay walks every top-level instance."""

    DESIGN = {
        "design": {
            "members": [
                {"kind": "CompilationUnit", "name": "", "members": []},
                _decl_ast_instance("other", ["x"]),
                _decl_ast_instance("t", ["clk", "rst", "data"]),
            ]
        }
    }

    def test_declarations_of_every_top_level_module(self) -> None:
        # slang lists one Instance per top-level module. A source set whose
        # modules no single top reaches has several; the second one used to
        # be skipped, so its ports carried no src to highlight.
        decls, _ = _parse_ast_decls(self.DESIGN)
        self.assertEqual(sorted(decls), ["other", "t"])

    def test_ports_carry_the_declaration_span(self) -> None:
        decls, _ = _parse_ast_decls(self.DESIGN)
        self.assertEqual(decls["t"]["ports"]["clk"]["src"], "rtl/t.sv:2:12")
        # the TUI focuses the line the port name sits on
        self.assertEqual(
            parse_src(decls["t"]["ports"]["data"]["src"]), ("rtl/t.sv", 4, 4)
        )


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


class ConnectivityDirectionTests(unittest.TestCase):
    """A cell whose module the netlist lacks has unknown port directions.

    yosys emits no ``port_directions`` for an instance of an unresolved
    module (an IP with no stub in the source set). The endpoint must not be
    guessed into drivers or loads: it lands in ``BitLink.unknown`` and the
    trace shows it in both directions, marked.
    """

    def test_unresolved_instance_lands_in_unknown(self) -> None:
        netlist = {
            "modules": {
                "top": {
                    "cells": {
                        "u_fifo": {
                            "type": "vendor_fifo",
                            "connections": {"dout": [1]},
                        }
                    },
                    "netnames": {"y": {"bits": [1]}},
                }
            }
        }
        conn = _build_connectivity(netlist, {"top": "top"})
        link = conn[0].bits[0]
        self.assertEqual(link.drivers, ())
        self.assertEqual(link.loads, ())
        self.assertEqual(
            link.unknown, (Endpoint(cell="u_fifo", port="dout", bit=0),)
        )

    def test_known_directions_stay_split(self) -> None:
        netlist = {
            "modules": {
                "top": {
                    "cells": {
                        "u_alu": {
                            "type": "alu",
                            "port_directions": {"a": "input", "y": "output"},
                            "connections": {"a": [2], "y": [1]},
                        }
                    },
                    "netnames": {"y": {"bits": [1]}, "a": {"bits": [2]}},
                },
                "alu": {
                    "ports": {
                        "a": {"direction": "input"},
                        "y": {"direction": "output"},
                    }
                },
            }
        }
        conn = {c.net: c for c in _build_connectivity(netlist, {"top": "top"})}
        self.assertEqual(conn["y"].bits[0].drivers, (Endpoint("u_alu", "y", 0),))
        self.assertEqual(conn["a"].bits[0].loads, (Endpoint("u_alu", "a", 0),))


@unittest.skipUnless(
    shutil.which("yosys") and shutil.which("slang-hier") and shutil.which("slang"),
    "requires the nix dev shell (yosys/slang on PATH)",
)
class DemoAcceptanceTests(unittest.TestCase):
    """tests/rtl/demo.sv -> IR with generate scopes, resolved
    params, black-box ports; deterministic and cached."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = Manifest.read(REPO / "tests" / "rtl" / "demo.manifest.json")

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

        # declared defaults + override at full fidelity
        self.assertEqual(by_name["W"].default, "8")
        self.assertTrue(by_name["W"].overridden, "W resolves 32 vs default 8")
        self.assertEqual(by_name["OP"].default, "3")
        self.assertFalse(by_name["OP"].overridden, "localparams cannot be overridden")
        alu0 = design.scope("top.u_core.u_alu0")
        assert alu0 is not None
        alu_w = {p.name: p for p in alu0.params}["W"]
        self.assertEqual((alu_w.value, alu_w.default), ("32'd32", "8"))
        self.assertTrue(alu_w.overridden)

        # source-faithful port types from slang --ast-json
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

    def test_missing_module_becomes_blackbox_scope(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tren-missing-module-") as td:
            root = Path(td)
            (root / "missing_module.sv").write_text(
                "module top (input logic clk, input logic [7:0] addr, "
                "output logic [31:0] rdata);\n"
                "  ram_model u_ram(.clk(clk), .addr(addr), .rdata(rdata));\n"
                "endmodule\n"
            )
            manifest = Manifest(
                name="missing_module",
                sources=("missing_module.sv",),
                top="top",
            )
            design = elaborate(manifest, root, cache=None)
        top = design.scope("top")
        missing = design.scope("top.u_ram")
        assert top is not None
        assert missing is not None

        self.assertEqual(
            [(i.name, i.module, i.blackbox) for i in top.instances],
            [("u_ram", "ram_model", True)],
        )
        self.assertTrue(missing.blackbox)
        self.assertTrue(missing.unresolved)
        self.assertEqual(
            [(p.name, p.direction, p.width) for p in missing.ports],
            [
                ("addr", "unknown", 8),
                ("clk", "unknown", 1),
                ("rdata", "unknown", 32),
            ],
        )
        self.assertIn("u_ram", {cell.name for cell in top.cells})
        round_trip = Design.from_json(design.to_json())
        round_trip_missing = round_trip.scope("top.u_ram")
        assert round_trip_missing is not None
        self.assertTrue(round_trip_missing.unresolved)

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
        """The overlay is only delivered if it survives
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

    def test_cells_carry_pins_and_source(self) -> None:
        design = elaborate(self.manifest, REPO, cache=None)
        core = design.scope("top.u_core")
        alu0 = design.scope("top.u_core.u_alu0")
        assert core is not None and alu0 is not None
        xor = {cell.type: cell for cell in core.cells}["$xor"]
        self.assertEqual(
            [(pin.name, pin.direction, pin.width) for pin in xor.pins],
            [("A", "in", 32), ("B", "in", 32), ("Y", "out", 32)],
        )
        self.assertIn(":64", xor.src or "")
        # a submodule instance is a scope, not a cell row
        self.assertNotIn("u_alu0", {cell.name for cell in core.cells})
        self.assertEqual(
            {cell.type for cell in alu0.cells}
            & {"$pmux", "$eq", "$logic_not"},
            {"$pmux", "$eq", "$logic_not"},
        )
        # the proc intermediate wires stay traceable: the mux inputs are
        # `$...` nets with no source-level name
        names = {c.net for c in design.connectivity if c.scope == "alu"}
        self.assertTrue(any(n.startswith("$procmux") for n in names), names)

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
class GenerateAcceptanceTests(unittest.TestCase):
    """The active generate branches become scopes; inactive and zero-width
    branches do not. Leaf instances keep the yosys dotted cell path and the
    instantiation source span, even when a generate block is between them."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = Manifest.read(REPO / "tests" / "rtl" / "generate.manifest.json")
        cls.design = elaborate(cls.manifest, REPO, cache=None)

    def test_active_generate_blocks_are_scopes(self) -> None:
        names = {scope.name for scope in self.design.scopes}
        self.assertTrue(
            {
                "gen_top.garr[0]",
                "gen_top.garr[1]",
                "gen_top.g_if",
                "gen_top.genblk3",
                "gen_top.g_case0",
                "gen_top.g_outer[0]",
                "gen_top.g_outer[0].g_inner",
                "gen_top.g_decl",
            }
            <= names,
            names,
        )
        top = self.design.scope("gen_top")
        assert top is not None
        self.assertEqual(
            [inst.name for inst in top.instances],
            [
                "garr[0]",
                "garr[1]",
                "g_if",
                "genblk3",
                "g_case0",
                "g_outer[0]",
                "g_decl",
            ],
        )
        self.assertTrue(all(inst.module is None for inst in top.instances))

        nested = self.design.scope("gen_top.g_outer[0]")
        inner = self.design.scope("gen_top.g_outer[0].g_inner")
        decl = self.design.scope("gen_top.g_decl")
        assert nested is not None and inner is not None and decl is not None
        self.assertEqual([inst.name for inst in nested.instances], ["g_inner"])
        self.assertEqual([inst.name for inst in inner.instances], ["u_nested"])
        self.assertEqual(decl.instances, ())

    def test_inactive_and_zero_width_generates_are_absent(self) -> None:
        names = {scope.name for scope in self.design.scopes}
        for absent in (
            "gen_top.g_else",
            "gen_top.g_case_def",
            "gen_top.g_zero",
        ):
            with self.subTest(scope=absent):
                self.assertNotIn(absent, names)

    def test_generate_source_spans(self) -> None:
        expected = {
            "gen_top.garr[0]": 21,
            "gen_top.garr[1]": 21,
            "gen_top.g_if": 26,
            "gen_top.genblk3": 33,
            "gen_top.g_case0": 39,
            "gen_top.g_outer[0]": 48,
            "gen_top.g_outer[0].g_inner": 49,
            "gen_top.g_decl": 59,
        }
        for name, line in expected.items():
            with self.subTest(scope=name):
                scope = self.design.scope(name)
                assert scope is not None
                self.assertEqual(
                    parse_src(scope.src), ("tests/rtl/generate.sv", line, line)
                )

    def test_leaf_instances_keep_the_cell_path_and_instantiation_span(self) -> None:
        expected = {
            "gen_top.garr[0].u_leaf": 23,
            "gen_top.garr[1].u_leaf": 23,
            "gen_top.g_if.u_if": 28,
            "gen_top.genblk3.u_anon": 35,
            "gen_top.g_case0.u_case0": 41,
            "gen_top.g_outer[0].g_inner.u_nested": 51,
        }
        for name, line in expected.items():
            with self.subTest(scope=name):
                scope = self.design.scope(name)
                assert scope is not None
                parent = self.design.scope(name.rsplit(".", 1)[0])
                assert parent is not None
                inst = {i.name: i for i in parent.instances}[name.rsplit(".", 1)[1]]
                self.assertEqual(
                    parse_src(inst.src), ("tests/rtl/generate.sv", line, line)
                )
        boundary_instances = {link.instance for link in self.design.boundaries}
        self.assertTrue(
            {
                "garr[0].u_leaf",
                "garr[1].u_leaf",
                "g_if.u_if",
                "genblk3.u_anon",
                "g_case0.u_case0",
                "g_outer[0].g_inner.u_nested",
            }
            <= boundary_instances,
            boundary_instances,
        )

    def test_parameter_dependent_branches_follow_each_instance(self) -> None:
        source = """\
module leaf(input logic a, output logic y);
  assign y = a;
endmodule

module cond #(parameter int USE = 1)
  (input  logic a,
   output logic y);

  if (USE) begin : g_on
    leaf u_on(.a(a), .y(y));
  end else begin : g_off
    leaf u_off(.a(a), .y(y));
  end
endmodule

module cond_top
  (input  logic a,
   output logic y1,
   output logic y0);

  cond #(.USE(1)) u_on0(.a(a), .y(y1));
  cond #(.USE(0)) u_off0(.a(a), .y(y0));
endmodule
"""
        with tempfile.TemporaryDirectory(prefix="tren-generate-param-") as td:
            root = Path(td)
            (root / "cond.sv").write_text(source)
            design = elaborate(
                Manifest(name="cond", sources=("cond.sv",), top="cond_top"),
                root,
                cache=None,
            )
        names = {scope.name for scope in design.scopes}
        self.assertTrue(
            {"cond_top.u_on0.g_on", "cond_top.u_off0.g_off"} <= names, names
        )
        self.assertNotIn("cond_top.u_on0.g_off", names)
        self.assertNotIn("cond_top.u_off0.g_on", names)

    def test_local_nets_and_parameter_overrides_reach_the_ir(self) -> None:
        top = self.design.scope("gen_top")
        assert top is not None
        nets = {net.name: net for net in top.nets}
        for name in (
            "garr[0].t",
            "garr[1].t",
            "g_if.t",
            "genblk3.t",
            "g_case0.t",
            "g_outer[0].g_inner.t",
            "g_decl.only",
        ):
            with self.subTest(net=name):
                self.assertEqual(nets[name].range, "[7:0]")
        for name in (
            "gen_top.garr[0].u_leaf",
            "gen_top.g_if.u_if",
            "gen_top.genblk3.u_anon",
            "gen_top.g_case0.u_case0",
            "gen_top.g_outer[0].g_inner.u_nested",
        ):
            with self.subTest(scope=name):
                scope = self.design.scope(name)
                assert scope is not None
                param = {p.name: p for p in scope.params}["W"]
                self.assertEqual(param.value, "8")
                self.assertEqual(param.default, "4")
                self.assertTrue(param.overridden)


@unittest.skipUnless(
    shutil.which("yosys") and shutil.which("slang-hier") and shutil.which("slang"),
    "requires the nix dev shell (yosys/slang on PATH)",
)
class SvConstructAcceptanceTests(unittest.TestCase):
    """A design using constructs `read_verilog` cannot parse elaborates.

    The netlist pass falls back to `read_slang`, whose module names carry the
    instance path. The scope tree, the ports and the boundary links must come
    out the same shape `read_verilog` gives.
    """

    def test_assignment_pattern_and_streaming_operator(self) -> None:
        manifest = Manifest.read(REPO / "tests" / "sv" / "sv_constructs.manifest.json")
        design = elaborate(manifest, REPO, cache=None)
        self.assertIn("read_slang", design.created_by or "")
        names = {scope.name for scope in design.scopes}
        self.assertEqual(
            names, {"sv_constructs", "sv_constructs.u_leaf"}
        )
        top = design.scope("sv_constructs")
        assert top is not None
        self.assertEqual(
            [p.name for p in top.ports], ["clk", "d", "q", "packed_out"]
        )
        leaf = design.scope("sv_constructs.u_leaf")
        assert leaf is not None
        self.assertEqual(leaf.module, "sv_leaf")
        self.assertEqual([p.name for p in leaf.ports], ["a", "y"])
        self.assertEqual(len(leaf.nets), 2)
        self.assertEqual(
            [(b.parent_scope, b.instance, b.port) for b in design.boundaries
             if b.instance == "u_leaf"],
            [
                ("sv_constructs", "u_leaf", "a"),
                ("sv_constructs", "u_leaf", "y"),
            ],
        )

    def test_a_parsable_design_stays_on_read_verilog(self) -> None:
        manifest = Manifest.read(REPO / "tests" / "rtl" / "demo.manifest.json")
        design = elaborate(manifest, REPO, cache=None)
        self.assertIn("read_verilog", design.created_by or "")

    def test_unknown_module_become_an_inferred_black_box(self) -> None:
        # sv-elab has no tolerance for an undeclared module. TREN infers a
        # declaration from the instantiation so the rest stays explorable.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "mixed.sv").write_text(
                "module mixed (input logic [7:0] d, output logic [7:0] q);\n"
                "  assign q = {<<8{d}};\n"
                "  vendor_ip u_ip (.a(d), .y());\n"
                "endmodule\n"
            )
            manifest = Manifest(sources=("mixed.sv",), top="mixed")
            warnings: list[str] = []
            design = elaborate(
                manifest, root, cache=None, warnings_out=warnings
            )
        box = design.scope("mixed.u_ip")
        assert box is not None
        self.assertTrue(box.blackbox)
        self.assertTrue(box.unresolved)
        # The box is read back from the cell, so a port with no connection
        # ('.y()') does not appear; 'a' is the one that carries bits.
        self.assertEqual([p.name for p in box.ports], ["a"])
        self.assertEqual({p.direction for p in box.ports}, {"unknown"})
        self.assertTrue(warnings)
        # No cache means the stub lives in a temporary directory that is
        # already gone, so the warning must not name a dead path.
        self.assertNotIn("stub:", warnings[0])


@unittest.skipUnless(
    shutil.which("yosys") and shutil.which("slang-hier") and shutil.which("slang"),
    "requires the nix dev shell (yosys/slang on PATH)",
)
class IncludeElaborationAcceptanceTests(unittest.TestCase):
    """A manifest from a source tree whose `` `include `` lives in a
    sibling source directory elaborates through both backends.

    The source-dir incdir default is what makes yosys find it: yosys 0.69
    resolves a *same-directory* include natively but needs ``-I`` for one in
    another source dir (verified). slang resolves against the including file's
    directory and needs the incdir too once the header moves out of it.
    """

    def _tree(self) -> tuple[Path, Path]:
        td = tempfile.mkdtemp(prefix="tren-inc-")
        root = Path(td)
        (root / "rtl").mkdir()
        (root / "rtl" / "inc").mkdir()
        (root / "rtl" / "inc" / "incparams.svh").write_text("`define INC_W 8\n")
        (root / "rtl" / "inc" / "helper.sv").write_text("module helper; endmodule\n")
        (root / "rtl" / "incdata.sv").write_text(
            "`include \"incparams.svh\"\n"
            "module incdata #(parameter int W = `INC_W) (\n"
            "  input  logic [W-1:0] i,\n"
            "  output logic [W-1:0] o\n"
            ");\n"
            "  assign o = i;\n"
            "endmodule\n"
        )
        return td, root

    def test_manifest_from_sibling_include_tree_elaborates(self) -> None:
        td, root = self._tree()
        try:
            manifest = import_manifest(root / "rtl", root=root, top="incdata")
            self.assertIn("rtl/inc", manifest.incdirs)
            design = elaborate(manifest, root, cache=None)
            self.assertEqual(design.top, "incdata")
            scope = design.scope("incdata")
            self.assertIsNotNone(scope, "incdata must survive elaboration")
            helper = design.scope("helper")
            self.assertIsNotNone(helper)
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_root_include_from_subdirectory_elaborates(self) -> None:
        """Slang does not search the cwd, so a source at the root
        records ``.`` and a root header resolves from a subdirectory."""
        td = tempfile.mkdtemp(prefix="tren-inc-root-")
        root = Path(td)
        try:
            (root / "rtl").mkdir()
            (root / "defs.svh").write_text("`define ROOT_W 8\n")
            (root / "top.sv").write_text("module top; sub u(); endmodule\n")
            (root / "rtl" / "sub.sv").write_text(
                "`include \"defs.svh\"\n"
                "module sub; logic [`ROOT_W-1:0] x; endmodule\n"
            )
            manifest = import_manifest(root, root=root, top="top")
            self.assertIn(".", manifest.incdirs)
            design = elaborate(manifest, root, cache=None)
            self.assertIsNotNone(design.scope("top.u"))
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_handwritten_manifest_gets_source_dir_floor(self) -> None:
        """The elaborator applies the source-dir floor, so a
        manifest that only lists sources needs no manual incdir."""
        td, root = self._tree()
        try:
            manifest = Manifest(
                name="incdata",
                sources=("rtl/inc/helper.sv", "rtl/incdata.sv"),
                top="incdata",
            )
            design = elaborate(manifest, root, cache=None)
            self.assertIsNotNone(design.scope("incdata"))
        finally:
            shutil.rmtree(td, ignore_errors=True)


@unittest.skipUnless(
    shutil.which("yosys") and shutil.which("slang-hier") and shutil.which("slang"),
    "requires the nix dev shell (yosys/slang on PATH)",
)
class MultiRootDeclAcceptanceTests(unittest.TestCase):
    """An inlined port declaration is highlightable in a multi-root source set.

    Two modules that no single top reaches make slang --ast-json list two
    design members. Reading only the first left the top's ports without a src
    span, so selecting a port focused nothing that the source pane could
    highlight.
    """

    def test_ports_of_a_second_root_carry_a_source_span(self) -> None:
        td = tempfile.mkdtemp(prefix="tren-roots-")
        root = Path(td)
        try:
            (root / "rtl").mkdir()
            (root / "rtl" / "a_other.sv").write_text(
                "module other(input wire x, output wire y);\n"
                "  assign y = x;\n"
                "endmodule\n"
            )
            (root / "rtl" / "t.sv").write_text(
                "module t(\n"
                "input wire clk,\n"
                "input wire rst,\n"
                "output wire data\n"
                ");\n"
                "  assign data = clk & rst;\n"
                "endmodule\n"
            )
            manifest = import_manifest(root / "rtl", root=root, top="t")
            design = elaborate(manifest, root, cache=None)
            self.assertIsNotNone(design.scope("other"))  # the second root
            scope = design.scope("t")
            self.assertIsNotNone(scope)
            assert scope is not None
            spans = {p.name: parse_src(p.src) for p in scope.ports}
            self.assertEqual(spans["clk"], ("rtl/t.sv", 2, 2))
            self.assertEqual(spans["rst"], ("rtl/t.sv", 3, 3))
            self.assertEqual(spans["data"], ("rtl/t.sv", 4, 4))
        finally:
            shutil.rmtree(td, ignore_errors=True)


@unittest.skipUnless(
    shutil.which("yosys") and shutil.which("slang-hier") and shutil.which("slang"),
    "requires the nix dev shell (yosys/slang on PATH)",
)
class DeclaredRangeAcceptanceTests(unittest.TestCase):
    """The range a leaf paints is the one the source declares.

    yosys carries a bit count only, so the range used to be derived as
    [width-1:0] and every descending range that does not end at 0, and every
    ascending range, read wrong. tests/rtl/ranges.sv holds exactly those cases.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.design = elaborate(
            Manifest.read(REPO / "tests" / "rtl" / "ranges.manifest.json"), REPO, cache=None
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
        self.assertEqual(port_leaf_label(ports["down"]), "port in    logic [7:1] down")
        self.assertEqual(port_leaf_label(ports["up"]), "port in    logic [0:7] up")
        self.assertEqual(port_leaf_label(ports["sc"]), "port in    logic sc")
        self.assertEqual(net_leaf_label(nets["nd"]), "logic [15:8] nd")
        self.assertEqual(net_leaf_label(nets["nu"]), "logic [0:3] nu")
        self.assertEqual(net_leaf_label(nets["ns"]), "logic ns")


if __name__ == "__main__":
    unittest.main()
