"""Tests for the external-tool checks."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tren.tools import slang_path, slang_warning  # noqa: E402


class SlangCheckTests(unittest.TestCase):
    def test_systemverilog_slang_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            bin_dir = Path(td) / "bin"
            bin_dir.mkdir()
            (bin_dir / "slang").touch()
            (bin_dir / "slang-hier").touch()
            self.assertIsNone(slang_warning(bin_dir / "slang"))

    def test_shader_slang_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            bin_dir = Path(td) / "shader-slang" / "bin"
            bin_dir.mkdir(parents=True)
            (bin_dir / "slang").touch()
            warning = slang_warning(bin_dir / "slang")
        self.assertIsNotNone(warning)
        assert warning is not None
        self.assertIn("Shader Slang", warning)
        self.assertIn("sv-lang", warning)

    def test_slang_without_slang_hier_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            bin_dir = Path(td) / "bin"
            bin_dir.mkdir()
            (bin_dir / "slang").touch()
            warning = slang_warning(bin_dir / "slang")
        self.assertIsNotNone(warning)
        assert warning is not None
        self.assertIn("no slang-hier", warning)

    def test_missing_slang_is_not_a_warning(self) -> None:
        with mock.patch("tren.tools.shutil.which", return_value=None):
            self.assertIsNone(slang_path())
            self.assertIsNone(slang_warning())


if __name__ == "__main__":
    unittest.main()
