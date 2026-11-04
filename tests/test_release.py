"""The package version and the documented install stay in step."""

from __future__ import annotations

import io
import platform
import re
import sys
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import tren  # noqa: E402
from tren import __main__ as cli  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")


class VersionTests(unittest.TestCase):
    def test_pyproject_and_package_versions_match(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(project["project"]["version"], tren.__version__)

    def test_version_is_semver(self) -> None:
        self.assertRegex(tren.__version__, r"^\d+\.\d+\.\d+$")

    def test_readme_pins_the_release_tag(self) -> None:
        tag = f"v{tren.__version__}"
        self.assertIn(f"tren@{tag}", README)
        self.assertIn(f"git checkout {tag}", README)

    def test_source_install_does_not_use_a_branch(self) -> None:
        self.assertNotIn("--branch", README)

    def test_readme_has_no_unpinned_git_install(self) -> None:
        unpinned = re.search(
            r"git\+https://github\.com/DiegoRosales/tren(?!@)",
            README,
        )
        self.assertIsNone(unpinned, "the pip install must carry a tag")

    def test_cli_version_reports_the_python_version(self) -> None:
        out = io.StringIO()
        with mock.patch("tren.__main__._source_sha", return_value=None):
            with redirect_stdout(out):
                code = cli.main(["--version"])
        self.assertEqual(code, 0)
        self.assertEqual(
            out.getvalue().strip(),
            f"tren {tren.__version__} (Python {platform.python_version()})",
        )

    def test_cli_version_reports_a_source_sha(self) -> None:
        out = io.StringIO()
        with mock.patch("tren.__main__._source_sha", return_value="abc1234"):
            with redirect_stdout(out):
                code = cli.main(["--version"])
        self.assertEqual(code, 0)
        self.assertEqual(
            out.getvalue().strip(),
            (
                f"tren {tren.__version__} "
                f"(dev-abc1234, Python {platform.python_version()})"
            ),
        )

    def test_version_rejects_extra_arguments(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = cli.main(["--version", "extra"])
        self.assertEqual(code, 2)
        self.assertIn("takes no arguments", err.getvalue())

    def test_tool_lines_report_version_and_path(self) -> None:
        result = mock.Mock(stdout="Yosys 0.70+post\n", stderr="")
        with mock.patch(
            "tren.__main__.shutil.which", return_value="/bin/yosys"
        ), mock.patch("tren.__main__.subprocess.run", return_value=result) as run:
            lines = cli._tool_lines("yosys", "-V")
        self.assertEqual(lines, ["yosys: 0.70+post (/bin/yosys)"])
        run.assert_called_once_with(
            ["/bin/yosys", "-V"],
            capture_output=True,
            text=True,
            check=False,
        )

        slang = mock.Mock(stdout="slang version 12.0.0+0\n", stderr="")
        with mock.patch(
            "tren.__main__.shutil.which", return_value="/bin/slang"
        ), mock.patch("tren.__main__.subprocess.run", return_value=slang):
            self.assertEqual(
                cli._tool_lines("slang", "--version"),
                ["slang: 12.0.0+0 (/bin/slang)"],
            )

    def test_tool_lines_warn_when_the_tool_is_missing(self) -> None:
        with mock.patch("tren.__main__.shutil.which", return_value=None):
            lines = cli._tool_lines("slang", "--version")
        self.assertEqual(
            lines,
            [
                "warning: slang not found on PATH",
                "slang: not found",
            ],
        )

    def test_version_debug_reports_environment(self) -> None:
        out = io.StringIO()
        with mock.patch("tren.__main__._source_root", return_value=None):
            with mock.patch(
                "tren.__main__._installed_version", return_value="test-1.0"
            ):
                with mock.patch(
                    "tren.__main__._tool_lines",
                    side_effect=[
                        ["yosys: Yosys test (/bin/yosys)"],
                        ["slang: slang version test (/bin/slang)"],
                    ],
                ):
                    with redirect_stdout(out):
                        code = cli.main(["--version-debug"])
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn(f"python: {platform.python_version()}", text)
        self.assertIn(f"implementation: {platform.python_implementation()}", text)
        self.assertIn("executable: ", text)
        self.assertIn(
            f"platform: {platform.system()} {platform.release()} {platform.machine()}",
            text,
        )
        self.assertIn(f"package: {Path(tren.__file__).resolve().parent}", text)
        self.assertIn("source: installed", text)
        self.assertIn(f"cwd: {Path.cwd()}", text)
        self.assertIn("textual: test-1.0", text)
        self.assertIn("rich: test-1.0", text)
        self.assertIn("yosys: Yosys test (/bin/yosys)", text)
        self.assertIn("slang: slang version test (/bin/slang)", text)
        self.assertIn("config:", text)
        self.assertIn("  system: ", text)
        self.assertIn("  user: ", text)
        self.assertIn("  project: ", text)

    def test_version_debug_warns_when_tools_are_missing(self) -> None:
        out = io.StringIO()
        with mock.patch(
            "tren.__main__._source_root", return_value=None
        ), mock.patch(
            "tren.__main__._installed_version", return_value="test-1.0"
        ), mock.patch(
            "tren.__main__.shutil.which", return_value=None
        ):
            with redirect_stdout(out):
                code = cli.main(["--version-debug"])
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("warning: yosys not found on PATH", text)
        self.assertIn("warning: slang not found on PATH", text)
        self.assertIn("yosys: not found", text)
        self.assertIn("slang: not found", text)

    def test_version_debug_reports_a_dirty_source_tree(self) -> None:
        out = io.StringIO()
        with mock.patch(
            "tren.__main__._source_root", return_value=Path("/repo")
        ), mock.patch(
            "tren.__main__._source_sha", return_value="abc1234"
        ), mock.patch(
            "tren.__main__._source_dirty", return_value=True
        ), mock.patch(
            "tren.__main__._installed_version", return_value="test-1.0"
        ), mock.patch(
            "tren.__main__._tool_lines", return_value=[]
        ):
            with redirect_stdout(out):
                code = cli.main(["--version-debug"])
        self.assertEqual(code, 0)
        self.assertIn("source: /repo (git abc1234, dirty)", out.getvalue())

    def test_version_debug_rejects_extra_arguments(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = cli.main(["--version-debug", "extra"])
        self.assertEqual(code, 2)
        self.assertIn("takes no arguments", err.getvalue())


if __name__ == "__main__":
    unittest.main()
