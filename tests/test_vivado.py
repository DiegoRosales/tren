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

from tren.importer import (  # noqa: E402
    IMPORT_SLANG_FLAGS,
    detect_format,
    import_manifest,
)
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


def write_component_xml(
    dir_path: Path, file_sets: dict[str, list[str]]
) -> Path:
    """Write a minimal Vivado IP component XML with the given file sets."""
    xml = dir_path / "component.xml"
    sets = []
    for name, files in file_sets.items():
        entries = "".join(
            f"<spirit:file><spirit:name>{path}</spirit:name></spirit:file>"
            for path in files
        )
        sets.append(
            f"<spirit:fileSet><spirit:name>{name}</spirit:name>"
            f"{entries}</spirit:fileSet>"
        )
    xml.write_text(
        "<spirit:component "
        'xmlns:spirit="http://www.spiritconsortium.org/XMLSchema/SPIRIT/1685-2009">'
        f"<spirit:fileSets>{''.join(sets)}</spirit:fileSets>"
        "</spirit:component>\n"
    )
    return xml


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

    def test_import_manifest_reports_parser_warnings(self) -> None:
        """Parser warnings reach the caller through the manifest import,
        not only through VivadoProject.warnings."""
        (self.root / "bd_0.bd").write_text("<BlockDesign/>")
        write_xpr(
            self.root,
            files=["$PPRDIR/bd_0.bd", "$PPRDIR/rtl/core.v"],
        )
        warnings: list[str] = []
        import_manifest(self.root, root=self.root, warnings_out=warnings)
        self.assertTrue(any("non-Verilog" in w for w in warnings), warnings)

    def test_manifest_from_xpr_file(self) -> None:
        self.assertEqual(detect_format(self.root / "project.xpr"), "vivado")
        manifest = import_manifest(self.root / "project.xpr", root=self.root)
        self.assertEqual(manifest.top, "bd_0_wrapper")
        self.assertIn("rtl/core.v", manifest.sources)

    def test_manifest_carries_slang_tolerance_flags(self) -> None:
        """A Vivado import is openable: Vivado-legal constructs that slang
        rejects by default stay accepted without a hand-written manifest."""
        manifest = import_manifest(self.root, root=self.root)
        self.assertEqual(manifest.slang_flags, IMPORT_SLANG_FLAGS)

    def test_top_override(self) -> None:
        manifest = import_manifest(self.root, root=self.root, top="core")
        self.assertEqual(manifest.top, "core")

    def test_parameter_overrides_keep_last_and_warn(self) -> None:
        write_xpr(self.root, files=["$PPRDIR/rtl/core.v"])
        warnings: list[str] = []
        manifest = import_manifest(
            self.root,
            root=self.root,
            params=[("W", "8"), ("W", "16")],
            warnings_out=warnings,
        )
        self.assertEqual(manifest.params, (("W", "16"),))
        self.assertTrue(
            any("parameter W is defined more than once" in w for w in warnings),
            warnings,
        )

    def test_defines_keep_last_and_warn(self) -> None:
        write_xpr(self.root, files=["$PPRDIR/rtl/core.v"])
        warnings: list[str] = []
        manifest = import_manifest(
            self.root,
            root=self.root,
            defines=[("W", "8"), ("W", "16")],
            warnings_out=warnings,
        )
        self.assertEqual(manifest.defines, (("W", "16"),))
        self.assertTrue(
            any("macro W is defined more than once" in w for w in warnings),
            warnings,
        )

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

    def test_directory_scan_skips_simulation_models(self) -> None:
        """The scan keeps the synthesis tree and drops every simulation
        representation of the same design unit."""
        bd = self.root / "bd" / "top"
        (bd / "synth").mkdir(parents=True)
        (bd / "sim").mkdir()
        (bd / "synth" / "top.v").write_text("module top; endmodule\n")
        (bd / "sim" / "top.v").write_text("module top; endmodule\n")

        ip = bd / "ip" / "core"
        (ip / "synth").mkdir(parents=True)
        (ip / "sim").mkdir()
        (ip / "synth" / "core_ip.v").write_text("module core_ip; endmodule\n")
        (ip / "sim" / "core_ip.v").write_text("module core_ip; endmodule\n")
        (ip / "core_ip_sim_netlist.v").write_text(
            "module core_ip; endmodule\n"
        )
        (ip / "core_ip_stub.v").write_text("module core_ip; endmodule\n")

        shared = bd / "ipshared" / "abc"
        (shared / "hdl").mkdir(parents=True)
        (shared / "simulation").mkdir()
        (shared / "hdl" / "rtl.v").write_text("module rtl; endmodule\n")
        (shared / "simulation" / "model.v").write_text(
            "module model; endmodule\n"
        )

        manifest = import_manifest(self.root, root=self.root)
        self.assertIn("bd/top/synth/top.v", manifest.sources)
        self.assertIn("bd/top/ip/core/synth/core_ip.v", manifest.sources)
        self.assertIn("bd/top/ipshared/abc/hdl/rtl.v", manifest.sources)
        for path in manifest.sources:
            self.assertNotIn("/sim/", path)
            self.assertNotIn("/simulation/", path)
            self.assertFalse(path.endswith("_sim_netlist.v"), path)
            self.assertFalse(path.endswith("_stub.v"), path)

    def test_directory_scan_keeps_simulation_without_synth_counterpart(self) -> None:
        """A sim model with no synthesis sibling is the only model."""
        bd = self.root / "bd" / "top"
        (bd / "sim").mkdir(parents=True)
        (bd / "sim" / "orphan.v").write_text("module orphan; endmodule\n")

        shared = bd / "ipshared" / "abc"
        (shared / "simulation").mkdir(parents=True)
        (shared / "simulation" / "only.v").write_text(
            "module only; endmodule\n"
        )

        vhdl = bd / "ipshared" / "vhd"
        (vhdl / "hdl").mkdir(parents=True)
        (vhdl / "hdl" / "model.vhd").write_text("-- VHDL\n")
        (vhdl / "simulation").mkdir()
        (vhdl / "simulation" / "model.v").write_text(
            "module model; endmodule\n"
        )

        manifest = import_manifest(self.root, root=self.root)
        self.assertIn("bd/top/sim/orphan.v", manifest.sources)
        self.assertIn(
            "bd/top/ipshared/abc/simulation/only.v", manifest.sources
        )
        self.assertIn(
            "bd/top/ipshared/vhd/simulation/model.v", manifest.sources
        )

    def test_directory_scan_keeps_a_stub_outside_ip(self) -> None:
        """A hand-written stub is a source; only the generated IP stubs go."""
        (self.root / "rtl" / "my_stub.v").write_text(
            "module my_stub; endmodule\n"
        )
        manifest = import_manifest(self.root, root=self.root)
        self.assertIn("rtl/my_stub.v", manifest.sources)

    def test_component_xml_drops_behavioral_only_source(self) -> None:
        """A behavioral-only copy loses to the synthesis copy."""
        hdl = self.root / "ipshared" / "21f8" / "hdl"
        hdl.mkdir(parents=True)
        (hdl / "axi_dwidth_converter_v2_1_vl_rfs.v").write_text(
            "module axi_dwidth_converter_v2_1_37_top; endmodule\n"
        )
        (hdl / "axi_dwidth_converter_v2_1_vlsyn_rfs.v").write_text(
            "module axi_dwidth_converter_v2_1_37_top; endmodule\n"
        )
        ip = self.root / "ip" / "core"
        ip.mkdir(parents=True)
        write_component_xml(
            ip,
            {
                "xilinx_verilogbehavioralsimulation_view_fileset": [
                    "../../ipshared/21f8/hdl/"
                    "axi_dwidth_converter_v2_1_vl_rfs.v"
                ],
                "xilinx_verilogsynthesis_view_fileset": [
                    "../../ipshared/21f8/hdl/"
                    "axi_dwidth_converter_v2_1_vlsyn_rfs.v"
                ],
            },
        )
        manifest = import_manifest(self.root, root=self.root)
        self.assertIn(
            "ipshared/21f8/hdl/axi_dwidth_converter_v2_1_vlsyn_rfs.v",
            manifest.sources,
        )
        self.assertNotIn(
            "ipshared/21f8/hdl/axi_dwidth_converter_v2_1_vl_rfs.v",
            manifest.sources,
        )

    def test_component_xml_skips_bad_encoding(self) -> None:
        """A non-component XML with a bad encoding does not abort the scan."""
        (self.root / "bad.xml").write_text(
            '<?xml version="1.0" encoding="bogus"?><component/>\n'
        )
        manifest = import_manifest(self.root, root=self.root)
        self.assertIn("rtl/core.v", manifest.sources)

    def test_component_xml_keeps_behavioral_when_synthesis_is_vhdl(self) -> None:
        """The behavioral model is the only Verilog model for a VHDL IP."""
        hdl = self.root / "ipshared" / "42f3" / "hdl"
        hdl.mkdir(parents=True)
        (hdl / "blk_mem_gen_v8_4_vhsyn_rfs.vhd").write_text("-- VHDL\n")
        sim = self.root / "ipshared" / "42f3" / "simulation"
        sim.mkdir()
        (sim / "blk_mem_gen_v8_4.v").write_text(
            "module blk_mem_gen_v8_4; endmodule\n"
        )
        ip = self.root / "ip" / "core"
        ip.mkdir(parents=True)
        write_component_xml(
            ip,
            {
                "xilinx_verilogbehavioralsimulation_xilinx_com_ip_"
                "blk_mem_gen_8_4__ref_view_fileset": [
                    "../../ipshared/42f3/simulation/blk_mem_gen_v8_4.v"
                ],
                "xilinx_verilogsynthesis_xilinx_com_ip_"
                "blk_mem_gen_8_4__ref_view_fileset": [
                    "../../ipshared/42f3/hdl/"
                    "blk_mem_gen_v8_4_vhsyn_rfs.vhd"
                ],
            },
        )
        manifest = import_manifest(self.root, root=self.root)
        self.assertIn(
            "ipshared/42f3/simulation/blk_mem_gen_v8_4.v",
            manifest.sources,
        )

    def test_component_xml_drops_unpaired_behavioral_model(self) -> None:
        """A behavioral set with no synthesis pair is simulation-only."""
        vip = self.root / "ipshared" / "9a25" / "hdl"
        vip.mkdir(parents=True)
        (vip / "processing_system7_vip_v1_0_vl_rfs.sv").write_text(
            "module processing_system7_vip; endmodule\n"
        )
        ip = self.root / "ip" / "core"
        ip.mkdir(parents=True)
        write_component_xml(
            ip,
            {
                "xilinx_anylanguagebehavioralsimulation_xilinx_com_ip_"
                "processing_system7_vip_1_0__ref_view_fileset": [
                    "../../ipshared/9a25/hdl/"
                    "processing_system7_vip_v1_0_vl_rfs.sv"
                ],
            },
        )
        manifest = import_manifest(self.root, root=self.root)
        self.assertNotIn(
            "ipshared/9a25/hdl/processing_system7_vip_v1_0_vl_rfs.sv",
            manifest.sources,
        )

    def test_component_xml_keeps_a_shared_synthesis_file(self) -> None:
        """A file in both file sets is part of the design."""
        common = self.root / "ipshared" / "common" / "hdl" / "common.v"
        common.parent.mkdir(parents=True)
        common.write_text("module common; endmodule\n")
        ip = self.root / "ip" / "core"
        ip.mkdir(parents=True)
        write_component_xml(
            ip,
            {
                "xilinx_verilogbehavioralsimulation_xilinx_com_ip_"
                "common_1_0__ref_view_fileset": [
                    "../../ipshared/common/hdl/common.v"
                ],
                "xilinx_verilogsynthesis_xilinx_com_ip_"
                "common_1_0__ref_view_fileset": [
                    "../../ipshared/common/hdl/common.v"
                ],
            },
        )
        manifest = import_manifest(self.root, root=self.root)
        self.assertIn("ipshared/common/hdl/common.v", manifest.sources)

    def test_component_xml_anylanguage_behavioral_model(self) -> None:
        """The any-language behavioral file set is simulation-only too."""
        vip = self.root / "ipshared" / "9a25" / "hdl"
        vip.mkdir(parents=True)
        (vip / "processing_system7_vip_v1_0_vl_rfs.sv").write_text(
            "module processing_system7_vip; endmodule\n"
        )
        ps = self.root / "ip" / "ps" / "hdl" / "verilog"
        ps.mkdir(parents=True)
        (ps / "processing_system7_v5_5_processing_system7.v").write_text(
            "module processing_system7_v5_5_processing_system7; endmodule\n"
        )
        ip = self.root / "ip" / "core"
        ip.mkdir(parents=True)
        write_component_xml(
            ip,
            {
                "xilinx_anylanguagebehavioralsimulation_xilinx_com_ip_"
                "processing_system7_vip_1_0__ref_view_fileset": [
                    "../../ipshared/9a25/hdl/"
                    "processing_system7_vip_v1_0_vl_rfs.sv"
                ],
                "xilinx_anylanguagesynthesis_view_fileset": [
                    "../../ip/ps/hdl/verilog/"
                    "processing_system7_v5_5_processing_system7.v"
                ],
            },
        )
        manifest = import_manifest(self.root, root=self.root)
        self.assertIn(
            "ip/ps/hdl/verilog/processing_system7_v5_5_processing_system7.v",
            manifest.sources,
        )
        self.assertNotIn(
            "ipshared/9a25/hdl/processing_system7_vip_v1_0_vl_rfs.sv",
            manifest.sources,
        )

    def test_fileset_sources_bypass_the_generated_filter(self) -> None:
        """The FileSet is authoritative even for a name the scan skips."""
        ip = self.root / "ip" / "core"
        ip.mkdir(parents=True)
        (ip / "keep_stub.v").write_text("module keep_stub; endmodule\n")
        (ip / "keep_sim_netlist.v").write_text(
            "module keep_sim_netlist; endmodule\n"
        )
        write_xpr(
            self.root,
            files=[
                "$PPRDIR/rtl/core.v",
                "$PPRDIR/ip/core/keep_stub.v",
                "$PPRDIR/ip/core/keep_sim_netlist.v",
            ],
        )
        manifest = import_manifest(self.root, root=self.root)
        self.assertIn("ip/core/keep_stub.v", manifest.sources)
        self.assertIn("ip/core/keep_sim_netlist.v", manifest.sources)

    def test_scan_does_not_skip_a_project_dir_named_sim(self) -> None:
        """The project's own name must not look like a simulation tree."""
        proj = self.root / "proj.sim"
        (proj / "rtl").mkdir(parents=True)
        (proj / "rtl" / "core.v").write_text("module core; endmodule\n")
        write_xpr(proj, files=["$PPRDIR/rtl/core.v"])
        manifest = import_manifest(proj, root=proj)
        self.assertEqual(manifest.sources, ("rtl/core.v",))

    def test_directory_scan_skips_generated_dirs_for_any_project_name(self) -> None:
        """Generated dirs carry the project name, not the literal "project"."""
        for name in (
            "myproj.runs",
            "myproj.cache",
            "myproj.gen",
            "myproj.hw",
            "myproj.sim",
            "myproj.ip_user_files",
            ".Xil",
            ".git",
        ):
            d = self.root / name / "sub"
            d.mkdir(parents=True)
            (d / "generated.v").write_text("module generated; endmodule\n")
        manifest = import_manifest(self.root, root=self.root)
        self.assertEqual(manifest.sources, ("rtl/core.v",))


if __name__ == "__main__":
    unittest.main()