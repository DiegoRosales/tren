"""Tests for inferred black boxes: the stub a design needs to elaborate.

Pure-function tests build a small AST by hand. The acceptance tests shell out
to slang/yosys and are skipped outside the nix dev shell.

Run:  nix develop . -c bash -c "PYTHONPATH=src python -m unittest discover -s tests"
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tren.cache import IrCache  # noqa: E402
from tren.elaborate import elaborate  # noqa: E402
from tren.manifest import Manifest  # noqa: E402
from tren.stubs import Stub, infer, main as stub_main, render, summary  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def _packed(lo: int, hi: int) -> dict:
    return {
        "kind": "PackedArrayType",
        "range": f"[{hi}:{lo}]",
        "elementType": {"kind": "ScalarType", "name": "logic"},
    }


def _value(width: int) -> dict:
    return {"kind": "NamedValue", "type": _packed(0, width - 1)}


def _instance(
    module: str,
    name: str = "u",
    ports: list[dict] | None = None,
    params: list[dict] | None = None,
) -> dict:
    return {
        "kind": "UninstantiatedDef",
        "name": name,
        "definitionName": module,
        "parameters": params or [],
        "ports": ports or [],
    }


def _ast(*instances: dict) -> dict:
    return {"design": {"members": list(instances)}}


class StubInferenceTests(unittest.TestCase):
    """What TREN reads back from an instantiation of an undeclared module."""

    def test_named_ports_and_widths(self) -> None:
        ast = _ast(
            _instance(
                "vendor_ip",
                ports=[
                    {"name": "a", "expr": _value(8)},
                    {"name": "y", "expr": _value(16)},
                ],
            )
        )
        stubs = infer(ast, REPO)
        self.assertEqual(sorted(stubs), ["vendor_ip"])
        self.assertEqual(
            dict(stubs["vendor_ip"].ports), {"a": 8, "y": 16}
        )
        self.assertEqual(stubs["vendor_ip"].params, ())

    def test_scalar_and_expression_widths(self) -> None:
        ast = _ast(
            _instance(
                "vendor_wide",
                ports=[
                    {"name": "sel", "expr": {"kind": "BinaryOp", "type": {"kind": "ScalarType"}}},
                    {"name": "data", "expr": _value(16)},
                ],
            )
        )
        ports = dict(infer(ast, REPO)["vendor_wide"].ports)
        self.assertEqual(ports, {"sel": 1, "data": 16})

    def test_positional_connection_gets_a_synthetic_name(self) -> None:
        ast = _ast(
            _instance(
                "vendor_pos",
                ports=[
                    {"name": None, "expr": _value(8)},
                    {"name": None, "expr": _value(4)},
                ],
            )
        )
        stub = infer(ast, REPO)["vendor_pos"]
        self.assertEqual(stub.ports, (("p0", 8), ("p1", 4)))
        self.assertIn("positional connection shown as p0", stub.notes)

    def test_empty_argument_is_one_bit(self) -> None:
        ast = _ast(
            _instance(
                "vendor_odd",
                ports=[{"name": "a", "expr": {"kind": "EmptyArgument", "type": {"kind": "VoidType"}}}],
            )
        )
        self.assertEqual(dict(infer(ast, REPO)["vendor_odd"].ports), {"a": 1})

    def test_assignment_pattern_width_comes_from_its_default_setter(self) -> None:
        # slang binds an untyped connection as Invalid with the real
        # expression underneath; '{default: 4'h0} keeps its width there.
        expr = {
            "kind": "Invalid",
            "type": {"kind": "ErrorType"},
            "child": {
                "kind": "StructuredAssignmentPattern",
                "type": {"kind": "ErrorType"},
                "defaultSetter": {"kind": "IntegerLiteral", "type": _packed(0, 3)},
            },
        }
        ast = _ast(_instance("vendor_odd", ports=[{"name": "b", "expr": expr}]))
        self.assertEqual(dict(infer(ast, REPO)["vendor_odd"].ports), {"b": 4})

    def test_invalid_wrapped_expression_keeps_its_type_width(self) -> None:
        # An Invalid wrapper around a plain expression carries no pattern,
        # so the width has to come from the child's own type. Missing it
        # would declare the port one bit wide and truncate the connection.
        expr = {
            "kind": "Invalid",
            "type": {"kind": "ErrorType"},
            "child": {"kind": "NamedValue", "type": _packed(0, 7)},
        }
        ast = _ast(_instance("vendor_odd", ports=[{"name": "a", "expr": expr}]))
        self.assertEqual(dict(infer(ast, REPO)["vendor_odd"].ports), {"a": 8})

    def test_two_instances_union_their_ports(self) -> None:
        ast = _ast(
            _instance("vendor_ip", "u0", [{"name": "a", "expr": _value(8)}]),
            _instance("vendor_ip", "u1", [{"name": "y", "expr": _value(16)}]),
        )
        self.assertEqual(
            dict(infer(ast, REPO)["vendor_ip"].ports), {"a": 8, "y": 16}
        )

    def test_parameter_names_come_from_the_source_text(self) -> None:
        # The AST carries parameter values in order but never their names, so
        # the instantiation site is the only place to read them.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = (
                "module top;\n"
                "  vendor_ip #(\n"
                "    .W(8),\n"
                "    .MODE(\"fast\")\n"
                "  ) u_ip (.a(), .y());\n"
                "endmodule\n"
            )
            (root / "top.sv").write_text(source)
            ast = _ast(
                _instance(
                    "vendor_ip",
                    ports=[{"name": "a", "expr": {"kind": "EmptyArgument", "type": {"kind": "VoidType"}}}],
                    params=[{"kind": "Invalid"}, {"kind": "Invalid"}],
                )
            )
            node = ast["design"]["members"][0]
            node["source_file"] = str(root / "top.sv")
            node["source_line"] = 5
            node["source_column"] = 5  # 'u_ip' on line 5
            stub = infer(ast, root)["vendor_ip"]
            self.assertEqual(stub.params, ("W", "MODE"))

    def test_parameter_names_allow_whitespace_before_the_paren(self) -> None:
        # '#' and '(' may be separated by whitespace in SystemVerilog.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = (
                "module top;\n"
                "  vendor_ip # (\n"
                "    .W(8)\n"
                "  ) u_ip (.a());\n"
                "endmodule\n"
            )
            (root / "top.sv").write_text(source)
            ast = _ast(
                _instance(
                    "vendor_ip",
                    ports=[{"name": "a", "expr": {"kind": "EmptyArgument", "type": {"kind": "VoidType"}}}],
                    params=[{"kind": "Invalid"}],
                )
            )
            node = ast["design"]["members"][0]
            node["source_file"] = str(root / "top.sv")
            node["source_line"] = 4
            node["source_column"] = 5  # 'u_ip' on line 4
            stub = infer(ast, root)["vendor_ip"]
            self.assertEqual(stub.params, ("W",))

    def test_missing_parameter_names_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "top.sv").write_text("module top; endmodule\n")
            ast = _ast(_instance("vendor_ip", params=[{"kind": "Invalid"}]))
            node = ast["design"]["members"][0]
            node["source_file"] = str(root / "top.sv")
            node["source_line"] = 1
            node["source_column"] = 1
            stub = infer(ast, root)["vendor_ip"]
            self.assertEqual(stub.params, ())
            self.assertTrue(any("parameter names" in n for n in stub.notes))

    def test_no_undeclared_module_means_no_stub(self) -> None:
        self.assertEqual(infer({"design": {"members": []}}, REPO), {})

    def test_render_emits_a_blackbox_module(self) -> None:
        text = render(
            {
                "vendor_ip": Stub(
                    module="vendor_ip",
                    ports=(("a", 8), ("sel", 1)),
                    params=("W",),
                )
            },
            source="demo",
        )
        self.assertIn("(* blackbox *)", text)
        self.assertIn(
            "module vendor_ip #(parameter int W = 0) "
            "(input logic [7:0] a, input logic [0:0] sel);",
            text,
        )
        self.assertIn("// design: demo", text)

    def test_summary_names_every_module(self) -> None:
        stubs = {
            "a": Stub(module="a"),
            "b": Stub(module="b", notes=("positional connection shown as p0",)),
        }
        text = summary(stubs, Path("/tmp/stubs.sv"))
        assert text is not None
        self.assertIn("no declaration for 2 module(s)", text)
        self.assertIn("a, b", text)
        self.assertIn("/tmp/stubs.sv", text)
        self.assertIn("b: positional connection shown as p0", text)
        self.assertIsNone(summary({}))


@unittest.skipUnless(
    shutil.which("yosys") and shutil.which("slang-hier") and shutil.which("slang"),
    "requires the nix dev shell (yosys/slang on PATH)",
)
class InferredBlackboxAcceptanceTests(unittest.TestCase):
    """A design with an SV construct and a missing module now elaborates."""

    MANIFEST = REPO / "tests" / "sv" / "missing_rtl.manifest.json"

    def test_design_elaborates_with_the_box_inferred(self) -> None:
        manifest = Manifest.read(self.MANIFEST)
        warnings: list[str] = []
        design = elaborate(
            manifest, REPO, cache=None, warnings_out=warnings
        )
        boxes = {s.name: s for s in design.scopes if s.blackbox}
        self.assertEqual(
            sorted(boxes), ["missing_rtl.u_ip", "missing_rtl.u_pos"]
        )
        self.assertTrue(all(scope.unresolved for scope in boxes.values()))
        self.assertEqual(
            dict((p.name, p.width) for p in boxes["missing_rtl.u_ip"].ports),
            {"din": 8, "dout": 8, "sel": 1},
        )
        self.assertEqual(
            dict((p.name, p.width) for p in boxes["missing_rtl.u_pos"].ports),
            {"p0": 8, "p1": 4},
        )
        self.assertTrue(warnings)
        self.assertIn("vendor_ip, vendor_pos", warnings[0])

    def test_box_port_directions_stay_unknown(self) -> None:
        # The stub declares every port an input so that any expression is
        # accepted. TREN must not let that guess reach the trace.
        manifest = Manifest.read(self.MANIFEST)
        design = elaborate(manifest, REPO, cache=None)
        box = design.scope("missing_rtl.u_ip")
        assert box is not None
        self.assertEqual({p.direction for p in box.ports}, {"unknown"})
        conn = {c.net: c for c in design.connectivity if c.scope == "missing_rtl"}
        self.assertTrue(any(link.unknown for link in conn["q"].bits))

    def test_stub_file_lands_in_the_cache(self) -> None:
        manifest = Manifest.read(self.MANIFEST)
        with tempfile.TemporaryDirectory() as td:
            cache = IrCache(td, root=REPO)
            warnings: list[str] = []
            elaborate(
                manifest, REPO, cache=cache, refresh=True, warnings_out=warnings
            )
            path = cache.stub_path(manifest)
            self.assertTrue(path.is_file())
            self.assertIn(str(path), warnings[0])
            text = path.read_text()
            self.assertIn("module vendor_ip", text)
            self.assertIn("module vendor_pos", text)
            # the IR blob for the same inputs sits beside it
            self.assertTrue(cache.path(manifest).is_file())

    def test_a_design_read_verilog_parses_writes_no_stub(self) -> None:
        manifest = Manifest.read(REPO / "tests" / "rtl" / "demo.manifest.json")
        with tempfile.TemporaryDirectory() as td:
            cache = IrCache(td, root=REPO)
            warnings: list[str] = []
            design = elaborate(
                manifest, REPO, cache=cache, refresh=True, warnings_out=warnings
            )
            self.assertFalse(cache.stub_path(manifest).exists())
            self.assertEqual(warnings, [])
            self.assertIn("read_verilog", design.created_by or "")


@unittest.skipUnless(
    shutil.which("slang") and shutil.which("slang-hier"),
    "requires the nix dev shell (slang on PATH)",
)
class StubCommandTests(unittest.TestCase):
    def test_writes_the_stub_for_a_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "stubs.sv"
            code = stub_main([
                str(REPO / "tests" / "sv" / "missing_rtl.manifest.json"),
                "--root", str(REPO),
                "--out", str(out),
            ])
            self.assertEqual(code, 0)
            text = out.read_text()
            self.assertIn("module vendor_ip", text)

    def test_a_complete_design_reports_nothing_to_do(self) -> None:
        code = stub_main([
            str(REPO / "tests" / "rtl" / "demo.manifest.json"),
            "--root", str(REPO),
        ])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
