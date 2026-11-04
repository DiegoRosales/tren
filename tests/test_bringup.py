"""Unit tests for the one-command bringup.

``tren open <target>`` must import a source target (or read a manifest),
elaborate it in the same process, keep importer warnings visible, and only
write a manifest when ``--out`` asks for one. The bundled backends run only
in the acceptance test at the bottom (gated on the dev shell).

Run:  nix develop . -c bash -c "PYTHONPATH=src python -m unittest discover -s tests"
"""

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tren import __main__ as cli  # noqa: E402
from tren.bringup import is_manifest_target, load_target, main  # noqa: E402
from tren.config import Config  # noqa: E402
from tren.elaborate import elaborate  # noqa: E402
from tren.importer import import_manifest  # noqa: E402
from tren.ir import Design  # noqa: E402
from tren.manifest import Manifest  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


class TargetDispatchTests(unittest.TestCase):
    def test_manifest_target_is_a_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            manifest = Path(td) / "manifest.json"
            manifest.write_text("{}")
            self.assertTrue(is_manifest_target(manifest))
            self.assertFalse(is_manifest_target(Path(td)))

    def test_manifest_target_is_read_not_imported(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "manifest.json"
            path.write_text(Manifest(name="hand", sources=("a.sv",)).to_json())
            design = Design(top="hand")
            with mock.patch(
                "tren.bringup.import_manifest",
                side_effect=AssertionError("must not import a manifest"),
            ), mock.patch("tren.bringup.elaborate", return_value=design) as build:
                manifest, got = load_target(path, root=root)
            self.assertEqual(manifest.name, "hand")
            self.assertIs(got, design)
            build.assert_called_once()

    def test_source_target_is_imported_and_not_written(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.sv").write_text("module top; endmodule\n")
            design = Design(top="top")
            with mock.patch("tren.bringup.elaborate", return_value=design):
                manifest, _ = load_target(root, root=root)
            self.assertEqual(manifest.sources, ("a.sv",))
            self.assertEqual(manifest.top, "top")
            self.assertFalse((root / "manifest.json").exists())

    def test_out_writes_the_imported_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.sv").write_text("module top; endmodule\n")
            out = root / "build" / "design.json"
            with mock.patch("tren.bringup.elaborate", return_value=Design(top="top")):
                load_target(root, root=root, out=out)
            written = Manifest.read(out)
            self.assertEqual(written.sources, ("a.sv",))

    def test_out_directory_receives_manifest_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.sv").write_text("module top; endmodule\n")
            out = root / "build"
            out.mkdir()
            with mock.patch("tren.bringup.elaborate", return_value=Design(top="top")):
                load_target(root, root=root, out=out)
            self.assertEqual(Manifest.read(out / "manifest.json").sources, ("a.sv",))

    def test_file_list_is_imported_without_a_target(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "b.sv").write_text("module top; endmodule\n")
            (root / "a.sv").write_text("module leaf; endmodule\n")
            design = Design(top="top")
            with mock.patch("tren.bringup.elaborate", return_value=design):
                manifest, got = load_target(
                    files=[str(root / "b.sv"), str(root / "a.sv")],
                    root=root,
                    top="top",
                )
            self.assertEqual(
                manifest.sources, ("b.sv", "a.sv")
            )
            self.assertEqual(manifest.name, root.name)
            self.assertIs(got, design)

    def test_file_list_writes_the_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.sv").write_text("module top; endmodule\n")
            out = root / "build" / "design.json"
            with mock.patch("tren.bringup.elaborate", return_value=Design(top="top")):
                load_target(files=[str(root / "a.sv")], root=root, out=out)
            self.assertEqual(Manifest.read(out).sources, ("a.sv",))

    def test_missing_file_warns_and_keeps_the_rest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.sv").write_text("module top; endmodule\n")
            warnings: list[str] = []
            design = Design(top="top")
            with mock.patch("tren.bringup.elaborate", return_value=design):
                manifest, got = load_target(
                    files=[str(root / "missing.sv"), str(root / "a.sv")],
                    root=root,
                    warnings_out=warnings,
                )
            self.assertEqual(manifest.sources, ("a.sv",))
            self.assertIs(got, design)
            self.assertTrue(
                any("skipping missing source file" in w for w in warnings),
                warnings,
            )

    def test_all_files_missing_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaisesRegex(ValueError, "no source files remain"):
                load_target(files=[str(root / "missing.sv")], root=root)

    def test_target_with_files_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.sv").write_text("module top; endmodule\n")
            with self.assertRaisesRegex(ValueError, "not both"):
                load_target(
                    root / "a.sv", files=[str(root / "a.sv")], root=root
                )

    def test_format_with_files_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.sv").write_text("module top; endmodule\n")
            with self.assertRaisesRegex(ValueError, "does not apply"):
                load_target(files=[str(root / "a.sv")], root=root, fmt="f")

    def test_manifest_rejects_import_only_flags(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "manifest.json"
            path.write_text(Manifest().to_json())
            with self.assertRaisesRegex(ValueError, "only apply when importing"):
                load_target(path, top="other")


class WarningTests(unittest.TestCase):
    def test_importer_warnings_reach_the_caller(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "sources.f").write_text("--unknown-option\n")
            (root / "a.sv").write_text("module top; endmodule\n")
            warnings: list[str] = []
            with mock.patch("tren.bringup.elaborate", return_value=Design(top="top")):
                load_target(root, root=root, warnings_out=warnings)
            self.assertTrue(any("unknown" in w for w in warnings), warnings)

    def test_warnings_print_when_the_backend_fails(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.sv").write_text("module top; endmodule\n")
            failure = subprocess.CalledProcessError(
                1, ["yosys"], stderr="ERROR: bad design\n"
            )
            with mock.patch(
                "tren.bringup.load_config", return_value=_loaded(Config())
            ), mock.patch("tren.bringup.load_target") as load:
                def fake_load(*args, warnings_out=None, **kwargs):
                    warnings_out.append("missing include")
                    raise failure
                load.side_effect = fake_load
                err = io.StringIO()
                with redirect_stderr(err):
                    code = main([str(root)])
            self.assertEqual(code, 1)
            self.assertIn("missing include", err.getvalue())
            self.assertIn("yosys failed", err.getvalue())

    def test_missing_target_reports_without_a_traceback(self) -> None:
        err = io.StringIO()
        with mock.patch(
            "tren.bringup.load_config", return_value=_loaded(Config())
        ), redirect_stderr(err), redirect_stdout(io.StringIO()):
            code = main(["/nonexistent/tren-target"])
        self.assertEqual(code, 2)
        self.assertIn("tren: no such file or directory", err.getvalue())


class SubcommandTests(unittest.TestCase):
    def test_open_verb_dispatches_to_bringup(self) -> None:
        with mock.patch("tren.bringup.main", return_value=0) as run:
            code = cli.main(["open", "rtl", "--top", "foo"])
        self.assertEqual(code, 0)
        run.assert_called_once_with(["rtl", "--top", "foo"], prog="tren open")

    def test_a_target_alone_is_not_a_command(self) -> None:
        err = io.StringIO()
        with mock.patch("tren.bringup.main", return_value=0) as run, redirect_stderr(
            err
        ):
            code = cli.main(["rtl"])
        self.assertEqual(code, 2)
        run.assert_not_called()
        self.assertIn("unknown command: rtl", err.getvalue())
        self.assertIn("tren --help", err.getvalue())


def _loaded(config: Config):
    from tren.config import ConfigFiles, LoadedConfig, OPTIONS

    values = {
        option.key: getattr(config, option.field) for option in OPTIONS
    }
    origins = {option.key: "default" for option in OPTIONS}
    files = ConfigFiles(system=Path("/nonexistent"), user=Path("/nonexistent"), project=None)
    return LoadedConfig(config=config, values=values, origins=origins, files=files)


class LaunchTests(unittest.TestCase):
    def test_main_opens_the_tui_on_the_loaded_design(self) -> None:
        design = Design(top="top")
        manifest = Manifest(sources=("a.sv",))
        settings = Config()
        with mock.patch(
            "tren.bringup.load_config", return_value=_loaded(settings)
        ), mock.patch(
            "tren.bringup.load_target", return_value=(manifest, design)
        ), mock.patch("tren.bringup.run_tui") as launch, redirect_stdout(
            io.StringIO()
        ):
            code = main(["a.sv"])
        self.assertEqual(code, 0)
        launch.assert_called_once()
        self.assertIs(launch.call_args.args[0], design)
        self.assertIs(launch.call_args.args[2], settings)

    def test_main_passes_the_file_list_to_load_target(self) -> None:
        design = Design(top="top")
        manifest = Manifest(sources=("a.sv", "b.sv"))
        with mock.patch(
            "tren.bringup.load_config", return_value=_loaded(Config())
        ), mock.patch(
            "tren.bringup.load_target", return_value=(manifest, design)
        ) as load, mock.patch("tren.bringup.run_tui"), redirect_stdout(
            io.StringIO()
        ):
            code = main(["-f", "a.sv", "-f", "b.sv", "--top", "top"])
        self.assertEqual(code, 0)
        self.assertEqual(load.call_args.args[0], None)
        self.assertEqual(load.call_args.kwargs["files"], ["a.sv", "b.sv"])

    def test_main_rejects_a_target_with_files(self) -> None:
        with mock.patch(
            "tren.bringup.load_config", return_value=_loaded(Config())
        ), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            main(["rtl", "-f", "a.sv"])
        self.assertEqual(exc.exception.code, 2)

    def test_main_requires_a_target_or_files(self) -> None:
        with mock.patch(
            "tren.bringup.load_config", return_value=_loaded(Config())
        ), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            main([])
        self.assertEqual(exc.exception.code, 2)

    def test_main_rejects_format_with_files(self) -> None:
        with mock.patch(
            "tren.bringup.load_config", return_value=_loaded(Config())
        ), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            main(["-f", "a.sv", "--format", "f"])
        self.assertEqual(exc.exception.code, 2)

    def test_cache_dir_defaults_from_the_config(self) -> None:
        design = Design(top="top")
        manifest = Manifest(sources=("a.sv",))
        with mock.patch(
            "tren.bringup.load_config",
            return_value=_loaded(Config(cache_dir="config-cache")),
        ), mock.patch(
            "tren.bringup.load_target", return_value=(manifest, design)
        ) as load, mock.patch("tren.bringup.run_tui"), redirect_stdout(
            io.StringIO()
        ):
            code = main(["a.sv"])
        self.assertEqual(code, 0)
        self.assertEqual(load.call_args.kwargs["cache"], Path("config-cache"))

    def test_explicit_cache_overrides_the_config(self) -> None:
        design = Design(top="top")
        manifest = Manifest(sources=("a.sv",))
        with mock.patch(
            "tren.bringup.load_config",
            return_value=_loaded(Config(cache_dir="config-cache")),
        ), mock.patch(
            "tren.bringup.load_target", return_value=(manifest, design)
        ) as load, mock.patch("tren.bringup.run_tui"), redirect_stdout(
            io.StringIO()
        ):
            code = main(["a.sv", "--cache", "cli-cache"])
        self.assertEqual(code, 0)
        self.assertEqual(load.call_args.kwargs["cache"], Path("cli-cache"))


@unittest.skipUnless(
    __import__("shutil").which("yosys")
    and __import__("shutil").which("slang-hier")
    and __import__("shutil").which("slang"),
    "requires the nix dev shell (yosys/slang on PATH)",
)
class OneCommandEqualsTwoCommandTests(unittest.TestCase):
    """Layer 2: the composite result equals import-then-elaborate on tests/rtl/."""

    def test_rtl_directory(self) -> None:
        two_step = elaborate(
            import_manifest(REPO / "tests" / "rtl", root=REPO), REPO, cache=None
        )
        _, one_step = load_target(REPO / "tests" / "rtl", root=REPO, cache=None)
        self.assertEqual(one_step, two_step)


if __name__ == "__main__":
    unittest.main()
