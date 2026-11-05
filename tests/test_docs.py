"""The user documentation and its example designs stay runnable.

The link checks are pure. The elaboration checks skip outside the dev shell,
the same way the acceptance tests do.

Run:  nix develop . -c bash -c "PYTHONPATH=src python -m unittest discover -s tests"
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tren.cache import IrCache  # noqa: E402
from tren.elaborate import elaborate  # noqa: E402
from tren.importer import import_manifest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
EXAMPLES = DOCS / "examples"


class DocumentationLinkTests(unittest.TestCase):
    def test_readme_links_the_documents(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in (
            "quick_start.md",
            "command_reference.md",
            "advanced_usage.md",
            "configuration.md",
        ):
            with self.subTest(name=name):
                self.assertIn(f"docs/{name}", text)

    def test_the_example_directories_exist(self) -> None:
        for name in (
            "one-file",
            "multi-file",
            "multi-dir",
            "define",
            "generate",
            "parameter",
            "unresolved-module",
            "vivado",
        ):
            with self.subTest(name=name):
                self.assertTrue((EXAMPLES / name).is_dir())

    def test_the_example_index_names_every_example(self) -> None:
        text = (EXAMPLES / "README.md").read_text(encoding="utf-8")
        for name in (
            "one-file",
            "multi-file",
            "multi-dir",
            "define",
            "generate",
            "parameter",
            "unresolved-module",
            "vivado",
        ):
            with self.subTest(name=name):
                self.assertIn(f"`{name}/`", text)

    def test_example_commands_use_the_recorded_top_module(self) -> None:
        text = (EXAMPLES / "README.md").read_text(encoding="utf-8")
        for line in text.splitlines():
            if "`tren open" not in line:
                continue
            with self.subTest(line=line):
                if "docs/examples/vivado" in line:
                    self.assertNotIn("--top", line)
                else:
                    self.assertIn("--top", line)

    def test_the_new_documentation_is_ascii(self) -> None:
        paths = [
            DOCS / "quick_start.md",
            DOCS / "command_reference.md",
            DOCS / "advanced_usage.md",
            DOCS / "configuration.md",
            EXAMPLES / "README.md",
        ]
        paths.extend(EXAMPLES.rglob("*.sv"))
        paths.extend(EXAMPLES.rglob("*.svh"))
        paths.extend(EXAMPLES.rglob("*.f"))
        paths.extend(EXAMPLES.rglob("*.xpr"))
        for path in paths:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertTrue(path.read_text(encoding="utf-8").isascii())

    def test_the_user_guides_do_not_name_a_package_manager(self) -> None:
        for name in ("quick_start.md", "command_reference.md", "advanced_usage.md"):
            with self.subTest(name=name):
                text = (DOCS / name).read_text(encoding="utf-8").lower()
                self.assertNotIn("nix", text)


@unittest.skipUnless(
    shutil.which("yosys") and shutil.which("slang-hier") and shutil.which("slang"),
    "the elaboration backends are not on PATH",
)
class ExampleElaborationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory(prefix="tren-docs-")
        self.addCleanup(self.td.cleanup)

    def _design(self, target: Path, top: str | None = None):
        manifest = import_manifest(target, root=ROOT, top=top)
        cache = IrCache(Path(self.td.name) / "cache", root=ROOT)
        return manifest, elaborate(manifest, ROOT, cache=cache)

    def test_one_file_example(self) -> None:
        manifest, design = self._design(
            EXAMPLES / "one-file" / "counter.sv", "counter"
        )
        self.assertEqual(manifest.sources, ("docs/examples/one-file/counter.sv",))
        self.assertEqual(manifest.top, "counter")
        self.assertIsNotNone(design.scope("counter.u_count"))

    def test_multi_file_example(self) -> None:
        manifest, design = self._design(EXAMPLES / "multi-file", "alu_top")
        self.assertEqual(
            manifest.sources,
            (
                "docs/examples/multi-file/alu.sv",
                "docs/examples/multi-file/alu_top.sv",
            ),
        )
        self.assertEqual(manifest.top, "alu_top")
        self.assertIsNotNone(design.scope("alu_top.u_alu"))
        top = design.scope("alu_top")
        assert top is not None
        self.assertEqual({p.name: p.width for p in top.ports}["y"], 8)

    def test_multi_directory_example(self) -> None:
        manifest, design = self._design(
            EXAMPLES / "multi-dir" / "design.f", "alu_top"
        )
        self.assertEqual(
            manifest.sources,
            (
                "docs/examples/multi-dir/rtl/alu_top.sv",
                "docs/examples/multi-dir/ip/alu.sv",
            ),
        )
        self.assertEqual(manifest.top, "alu_top")
        for incdir in (
            "docs/examples/multi-dir/include",
            "docs/examples/multi-dir/ip",
            "docs/examples/multi-dir/rtl",
        ):
            with self.subTest(incdir=incdir):
                self.assertIn(incdir, manifest.incdirs)
        self.assertIsNotNone(design.scope("alu_top.u_alu"))

    def test_parameter_example(self) -> None:
        manifest, design = self._design(
            EXAMPLES / "parameter" / "design.f", "param_top"
        )
        self.assertEqual(
            manifest.sources, ("docs/examples/parameter/param_top.sv",)
        )
        self.assertEqual(manifest.params, (("WIDTH", "16"),))
        top = design.scope("param_top")
        assert top is not None
        self.assertEqual(
            {port.name: port.width for port in top.ports},
            {"a": 16, "b": 16, "y": 16},
        )

    def test_define_example(self) -> None:
        manifest, design = self._design(
            EXAMPLES / "define" / "design.f", "define_top"
        )
        self.assertEqual(
            manifest.sources, ("docs/examples/define/define_top.sv",)
        )
        self.assertEqual(manifest.defines, (("TOPW", "16"),))
        top = design.scope("define_top")
        assert top is not None
        self.assertEqual(
            {port.name: (port.width, port.range) for port in top.ports},
            {"a": (16, "[15:0]"), "y": (16, "[15:0]")},
        )

    def test_generate_example(self) -> None:
        manifest, design = self._design(
            EXAMPLES / "generate" / "design.f", "generate_top"
        )
        self.assertEqual(
            manifest.sources, ("docs/examples/generate/generate_top.sv",)
        )
        self.assertEqual(manifest.params, (("LANES", "4"),))
        names = {scope.name for scope in design.scopes}
        self.assertTrue(
            {
                "generate_top.g[0]",
                "generate_top.g[1]",
                "generate_top.g[2]",
                "generate_top.g[3]",
                "generate_top.g_wide",
                "generate_top.g_wide.u_wide",
            }
            <= names,
            names,
        )
        top = design.scope("generate_top")
        assert top is not None
        self.assertEqual(
            {port.name: port.width for port in top.ports},
            {"a": 8, "y": 8},
        )

        _, default_design = self._design(
            EXAMPLES / "generate" / "generate_top.sv", "generate_top"
        )
        default_names = {scope.name for scope in default_design.scopes}
        self.assertTrue(
            {"generate_top.g[0]", "generate_top.g[1]"} <= default_names,
            default_names,
        )
        self.assertNotIn("generate_top.g[2]", default_names)
        self.assertNotIn("generate_top.g_wide", default_names)

    def test_vivado_example(self) -> None:
        manifest, design = self._design(EXAMPLES / "vivado" / "project.xpr")
        self.assertEqual(manifest.top, "alu_top")
        self.assertEqual(
            set(manifest.sources),
            {
                "docs/examples/vivado/project.srcs/sources_1/new/alu.sv",
                "docs/examples/vivado/project.srcs/sources_1/new/alu_top.sv",
            },
        )
        self.assertIsNotNone(design.scope("alu_top.u_alu"))

    def test_unresolved_module_example(self) -> None:
        manifest, design = self._design(
            EXAMPLES / "unresolved-module" / "memory_top.sv", "memory_top"
        )
        self.assertEqual(
            manifest.sources,
            ("docs/examples/unresolved-module/memory_top.sv",),
        )
        missing = design.scope("memory_top.u_memory")
        assert missing is not None
        self.assertTrue(missing.blackbox)
        self.assertTrue(missing.unresolved)
        self.assertEqual(
            {port.name: (port.direction, port.width) for port in missing.ports},
            {
                "clk": ("unknown", 1),
                "address": ("unknown", 8),
                "write_data": ("unknown", 32),
                "read_data": ("unknown", 32),
            },
        )


if __name__ == "__main__":
    unittest.main()
