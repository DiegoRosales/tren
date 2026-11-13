"""Tests for the run log (``tren.log``).

Every command that runs a backend writes the commands, their output and
TREN's own steps to ``tren.log``. ``--log PATH`` moves the file and
``--nolog`` turns it off.

Run:  nix develop . -c bash -c "PYTHONPATH=src python -m unittest discover -s tests"
"""

from __future__ import annotations

import io
import shlex
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tren import log  # noqa: E402
from tren.config import Config  # noqa: E402
from tren.elaborate import _run  # noqa: E402
from tren.ir import Design  # noqa: E402
from tren.manifest import Manifest  # noqa: E402


def _args(**values: object) -> Namespace:
    parsed = {"log": None, "nolog": False}
    parsed.update(values)
    return Namespace(**parsed)


def _loaded() -> object:
    from tren.config import ConfigFiles, LoadedConfig, OPTIONS

    config = Config()
    return LoadedConfig(
        config=config,
        values={option.key: getattr(config, option.field) for option in OPTIONS},
        origins={option.key: "default" for option in OPTIONS},
        files=ConfigFiles(
            system=Path("/nonexistent"),
            user=Path("/nonexistent"),
            project=None,
        ),
    )


class LoggerFormatTests(unittest.TestCase):
    def test_header_names_the_command_and_the_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tren.log"
            logger = log.Logger(path, command="tren open rtl", cwd=td)
            logger.note("cache hit: abc")
            logger.close()
            text = path.read_text(encoding="utf-8")
        self.assertIn("TREN log\n", text)
        self.assertIn("command: tren open rtl\n", text)
        self.assertIn(f"cwd: {Path(td)}\n", text)
        self.assertIn("] cache hit: abc\n", text)

    def test_backend_keeps_the_quoted_command_and_the_output(self) -> None:
        cmd = ["yosys", "-Q", "-p", "read_verilog a.sv; proc"]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tren.log"
            logger = log.Logger(path)
            logger.backend(
                cmd, cwd=td, returncode=1, stdout="out\n", stderr="bad\n"
            )
            logger.close()
            text = path.read_text(encoding="utf-8")
        run_line = text.split("run: ", 1)[1].splitlines()[0]
        self.assertEqual(shlex.split(run_line), cmd)
        self.assertIn("exit: 1\n", text)
        self.assertIn("] stdout:\nout\n", text)
        self.assertIn("] stderr:\nbad\n", text)

    def test_empty_output_has_no_section(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tren.log"
            logger = log.Logger(path)
            logger.backend(["yosys"], cwd=td, returncode=0)
            logger.close()
            text = path.read_text(encoding="utf-8")
        self.assertNotIn("stdout:", text)
        self.assertNotIn("stderr:", text)

    def test_a_new_run_replaces_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tren.log"
            first = log.Logger(path)
            first.note("first run")
            first.close()
            second = log.Logger(path)
            second.note("second run")
            second.close()
            text = path.read_text(encoding="utf-8")
        self.assertNotIn("first run", text)
        self.assertIn("second run", text)

    def test_a_write_failure_warns_and_stops_the_log(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tren.log"
            logger = log.Logger(path)
            err = io.StringIO()
            with mock.patch.object(
                logger._file, "write", side_effect=OSError("disk full")
            ), mock.patch("sys.stderr", err):
                logger.note("cache hit")
                logger.note("second step")
            logger.close()
            warnings = err.getvalue()
        self.assertIn("the log stopped: disk full", warnings)
        self.assertEqual(warnings.count("the log stopped"), 1)


class ArgumentTests(unittest.TestCase):
    def tearDown(self) -> None:
        log.set_active(None)

    def test_the_default_path_is_tren_log_in_the_current_directory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tren.log"
            with mock.patch.object(log, "DEFAULT_PATH", str(path)):
                logger = log.from_args(_args())
                self.assertIsNotNone(logger)
                logger.close()
            self.assertTrue(path.is_file())

    def test_nolog_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tren.log"
            with mock.patch.object(log, "DEFAULT_PATH", str(path)):
                logger = log.from_args(_args(nolog=True))
            self.assertIsNone(logger)
            self.assertFalse(path.exists())

    def test_log_path_overrides_the_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "nested" / "my.log"
            logger = log.from_args(_args(log=path))
            self.assertIsNotNone(logger)
            logger.close()
            self.assertTrue(path.is_file())

    def test_an_unwritable_path_warns_and_leaves_the_run_unlogged(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            blocker = Path(td) / "blocker"
            blocker.write_text("")
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                logger = log.from_args(_args(log=blocker / "tren.log"))
            self.assertIsNone(logger)
            self.assertIn("cannot write the log", err.getvalue())

    def test_a_command_with_a_non_utf8_path_still_opens_the_log(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tren.log"
            logger = log.from_args(
                _args(log=path), command="tren elaborate bad\udcff.sv"
            )
            self.assertIsNotNone(logger)
            assert logger is not None
            logger.close()
            text = path.read_text(encoding="utf-8")
        self.assertIn("bad\\udcff.sv", text)

    def test_the_header_records_the_process_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tren.log"
            logger = log.from_args(_args(log=path))
            self.assertIsNotNone(logger)
            assert logger is not None
            logger.close()
            text = path.read_text(encoding="utf-8")
        self.assertIn(f"cwd: {Path.cwd().resolve()}\n", text)

    def test_log_and_nolog_are_mutually_exclusive(self) -> None:
        import argparse

        parser = argparse.ArgumentParser()
        log.add_arguments(parser)
        with self.assertRaises(SystemExit), mock.patch(
            "sys.stderr", io.StringIO()
        ):
            parser.parse_args(["--log", "a.log", "--nolog"])


class RunWiringTests(unittest.TestCase):
    def tearDown(self) -> None:
        log.set_active(None)

    def test_run_records_each_backend_call(self) -> None:
        proc = subprocess.CompletedProcess(
            ["slang-hier", "--params"], 0, stdout="node\n", stderr=""
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tren.log"
            logger = log.Logger(path)
            log.set_active(logger)
            with mock.patch(
                "tren.elaborate.subprocess.run", return_value=proc
            ):
                _run(["slang-hier", "--params"], Path(td))
            log.set_active(None)
            logger.close()
            text = path.read_text(encoding="utf-8")
        self.assertIn("run: slang-hier --params", text)
        self.assertIn("exit: 0\n", text)
        self.assertIn("] stdout:\nnode\n", text)

    def test_run_records_the_exit_code_of_a_rejected_design(self) -> None:
        proc = subprocess.CompletedProcess(
            ["yosys"], 1, stdout="", stderr="ERROR: syntax\n"
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tren.log"
            logger = log.Logger(path)
            log.set_active(logger)
            with mock.patch("tren.elaborate.subprocess.run", return_value=proc):
                with self.assertRaises(subprocess.CalledProcessError):
                    _run(["yosys"], Path(td))
            log.set_active(None)
            logger.close()
            text = path.read_text(encoding="utf-8")
        self.assertIn("exit: 1\n", text)
        self.assertIn("ERROR: syntax", text)

    def test_a_cache_hit_is_recorded(self) -> None:
        from tren.elaborate import elaborate

        class Cache:
            def get(self, manifest: Manifest) -> Design:
                return Design(top="top")

            def path(self, manifest: Manifest) -> Path:
                return Path("/cache/abc.json")

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tren.log"
            logger = log.Logger(path)
            log.set_active(logger)
            try:
                elaborate(Manifest(sources=("a.sv",)), td, cache=Cache())
            finally:
                log.set_active(None)
                logger.close()
            text = path.read_text(encoding="utf-8")
        self.assertIn("cache hit: /cache/abc.json", text)

    def test_a_missing_manifest_is_recorded_as_an_error(self) -> None:
        from tren.elaborate import main

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tren.log"
            err = io.StringIO()
            with mock.patch(
                "tren.elaborate.load_config",
                return_value=mock.Mock(config=Config()),
            ), redirect_stderr(err):
                code = main([str(Path(td) / "absent.json"), "--log", str(path)])
            text = path.read_text(encoding="utf-8")
        self.assertEqual(code, 2)
        self.assertIn("error:", text)
        self.assertIn("absent.json", text)

    def test_note_without_an_open_log_does_nothing(self) -> None:
        log.set_active(None)
        log.note("cache hit")  # must not raise

    def test_open_writes_notes_for_its_own_steps(self) -> None:
        from tren.bringup import main

        design = Design(top="top")
        manifest = Manifest(sources=("a.sv",))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tren.log"
            with mock.patch(
                "tren.bringup.load_config", return_value=_loaded()
            ), mock.patch(
                "tren.bringup.load_target", return_value=(manifest, design)
            ), mock.patch("tren.bringup.run_tui"), redirect_stdout(io.StringIO()):
                code = main(["a.sv", "--log", str(path)])
            text = path.read_text(encoding="utf-8")
        self.assertEqual(code, 0)
        self.assertIn("command: tren open a.sv --log", text)
        self.assertIn("opening the TUI", text)
        self.assertIn("TUI closed", text)

    def test_open_nolog_writes_no_file(self) -> None:
        from tren.bringup import main

        design = Design(top="top")
        manifest = Manifest(sources=("a.sv",))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tren.log"
            with mock.patch.object(log, "DEFAULT_PATH", str(path)), mock.patch(
                "tren.bringup.load_config", return_value=_loaded()
            ), mock.patch(
                "tren.bringup.load_target", return_value=(manifest, design)
            ), mock.patch("tren.bringup.run_tui"), redirect_stdout(io.StringIO()):
                code = main(["a.sv", "--nolog"])
            self.assertEqual(code, 0)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
