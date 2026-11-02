"""Tests for the layered configuration files and the `tren config` CLI.

Discovery is deterministic: defaults, one system file, one user file, and the
nearest project file up to the repository root. These tests build those files
in temporary directories, so no test reads the machine's real configuration.

Run:  nix develop . -c bash -c "PYTHONPATH=src python -m unittest discover -s tests"
"""

from __future__ import annotations

import io
import sys
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tren import __main__ as cli  # noqa: E402
from tren.config import (  # noqa: E402
    Config,
    ConfigError,
    ConfigFiles,
    OPTIONS,
    discover_files,
    find_project_file,
    load_config,
    main,
    render_export,
    render_origins,
    render_show,
)

MISSING = Path("/nonexistent/tren-config")


def _files(
    system: Path = MISSING,
    user: Path = MISSING,
    project: Path | None = None,
) -> ConfigFiles:
    return ConfigFiles(system=system, user=user, project=project)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class DefaultTests(unittest.TestCase):
    def test_missing_layers_yield_the_built_in_defaults(self) -> None:
        loaded = load_config(_files())
        self.assertEqual(loaded.config, Config())
        for option in OPTIONS:
            self.assertEqual(loaded.origin(option.key), "default")

    def test_defaults_are_the_documented_values(self) -> None:
        self.assertEqual(Config().cache_dir, ".cache/tren")
        self.assertEqual(Config().theme, "textual-dark")
        self.assertTrue(Config().line_numbers)
        self.assertTrue(Config().hierarchy_enabled)
        for field in (
            "hierarchy_scope",
            "hierarchy_module",
            "hierarchy_blackbox",
            "hierarchy_port",
            "hierarchy_inout",
            "hierarchy_net",
        ):
            self.assertEqual(getattr(Config(), field), "theme")
        self.assertEqual(Config().yank_format, "sv")
        self.assertEqual(Config().editor_command, "")


class PrecedenceTests(unittest.TestCase):
    def test_defaults_then_system_then_user_then_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            system = _write(root / "etc" / "tren" / "config.toml", "[cache]\ndir = 'system-cache'\n")
            user = _write(
                root / "home" / ".config" / "tren" / "config.toml",
                "[cache]\ndir = 'user-cache'\n[tui]\ntheme = 'nord'\n",
            )
            project = _write(
                root / "repo" / ".tren.toml",
                "[tui]\nline_numbers = false\n[yank]\nformat = 'vcd'\n",
            )
            loaded = load_config(_files(system=system, user=user, project=project))
            self.assertEqual(loaded.config.cache_dir, "user-cache")
            self.assertEqual(loaded.config.theme, "nord")
            self.assertFalse(loaded.config.line_numbers)
            self.assertEqual(loaded.config.yank_format, "vcd")
            self.assertEqual(loaded.origin("cache.dir"), str(user))
            self.assertEqual(loaded.origin("yank.format"), str(project))

    def test_project_layer_overrides_the_user_layer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user = _write(root / "user.toml", "[yank]\nformat = 'yosys'\n")
            project = _write(root / "project.toml", "[yank]\nformat = 'vcd'\n")
            loaded = load_config(_files(user=user, project=project))
            self.assertEqual(loaded.config.yank_format, "vcd")
            self.assertEqual(loaded.origin("yank.format"), str(project))

    def test_nested_hierarchy_table_is_read_by_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _write(
                Path(tmp) / ".tren.toml",
                "[tui.hierarchy]\n"
                "enabled = false\n"
                "scope = '#a6c9ff'\n"
                "port = 'none'\n"
                "inout = '#d17e92'\n",
            )
            loaded = load_config(_files(project=project))
            self.assertFalse(loaded.config.hierarchy_enabled)
            self.assertEqual(loaded.config.hierarchy_scope, "#a6c9ff")
            self.assertEqual(loaded.config.hierarchy_port, "none")
            self.assertEqual(loaded.config.hierarchy_inout, "#d17e92")
            self.assertEqual(loaded.config.hierarchy_net, "theme")

    def test_loaded_files_are_the_layers_in_order(self) -> None:
        files = _files(Path("s"), Path("u"), Path("p"))
        self.assertEqual(files.layers(), (Path("s"), Path("u"), Path("p")))


class DiscoveryTests(unittest.TestCase):
    def test_nearest_project_file_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            (repo / ".git").mkdir(parents=True)
            _write(repo / ".tren.toml", "[cache]\ndir = 'root'\n")
            nested = repo / "a" / "b"
            nested.mkdir(parents=True)
            near = _write(repo / "a" / ".tren.toml", "[cache]\ndir = 'near'\n")
            self.assertEqual(find_project_file(nested), near)

    def test_discovery_stops_at_the_repository_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outer = Path(tmp) / "outer"
            _write(outer / ".tren.toml", "[cache]\ndir = 'outside'\n")
            repo = outer / "repo"
            (repo / ".git").mkdir(parents=True)
            (repo / "sub").mkdir()
            self.assertIsNone(find_project_file(repo / "sub"))
            project = _write(repo / ".tren.toml", "[cache]\ndir = 'inside'\n")
            self.assertEqual(find_project_file(repo / "sub"), project)

    def test_outside_a_repository_only_the_current_directory_is_searched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / ".tren.toml", "[cache]\ndir = 'parent'\n")
            child = root / "child"
            child.mkdir()
            self.assertIsNone(find_project_file(child))

    def test_user_file_follows_xdg_config_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            loaded = discover_files(
                start=tmp,
                env={"HOME": str(home), "XDG_CONFIG_HOME": str(Path(tmp) / "xdg")},
            )
            self.assertEqual(loaded.user, Path(tmp) / "xdg" / "tren" / "config.toml")

    def test_relative_xdg_config_home_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            loaded = discover_files(
                start=tmp,
                env={"HOME": str(home), "XDG_CONFIG_HOME": "relative"},
            )
            self.assertEqual(loaded.user, home / ".config" / "tren" / "config.toml")

    def test_environment_overrides_the_fixed_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loaded = discover_files(
                start=tmp,
                env={
                    "TREN_SYSTEM_CONFIG": "/custom/system.toml",
                    "TREN_USER_CONFIG": "/custom/user.toml",
                },
            )
        self.assertEqual(loaded.system, Path("/custom/system.toml"))
        self.assertEqual(loaded.user, Path("/custom/user.toml"))

    def test_environment_overrides_the_project_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loaded = discover_files(
                start=tmp,
                env={"TREN_PROJECT_CONFIG": "/custom/project.toml"},
            )
        self.assertEqual(loaded.project, Path("/custom/project.toml"))


class ErrorTests(unittest.TestCase):
    def _error(self, text: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            project = _write(Path(tmp) / ".tren.toml", text)
            with self.assertRaises(ConfigError) as caught:
                load_config(_files(project=project))
            return str(caught.exception)

    def test_unknown_option_is_an_error(self) -> None:
        self.assertIn(
            "unknown option 'cache.directory'",
            self._error("[cache]\ndirectory = 'x'\n"),
        )

    def test_unknown_section_is_an_error(self) -> None:
        self.assertIn(
            "unknown option 'colour.theme'",
            self._error("[colour]\ntheme = 'nord'\n"),
        )

    def test_wrong_type_is_an_error(self) -> None:
        self.assertIn("must be a boolean", self._error("[tui]\nline_numbers = 'yes'\n"))

    def test_bad_yank_format_is_an_error(self) -> None:
        self.assertIn(
            "yank.format must be one of",
            self._error("[yank]\nformat = 'quests'\n"),
        )

    def test_unknown_textual_theme_is_an_error(self) -> None:
        self.assertIn(
            "tui.theme must be a Textual theme name",
            self._error("[tui]\ntheme = 'neon'\n"),
        )

    def test_bad_hierarchy_colour_is_an_error(self) -> None:
        self.assertIn(
            "must be 'theme', 'none', or a Textual colour",
            self._error("[tui.hierarchy]\nscope = 'not-a-colour'\n"),
        )

    def test_invalid_toml_is_an_error(self) -> None:
        self.assertIn("invalid TOML", self._error("[cache\n"))

    def test_top_level_scalar_is_an_error(self) -> None:
        self.assertIn("must be a table", self._error("cache = 'x'\n"))

    def test_project_file_cannot_set_editor_command(self) -> None:
        self.assertIn(
            "may only be set in the system or user configuration",
            self._error("[editor]\ncommand = 'nvim'\n"),
        )

    def test_user_file_can_set_editor_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user = _write(Path(tmp) / "user.toml", "[editor]\ncommand = 'nvim'\n")
            loaded = load_config(_files(user=user))
        self.assertEqual(loaded.config.editor_command, "nvim")

    def test_project_file_may_clear_the_editor_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user = _write(root / "user.toml", "[editor]\ncommand = 'nvim'\n")
            project = _write(root / "project.toml", "[editor]\ncommand = ''\n")
            loaded = load_config(_files(user=user, project=project))
        self.assertEqual(loaded.config.editor_command, "")


class ExportTests(unittest.TestCase):
    def test_export_contains_every_option_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user = _write(Path(tmp) / "user.toml", "[editor]\ncommand = 'nvim'\n")
            project = _write(
                Path(tmp) / ".tren.toml",
                "[cache]\ndir = '/tmp/cache'\n"
                "[tui]\ntheme = 'nord'\nline_numbers = false\n"
                "[yank]\nformat = 'vcd'\n",
            )
            loaded = load_config(_files(user=user, project=project))
            text = render_export(loaded)
            self.assertTrue(text.isascii())
            self.assertEqual(
                tomllib.loads(text),
                {
                    "cache": {"dir": "/tmp/cache"},
                    "editor": {"command": "nvim"},
                    "tui": {
                        "theme": "nord",
                        "line_numbers": False,
                        "hierarchy": {
                            "enabled": True,
                            "scope": "theme",
                            "module": "theme",
                            "blackbox": "theme",
                            "port": "theme",
                            "inout": "theme",
                            "net": "theme",
                        },
                    },
                    "yank": {"format": "vcd"},
                },
            )
            exported = _write(root / "exported.toml", text)
            reloaded = load_config(_files(user=exported))
        self.assertEqual(reloaded.config, loaded.config)

    def test_export_at_defaults_reloads_the_defaults(self) -> None:
        loaded = load_config(_files())
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp) / "export.toml", render_export(loaded))
            reloaded = load_config(_files(project=path))
        self.assertEqual(reloaded.config, Config())


class ShowTests(unittest.TestCase):
    def test_show_reports_files_and_values(self) -> None:
        loaded = load_config(_files())
        text = render_show(loaded)
        self.assertIn("system", text)
        self.assertIn("user", text)
        self.assertIn("project", text)
        self.assertIn("yank.format", text)
        self.assertIn("[default]", text)
        self.assertIn("(no file)", text)

    def test_show_marks_a_configured_value_with_its_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _write(Path(tmp) / ".tren.toml", "[yank]\nformat = 'vcd'\n")
            loaded = load_config(_files(project=project))
            shown = render_show(loaded)
            self.assertIn('"vcd"', shown)
            self.assertIn(str(project), shown)


class OriginsTests(unittest.TestCase):
    def test_defaults_have_no_file_origins(self) -> None:
        self.assertEqual(
            render_origins(load_config(_files())),
            "no configuration values set by a TOML file\n",
        )

    def test_groups_the_effective_values_by_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            system = _write(root / "system.toml", "[cache]\ndir = 'system-cache'\n")
            user = _write(
                root / "user.toml",
                "[tui]\ntheme = 'nord'\n[yank]\nformat = 'yosys'\n",
            )
            project = _write(
                root / "project.toml",
                "[tui]\nline_numbers = false\n[yank]\nformat = 'vcd'\n",
            )
            text = render_origins(
                load_config(_files(system=system, user=user, project=project))
            )
        self.assertIn(f'{system}\n  cache.dir = "system-cache"', text)
        self.assertIn(f'{user}\n  tui.theme = "nord"', text)
        self.assertIn(f'{project}\n  tui.line_numbers = false', text)
        self.assertIn('yank.format = "vcd"', text)
        self.assertNotIn("yosys", text, "an overridden value must not appear")
        self.assertNotIn("editor.command", text, "defaults must not appear")
        self.assertLess(text.index(str(system)), text.index(str(user)))
        self.assertLess(text.index(str(user)), text.index(str(project)))


class CliTests(unittest.TestCase):
    def _loaded(self):
        return load_config(_files())

    def test_show_is_the_default_command(self) -> None:
        out = io.StringIO()
        with mock.patch("tren.config.load_config", return_value=self._loaded()):
            with redirect_stdout(out):
                code = main([])
        self.assertEqual(code, 0)
        self.assertIn("options:", out.getvalue())
        self.assertIn("cache.dir", out.getvalue())

    def test_export_to_stdout_is_valid_toml(self) -> None:
        out = io.StringIO()
        with mock.patch("tren.config.load_config", return_value=self._loaded()):
            with redirect_stdout(out):
                code = main(["export"])
        self.assertEqual(code, 0)
        self.assertEqual(tomllib.loads(out.getvalue())["cache"], {"dir": ".cache/tren"})

    def test_export_writes_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "config.toml"
            with mock.patch("tren.config.load_config", return_value=self._loaded()):
                with redirect_stdout(io.StringIO()):
                    code = main(["export", "--out", str(out)])
            self.assertEqual(code, 0)
            self.assertTrue(out.is_file())
            self.assertIn("[cache]", out.read_text(encoding="utf-8"))

    def test_export_accepts_a_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "config"
            out.mkdir()
            with mock.patch("tren.config.load_config", return_value=self._loaded()):
                with redirect_stdout(io.StringIO()):
                    code = main(["export", "--out", str(out)])
            self.assertEqual(code, 0)
            self.assertTrue((out / "config.toml").is_file())

    def test_invalid_config_prints_the_error(self) -> None:
        err = io.StringIO()
        with mock.patch(
            "tren.config.load_config",
            side_effect=ConfigError("bad value"),
        ):
            with redirect_stderr(err):
                code = main(["show"])
        self.assertEqual(code, 2)
        self.assertIn("tren config: bad value", err.getvalue())

    def test_debug_config_prints_toml(self) -> None:
        out = io.StringIO()
        with mock.patch("tren.config.load_config", return_value=self._loaded()):
            with redirect_stdout(out):
                code = cli.main(["--debug-config"])
        self.assertEqual(code, 0)
        self.assertEqual(tomllib.loads(out.getvalue())["yank"], {"format": "sv"})

    def test_debug_config_origins_prints_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user = _write(Path(tmp) / "user.toml", "[yank]\nformat = 'vcd'\n")
            loaded = load_config(_files(user=user))
        out = io.StringIO()
        with mock.patch("tren.config.load_config", return_value=loaded):
            with redirect_stdout(out):
                code = cli.main(["--debug-config-origins"])
        self.assertEqual(code, 0)
        self.assertIn(str(user), out.getvalue())
        self.assertIn('yank.format = "vcd"', out.getvalue())

    def test_debug_flag_rejects_extra_arguments(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = cli.main(["--debug-config", "extra"])
        self.assertEqual(code, 2)
        self.assertIn("takes no arguments", err.getvalue())

    def test_debug_flag_reports_a_config_error(self) -> None:
        err = io.StringIO()
        with mock.patch(
            "tren.config.load_config",
            side_effect=ConfigError("bad value"),
        ):
            with redirect_stderr(err):
                code = cli.main(["--debug-config"])
        self.assertEqual(code, 2)
        self.assertIn("tren: bad value", err.getvalue())


if __name__ == "__main__":
    unittest.main()
