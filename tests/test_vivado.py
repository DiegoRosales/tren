"""Unit tests for the Vivado importer. Pure text/XML.

Run:  nix develop . -c bash -c "PYTHONPATH=src python -m unittest discover -s tests"
"""

from __future__ import annotations

import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tren.importer import detect_format, import_manifest  # noqa: E402
from tren.vivado import _parse_xpr_file_lists, parse_vivado  # noqa: E402


def write_xpr(
    dir_path: Path, *, top: str | None = "bd_0_wrapper", files: list[str]
) -> Path:
    xpr = dir_path / "project.xpr"
    sets = []
    for f in files:
        sets.append(f'      <File Path="{f}">\n        <FileInfo/>\n      </File>')
    top_opt = f"\n        <Option Name=\"TopModule\" Val=\"{top}\"/>" if top else ""
    text = f"""<?xml version="1.0" encoding="UTF-8"?>
<Project Product="Vivado" Version="7" Minor="71" Path="{xpr}">
  <Configuration>
    <Option Name="XSimRadix" Val="hex"/>
    <Option Name="XPMLibraries" Val="XPM_MEMORY"/>
  </Configuration>
  <FileSets Version="1" Minor="32">
    <FileSet Name="sources_1" Type="DesignSrcs" RelSrcDir="$PSRCDIR/sources_1" RelGenDir="$PGENDIR/sources_1">
      <Filter Type="Srcs"/>
      {chr(10).join(sets)}
      <Config>
        <Option Name="DesignMode" Val="RTL"/>
        {top_opt}
        <Option Name="TopAutoSet" Val="TRUE"/>
      </Config>
    </FileSet>
    <FileSet Name="constrs_1" Type="Constrs" RelSrcDir="$PSRCDIR/constrs_1">
      <Config><Option Name="ConstrsType" Val="XDC"/></Config>
    </FileSet>
  </FileSets>
  <Runs Version="1" Minor="22">
    <Run Id="synth_1" Type="Ft3:Synth" SrcSet="sources_1" Description="Vivado Synthesis Defaults">
      <Strategy Version="1" Minor="2">
        <StratHandle Name="Vivado Synthesis Defaults" Flow="Vivado Synthesis 2025"/>
        <Step Id="synth_design"><Option Id="MoreOptsStr"><![CDATA[-mode out_of_context]]></Option></Step>
      </Strategy>
    </Run>
  </Runs>
</Project>
"""
    xpr.write_text(text)
    return xpr


class ParseXprTests(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.mkdtemp(prefix="tren-vivado-")
        self.root = Path(self.td)
        (self.root / "rtl").mkdir()
        (self.root / "rtl" / "core.v").write_text("module core; endmodule\n")
        (self.root / "rtl" / "top.sv").write_text("module top; endmodule\n")

    def test_file_sets_sources_and_top(self) -> None:
        gen = self.root / "project.srcs/sources_1/new"
        gen.mkdir(parents=True)
        (gen / "core.v").write_text("module core; endmodule\n")
        xpr = write_xpr(
            self.root,
            files=["$PSRCDIR/sources_1/new/core.v", "$PPRDIR/rtl/top.sv"],
        )
        warnings: list[str] = []
        sources, top = _parse_xpr_file_lists(xpr, warnings)
        self.assertEqual(
            sorted(p.resolve() for p in sources),
            sorted(
                [self.root / "project.srcs/sources_1/new/core.v",
                 self.root / "rtl/top.sv"]
            ),
        )
        self.assertEqual(top, "bd_0_wrapper")
        self.assertEqual(warnings, [])

    def test_non_verilog_dropped_with_warning(self) -> None:
        (self.root / "bd_0.bd").write_text("<BlockDesign/>")
        xpr = write_xpr(
            self.root,
            files=["$PPRDIR/bd_0.bd", "$PPRDIR/rtl/top.sv"],
        )
        warnings: list[str] = []
        sources, _ = _parse_xpr_file_lists(xpr, warnings)
        self.assertEqual([p.name for p in sources], ["top.sv"])
        self.assertTrue(any("non-Verilog" in w for w in warnings), warnings)

    def test_missing_file_warns(self) -> None:
        xpr = write_xpr(self.root, files=["$PPRDIR/rtl/gone.v"])
        warnings: list[str] = []
        sources, _ = _parse_xpr_file_lists(xpr, warnings)
        self.assertEqual(sources, [])
        self.assertTrue(any("missing" in w for w in warnings), warnings)

    def test_broken_xpr_raises(self) -> None:
        xpr = self.root / "project.xpr"
        xpr.write_text("<Project")
        with self.assertRaises(ValueError):
            _parse_xpr_file_lists(xpr, [])

    def test_relative_and_absolute_paths(self) -> None:
        xpr = write_xpr(
            self.root,
            files=[str(self.root / "rtl" / "top.sv"), "rtl/core.v"],
        )
        warnings: list[str] = []
        sources, _ = _parse_xpr_file_lists(xpr, warnings)
        self.assertEqual(len(sources), 2)


class ParseVivadoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.mkdtemp(prefix="tren-vivado-")
        self.root = Path(self.td)
        (self.root / "rtl").mkdir()
        (self.root / "rtl" / "core.v").write_text("module core; endmodule\n")
        write_xpr(self.root, files=["$PPRDIR/rtl/core.v"])

    def test_header_fileset_entry_feeds_incdirs(self) -> None:
        """A Verilog-header FileSet entry supplies an incdir, not a
        source."""
        (self.root / "rtl" / "inc" / "defs.svh").parent.mkdir(parents=True)
        (self.root / "rtl" / "inc" / "defs.svh").write_text("`define FOO 1\n")
        xpr = write_xpr(
            self.root,
            files=["$PPRDIR/rtl/inc/defs.svh", "$PPRDIR/rtl/core.v"],
        )
        warnings: list[str] = []
        incdirs: list[str] = []
        sources, _ = _parse_xpr_file_lists(xpr, warnings, incdirs_out=incdirs)
        self.assertEqual([p.name for p in sources], ["core.v"])
        self.assertEqual(
            [str(p) for p in incdirs], [str(self.root / "rtl" / "inc")]
        )
        self.assertEqual(warnings, [])

    def test_manifest_incdirs_default_to_source_dirs(self) -> None:
        """The manifest records the source directories as incdirs
        (yosys needs them as -I); header entries add their own dirs."""
        (self.root / "rtl" / "inc").mkdir()
        (self.root / "rtl" / "inc" / "defs.svh").write_text("`define FOO 1\n")
        write_xpr(
            self.root,
            files=["$PPRDIR/rtl/inc/defs.svh", "$PPRDIR/rtl/core.v"],
        )
        manifest = import_manifest(self.root, root=self.root)
        self.assertIn("rtl", manifest.incdirs)
        self.assertIn("rtl/inc", manifest.incdirs)
        self.assertNotIn("rtl/inc/defs.svh", manifest.sources)

    def test_root_source_dir_feeds_dot_incdir(self) -> None:
        """A source at the root records ``.`` (slang does not
        search the cwd)."""
        (self.root / "top_root.v").write_text("module top_root; endmodule\n")
        manifest = import_manifest(self.root, root=self.root)
        self.assertIn(".", manifest.incdirs)

    def test_manifest_from_directory_with_xpr(self) -> None:
        self.assertEqual(detect_format(self.root), "vivado")
        manifest = import_manifest(self.root, root=self.root, fmt="auto")
        self.assertEqual(manifest.top, "bd_0_wrapper")
        self.assertEqual(set(manifest.sources), {"rtl/core.v"})
        self.assertEqual(manifest.name, self.root.name)

    def test_manifest_from_xpr_file(self) -> None:
        self.assertEqual(detect_format(self.root / "project.xpr"), "vivado")
        manifest = import_manifest(self.root / "project.xpr", root=self.root)
        self.assertEqual(manifest.top, "bd_0_wrapper")
        self.assertIn("rtl/core.v", manifest.sources)

    def test_top_override(self) -> None:
        manifest = import_manifest(self.root, root=self.root, top="core")
        self.assertEqual(manifest.top, "core")

    def test_no_xpr_falls_back_to_plain_import(self) -> None:
        plain = self.root / "plain"
        plain.mkdir()
        (plain / "a.sv").write_text("module a; endmodule\n")
        self.assertEqual(detect_format(plain), "f")
        manifest = import_manifest(plain, root=self.root)
        self.assertEqual(manifest.sources, ("plain/a.sv",))

    def test_recorded_top_from_settings_tcl(self) -> None:
        write_xpr(self.root, top=None, files=[])
        (self.root / "settings.tcl").write_text("set top_module fft_wrapper\n")
        manifest = import_manifest(self.root, root=self.root)
        self.assertEqual(manifest.top, "fft_wrapper")
        self.assertEqual(manifest.sources, ("rtl/core.v",))  # from the dir scan

    def test_directory_scan_completes_batch_projects(self) -> None:
        (self.root / "fft_wrapper.v").write_text("module fft_wrapper; endmodule\n")
        (self.root / "fft_wrapper_glue.vh").write_text("/* include-only */\n")
        manifest = import_manifest(self.root, root=self.root)
        self.assertIn("fft_wrapper.v", manifest.sources)
        # .vh are `include`-files pulled in by their parent .v — never
        # standalone compile units (Vitis HLS ships them next to the RTL)
        self.assertNotIn("fft_wrapper_glue.vh", manifest.sources)


if __name__ == "__main__":
    unittest.main()