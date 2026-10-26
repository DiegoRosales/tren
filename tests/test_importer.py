"""Unit tests for the manifest importer (card 211). Parser is pure text.

Run:  nix develop . -c bash -c "PYTHONPATH=src python -m unittest discover -s tests"
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tren.importer import import_manifest, parse_filelist  # noqa: E402


class ParseFilelistTests(unittest.TestCase):
    def test_sources_defines_incdirs(self) -> None:
        sources, incdirs, defines, warnings = parse_filelist(
            "// demo filelist\n"
            "-D DEMODEF=1\n"
            "+incdir+../rtl\n"
            "demo.sv\n"
            "sub/deep.sv\n"
        )
        self.assertEqual(sources, ["demo.sv", "sub/deep.sv"])
        self.assertEqual(incdirs, ["../rtl"])
        self.assertEqual(defines, [("DEMODEF", "1")])
        self.assertEqual(warnings, [])

    def test_define_without_value_defaults_to_1(self) -> None:
        _, _, defines, _ = parse_filelist("-D NOSCALE\n-D FOO=2\n")
        self.assertEqual(sorted(defines), [("FOO", "2"), ("NOSCALE", "1")])

    def test_space_and_joined_forms(self) -> None:
        sources, incdirs, _, _ = parse_filelist(
            "-I inc -Iinc2\n--include-directory inc3\ntop.sv \"with space.sv\""
        )
        self.assertEqual(incdirs, ["inc", "inc2", "inc3"])
        self.assertEqual(sources, ["top.sv", "with space.sv"])

    def test_unknown_option_warns_not_dies(self) -> None:
        *_, warnings = parse_filelist("-v\n-v full\n-xvf\n")
        self.assertGreaterEqual(len(warnings), 3)

    def test_trailing_comment_stripped(self) -> None:
        sources, _, _, _ = parse_filelist("demo.sv // the design\n")
        self.assertEqual(sources, ["demo.sv"])

    def test_define_values(self) -> None:
        _, _, defines, _ = parse_filelist("-D A\n-D B=2\n+define+C=x\n")
        self.assertEqual(sorted(defines), [("A", "1"), ("B", "2"), ("C", "x")])


class ImportManifestTests(unittest.TestCase):
    def _tree(self) -> tuple[Path, Path]:
        td = tempfile.mkdtemp(prefix="tren-import-")
        root = Path(td)
        (root / "rtl").mkdir()
        (root / "rtl" / "top.sv").write_text("module top;\nendmodule\n")
        (root / "rtl" / "sub").mkdir()
        (root / "rtl" / "sub" / "leaf.sv").write_text("")
        (root / "rtl" / "notes.txt").write_text("x")
        return td, root

    def test_dir_without_filelist_scans_sv(self) -> None:
        _, root = self._tree()
        m = import_manifest(root / "rtl", root=root)
        self.assertEqual(m.sources, ("rtl/sub/leaf.sv", "rtl/top.sv"))
        self.assertEqual(m.top, "top")

    def test_single_file(self) -> None:
        _, root = self._tree()
        m = import_manifest(root / "rtl" / "top.sv", root=root)
        self.assertEqual(m.sources, ("rtl/top.sv",))
        self.assertEqual(m.name, "top")

    def test_sibling_include_dir_becomes_incdir(self) -> None:
        """Card 260: a `` `include `` next to its source needs no manual incdir."""
        _, root = self._tree()
        (root / "rtl" / "defs.svh").write_text("`define FOO 1\n")
        (root / "rtl" / "top.sv").write_text(
            "`include \"defs.svh\"\nmodule top;\nendmodule\n"
        )
        m = import_manifest(root / "rtl", root=root)
        self.assertIn("rtl", m.incdirs)
        self.assertNotIn("defs.svh", m.sources)

    def test_root_source_dir_becomes_dot_incdir(self) -> None:
        """Card 260: slang does not search the cwd, so a root source records
        ``.`` and a root header resolves from a subdirectory source."""
        _, root = self._tree()
        (root / "root.sv").write_text("module root; endmodule\n")
        m = import_manifest(root, root=root)
        self.assertIn(".", m.incdirs)

    def test_dir_name_default(self) -> None:
        _, root = self._tree()
        self.assertEqual(import_manifest(root / "rtl", root=root).name, "rtl")

    def test_dir_with_single_filelist_is_file_relative(self) -> None:
        _, root = self._tree()
        (root / "rtl" / "list.f").write_text(
            "-D DEMO=1\n+incdir+inc\ntop.sv sub/leaf.sv\n"
        )
        (root / "rtl" / "inc").mkdir()
        m = import_manifest(root / "rtl", root=root)
        self.assertEqual(m.sources, ("rtl/top.sv", "rtl/sub/leaf.sv"))
        # card 260: explicit +incdir+ is kept, and the source directories are
        # added on top (yosys needs them spelled out as -I)
        self.assertEqual(m.incdirs, ("rtl", "rtl/inc", "rtl/sub"))
        self.assertEqual(m.defines, (("DEMO", "1"),))

    def test_multiple_filelists_raise(self) -> None:
        _, root = self._tree()
        (root / "rtl" / "a.f").write_text("top.sv\n")
        (root / "rtl" / "b.f").write_text("top.sv\n")
        with self.assertRaises(ValueError):
            import_manifest(root / "rtl", root=root)

    def test_json_roundtrip_via_manifest(self) -> None:
        _, root = self._tree()
        m = import_manifest(root / "rtl", root=root)
        self.assertEqual(m.sources, m.from_json(m.to_json()).sources)

    def test_missing_target_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            import_manifest("/nonexistent/tren", root="/")


if __name__ == "__main__":
    unittest.main()