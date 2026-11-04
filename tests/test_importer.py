"""Unit tests for the manifest importer. Parser is pure text.

Run:  nix develop . -c bash -c "PYTHONPATH=src python -m unittest discover -s tests"
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tren.importer import (  # noqa: E402
    IMPORT_SLANG_FLAGS,
    import_manifest,
    import_sources,
    main as importer_main,
    parse_filelist,
    parse_param,
)
from tren.manifest import Manifest  # noqa: E402


class ParseFilelistTests(unittest.TestCase):
    def test_sources_defines_incdirs(self) -> None:
        sources, incdirs, defines, params, warnings = parse_filelist(
            "// demo filelist\n"
            "-D DEMODEF=1\n"
            "+incdir+../rtl\n"
            "demo.sv\n"
            "sub/deep.sv\n"
        )
        self.assertEqual(sources, ["demo.sv", "sub/deep.sv"])
        self.assertEqual(incdirs, ["../rtl"])
        self.assertEqual(defines, [("DEMODEF", "1")])
        self.assertEqual(params, [])
        self.assertEqual(warnings, [])

    def test_define_without_value_defaults_to_1(self) -> None:
        _, _, defines, _, _ = parse_filelist("-D NOSCALE\n-D FOO=2\n")
        self.assertEqual(sorted(defines), [("FOO", "2"), ("NOSCALE", "1")])

    def test_space_and_joined_forms(self) -> None:
        sources, incdirs, _, _, _ = parse_filelist(
            "-I inc -Iinc2\n--include-directory inc3\ntop.sv \"with space.sv\""
        )
        self.assertEqual(incdirs, ["inc", "inc2", "inc3"])
        self.assertEqual(sources, ["top.sv", "with space.sv"])

    def test_unknown_option_warns_not_dies(self) -> None:
        *_, warnings = parse_filelist("-v\n-v full\n-xvf\n")
        self.assertGreaterEqual(len(warnings), 3)

    def test_trailing_comment_stripped(self) -> None:
        sources, _, _, _, _ = parse_filelist("demo.sv // the design\n")
        self.assertEqual(sources, ["demo.sv"])

    def test_define_values(self) -> None:
        _, _, defines, _, _ = parse_filelist("-D A\n-D B=2\n+define+C=x\n")
        self.assertEqual(sorted(defines), [("A", "1"), ("B", "2"), ("C", "x")])

    def test_parameter_override_is_consumed(self) -> None:
        sources, _, _, params, warnings = parse_filelist(
            "-G WIDTH=16\n-G DEPTH=4\ntop.sv\n"
        )
        self.assertEqual(sources, ["top.sv"])
        self.assertEqual(params, [("WIDTH", "16"), ("DEPTH", "4")])
        self.assertEqual(warnings, [])

    def test_joined_parameter_override(self) -> None:
        _, _, _, params, _ = parse_filelist("-GWIDTH=16\n")
        self.assertEqual(params, [("WIDTH", "16")])

    def test_malformed_parameter_override_raises(self) -> None:
        for text in ("-G WIDTH\n", "-G =16\n", "-G WIDTH=\n", "-G\n"):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "parameter"):
                parse_filelist(text)

    def test_parameter_value_must_be_one_token(self) -> None:
        with self.assertRaisesRegex(ValueError, "whitespace"):
            parse_param("WIDTH=8 + 2")


class ImportSourcesTests(unittest.TestCase):
    def test_explicit_sources_keep_order_and_derive_incdirs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "rtl" / "sub").mkdir(parents=True)
            first = root / "rtl" / "b.sv"
            second = root / "rtl" / "a.sv"
            third = root / "rtl" / "sub" / "c.sv"
            for path in (first, second, third):
                path.write_text("")
            m = import_sources([str(first), str(second), str(third)], root=root)
            self.assertEqual(
                m.sources, ("rtl/b.sv", "rtl/a.sv", "rtl/sub/c.sv")
            )
            self.assertEqual(m.incdirs, ("rtl", "rtl/sub"))
            self.assertEqual(m.name, root.name)
            self.assertEqual(m.top, "top")
            self.assertEqual(m.slang_flags, IMPORT_SLANG_FLAGS)

    def test_explicit_sources_name_and_top(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "a.sv"
            source.write_text("")
            m = import_sources(
                [str(source)], root=root, name="custom", top="dut"
            )
            self.assertEqual(m.name, "custom")
            self.assertEqual(m.top, "dut")

    def test_explicit_sources_store_parameter_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "a.sv"
            source.write_text("")
            m = import_sources(
                [str(source)], root=root, params=[("WIDTH", "16")]
            )
            self.assertEqual(m.params, (("WIDTH", "16"),))

    def test_missing_source_is_skipped_with_a_warning(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            present = root / "present.sv"
            present.write_text("")
            missing = root / "missing.sv"
            warnings: list[str] = []
            m = import_sources(
                [str(missing), str(present)],
                root=root,
                warnings_out=warnings,
            )
            self.assertEqual(m.sources, ("present.sv",))
            self.assertTrue(
                any("skipping missing source file" in w for w in warnings),
                warnings,
            )

    def test_all_missing_sources_raise(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError, "no source files remain"):
                import_sources([str(Path(td) / "missing.sv")], root=td)

    def test_empty_list_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one source"):
            import_sources([], root=".")


class ImportCliFileListTests(unittest.TestCase):
    def test_cli_writes_manifest_from_file_option(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            a, b = root / "a.sv", root / "b.sv"
            a.write_text("")
            b.write_text("")
            out = root / "design.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                code = importer_main(
                    ["-f", str(a), "-f", str(b), "--out", str(out), "--top", "dut"]
                )
            self.assertEqual(code, 0)
            written = Manifest.read(out)
            self.assertEqual(len(written.sources), 2)
            self.assertTrue(written.sources[0].endswith("a.sv"))
            self.assertTrue(written.sources[1].endswith("b.sv"))
            self.assertEqual(written.top, "dut")
            self.assertIn("format=files", stdout.getvalue())

    def test_cli_warns_about_a_missing_file_and_keeps_the_rest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            present = root / "present.sv"
            present.write_text("")
            out = root / "design.json"
            stderr = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                code = importer_main(
                    ["-f", str(root / "missing.sv"), "-f", str(present),
                     "--out", str(out)]
                )
            self.assertEqual(code, 0)
            written = Manifest.read(out)
            self.assertEqual(len(written.sources), 1)
            self.assertTrue(written.sources[0].endswith("present.sv"))
            self.assertIn("skipping missing source file", stderr.getvalue())

    def test_cli_reports_all_missing_sources_without_a_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            stderr = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                code = importer_main(
                    ["-f", str(root / "missing.sv"),
                     "--out", str(root / "design.json")]
                )
            self.assertEqual(code, 2)
            self.assertIn("no source files remain", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_cli_rejects_a_target_with_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "rtl"
            target.mkdir()
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                importer_main([str(target), "-f", "a.sv"])
            self.assertEqual(exc.exception.code, 2)

    def test_cli_rejects_format_with_files(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            importer_main(["-f", "a.sv", "--format", "f"])
        self.assertEqual(exc.exception.code, 2)

    def test_cli_writes_parameter_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "a.sv"
            source.write_text("")
            out = root / "design.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                code = importer_main(
                    ["-f", str(source), "-G", "WIDTH=16", "--out", str(out)]
                )
            self.assertEqual(code, 0)
            self.assertEqual(Manifest.read(out).params, (("WIDTH", "16"),))
            self.assertIn("param WIDTH=16", stdout.getvalue())

    def test_cli_duplicate_parameter_keeps_last_and_warns(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "a.sv"
            source.write_text("")
            out = root / "design.json"
            stderr = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                code = importer_main(
                    ["-f", str(source), "-G", "WIDTH=8", "-G", "WIDTH=16",
                     "--out", str(out)]
                )
            self.assertEqual(code, 0)
            self.assertEqual(Manifest.read(out).params, (("WIDTH", "16"),))
            self.assertIn("defined more than once", stderr.getvalue())

    def test_cli_rejects_a_malformed_parameter(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            importer_main(["-f", "a.sv", "-G", "WIDTH"])
        self.assertEqual(exc.exception.code, 2)

    def test_cli_requires_a_target_or_file(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            importer_main([])
        self.assertEqual(exc.exception.code, 2)


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
        """A `` `include `` next to its source needs no manual incdir."""
        _, root = self._tree()
        (root / "rtl" / "defs.svh").write_text("`define FOO 1\n")
        (root / "rtl" / "top.sv").write_text(
            "`include \"defs.svh\"\nmodule top;\nendmodule\n"
        )
        m = import_manifest(root / "rtl", root=root)
        self.assertIn("rtl", m.incdirs)
        self.assertNotIn("defs.svh", m.sources)

    def test_root_source_dir_becomes_dot_incdir(self) -> None:
        """Slang does not search the cwd, so a root source records
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
        # explicit +incdir+ is kept, and the source directories are
        # added on top (yosys needs them spelled out as -I)
        self.assertEqual(m.incdirs, ("rtl", "rtl/inc", "rtl/sub"))
        self.assertEqual(m.defines, (("DEMO", "1"),))

    def test_filelist_parameters_merge_with_the_cli(self) -> None:
        _, root = self._tree()
        (root / "rtl" / "list.f").write_text(
            "-G WIDTH=16\n-G DEPTH=4\ntop.sv\n"
        )
        warnings: list[str] = []
        m = import_manifest(
            root / "rtl",
            root=root,
            params=[("WIDTH", "32"), ("EXTRA", "1")],
            warnings_out=warnings,
        )
        self.assertEqual(m.params, (("WIDTH", "32"), ("DEPTH", "4"), ("EXTRA", "1")))
        self.assertTrue(
            any("parameter WIDTH is defined more than once" in w for w in warnings),
            warnings,
        )

    def test_filelist_duplicate_parameter_keeps_last_and_warns(self) -> None:
        _, root = self._tree()
        (root / "rtl" / "list.f").write_text(
            "-G WIDTH=8\n-G WIDTH=16\ntop.sv\n"
        )
        warnings: list[str] = []
        m = import_manifest(root / "rtl", root=root, warnings_out=warnings)
        self.assertEqual(m.params, (("WIDTH", "16"),))
        self.assertTrue(
            any("parameter WIDTH is defined more than once" in w for w in warnings),
            warnings,
        )

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

    def test_import_writes_slang_tolerance_flags(self) -> None:
        """A fresh import is openable: the slang tolerances travel with it."""
        _, root = self._tree()
        (root / "rtl" / "list.f").write_text("top.sv\n")
        for target in (root / "rtl", root / "rtl" / "top.sv", root / "rtl" / "list.f"):
            with self.subTest(target=target.name):
                m = import_manifest(target, root=root)
                self.assertEqual(m.slang_flags, IMPORT_SLANG_FLAGS)
                self.assertIn("--allow-use-before-declare", m.slang_flags)
                self.assertIn("--timescale=1ns/1ps", m.slang_flags)
                self.assertIn("-Wno-redefinition", m.slang_flags)
                # survives the manifest roundtrip the elaborator reads
                self.assertEqual(m.from_json(m.to_json()).slang_flags, m.slang_flags)


if __name__ == "__main__":
    unittest.main()