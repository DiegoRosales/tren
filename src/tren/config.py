"""Configuration: built-in defaults, TOML layers, and the `tren config` CLI.

TREN reads an optional ``config.toml`` from three places and merges them in a
fixed order:

    built-in defaults
    system   /etc/tren/config.toml
    user     $XDG_CONFIG_HOME/tren/config.toml (or ~/.config/tren/config.toml)
    project  .tren.toml, the nearest file from the current directory up to
             the repository root

Each layer is one file, and a key in a later layer replaces the same key in an
earlier layer. The nearest project file wins. Discovery stops at the directory
that holds ``.git`` (the repository root); outside a repository only the
current directory is searched. The result is the same for the same working
directory and files.

``tren config show`` prints the effective value and its origin for every
option. ``tren config export`` writes a complete TOML template at the
effective values, so the same file serves as a template and as a snapshot.

An unknown option or a value of the wrong type is an error. A silently
ignored typo is worse than a stopped command.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .clipboard import PROVIDERS
from .colors import NONE, THEME
from .formats import BY_NAME

PROJECT_FILE = ".tren.toml"
SYSTEM_PATH = Path("/etc/tren/config.toml")
DEFAULT_ORIGIN = "default"

HIERARCHY_COLOR_KEYS = frozenset(
    {
        "tui.hierarchy.scope",
        "tui.hierarchy.module",
        "tui.hierarchy.blackbox",
        "tui.hierarchy.port",
        "tui.hierarchy.inout",
        "tui.hierarchy.net",
    }
)

# Environment overrides for the three layer paths. Tests and unusual installs
# need them; normal discovery never reads them.
SYSTEM_ENV = "TREN_SYSTEM_CONFIG"
USER_CONFIG_ENV = "TREN_USER_CONFIG"
PROJECT_ENV = "TREN_PROJECT_CONFIG"


@dataclass(frozen=True)
class Option:
    """One configuration key and the ``Config`` field it fills."""

    key: str
    field: str
    default: object
    description: str


OPTIONS: tuple[Option, ...] = (
    Option(
        "cache.dir",
        "cache_dir",
        ".cache/tren",
        "IR cache directory. A relative path resolves from the current "
        "working directory.",
    ),
    Option(
        "editor.command",
        "editor_command",
        "",
        "Editor command used by e and E. TREN_EDITOR wins; otherwise this "
        "value, then EDITOR, then VISUAL. A project file may only leave it "
        "empty.",
    ),
    Option(
        "tui.theme",
        "theme",
        "textual-dark",
        "Textual theme name for the TUI chrome.",
    ),
    Option(
        "tui.line_numbers",
        "line_numbers",
        True,
        "Show the line-number gutter in the source pane at startup.",
    ),
    Option(
        "tui.hierarchy.enabled",
        "hierarchy_enabled",
        True,
        "Colour hierarchy rows by kind. False gives a monochrome tree.",
    ),
    Option(
        "tui.hierarchy.scope",
        "hierarchy_scope",
        THEME,
        "Colour for module instances: theme, none, or a colour name or hex.",
    ),
    Option(
        "tui.hierarchy.module",
        "hierarchy_module",
        THEME,
        "Colour for module definitions and generate blocks: theme, none, "
        "or a colour name or hex.",
    ),
    Option(
        "tui.hierarchy.blackbox",
        "hierarchy_blackbox",
        THEME,
        "Colour for black boxes and user cuts: theme, none, or a colour "
        "name or hex.",
    ),
    Option(
        "tui.hierarchy.port",
        "hierarchy_port",
        THEME,
        "Colour for input and output port leaves: theme, none, or a colour "
        "name or hex. The theme role follows the port direction.",
    ),
    Option(
        "tui.hierarchy.inout",
        "hierarchy_inout",
        THEME,
        "Colour for inout port leaves: theme, none, or a colour name or "
        "hex.",
    ),
    Option(
        "tui.hierarchy.net",
        "hierarchy_net",
        THEME,
        "Colour for net leaves: theme, none, or a colour name or hex.",
    ),
    Option(
        "yank.format",
        "yank_format",
        "sv",
        "Initial format for y, Y and the status path: sv, vivado, yosys or vcd.",
    ),
    Option(
        "yank.provider",
        "yank_provider",
        "auto",
        "Clipboard provider for y: auto, tmux or osc52. Auto uses tmux "
        "inside a tmux session, otherwise OSC 52.",
    ),
)

OPTIONS_BY_KEY: dict[str, Option] = {option.key: option for option in OPTIONS}


class ConfigError(Exception):
    """A configuration file is unreadable, malformed or invalid."""


@dataclass(frozen=True)
class Config:
    """The effective settings after all layers are merged."""

    cache_dir: str = ".cache/tren"
    editor_command: str = ""
    theme: str = "textual-dark"
    line_numbers: bool = True
    hierarchy_enabled: bool = True
    hierarchy_scope: str = THEME
    hierarchy_module: str = THEME
    hierarchy_blackbox: str = THEME
    hierarchy_port: str = THEME
    hierarchy_inout: str = THEME
    hierarchy_net: str = THEME
    yank_format: str = "sv"
    yank_provider: str = "auto"


@dataclass(frozen=True)
class ConfigFiles:
    """The files discovery selected, whether or not each one exists."""

    system: Path
    user: Path
    project: Path | None

    def layers(self) -> tuple[Path, ...]:
        paths = (self.system, self.user, self.project)
        return tuple(p for p in paths if p is not None)


@dataclass(frozen=True)
class LoadedConfig:
    """Effective values plus where each one came from."""

    config: Config
    values: dict[str, object]
    origins: dict[str, str]
    files: ConfigFiles

    def origin(self, key: str) -> str:
        return self.origins[key]


def _home(env: Mapping[str, str]) -> Path:
    home = env.get("HOME")
    return Path(home) if home else Path.home()


def _user_path(env: Mapping[str, str]) -> Path:
    override = env.get(USER_CONFIG_ENV)
    if override:
        return Path(override).expanduser().resolve()
    xdg = env.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else _home(env) / ".config"
    return base / "tren" / "config.toml"


def _system_path(env: Mapping[str, str]) -> Path:
    override = env.get(SYSTEM_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return SYSTEM_PATH


def _find_repo_root(start: Path) -> Path | None:
    for directory in (start, *start.parents):
        if (directory / ".git").exists():
            return directory
    return None


def find_project_file(start: str | Path) -> Path | None:
    """The nearest ``.tren.toml`` from ``start`` up to the repository root.

    Outside a repository only ``start`` is searched. Return ``None`` when no
    project file exists.
    """
    start = Path(start).resolve()
    root = _find_repo_root(start)
    stop = root if root is not None else start
    for directory in (start, *start.parents):
        candidate = directory / PROJECT_FILE
        if candidate.is_file():
            return candidate
        if directory == stop:
            break
    return None


def _project_path(start: Path, env: Mapping[str, str]) -> Path | None:
    override = env.get(PROJECT_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return find_project_file(start)


def discover_files(
    start: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> ConfigFiles:
    """Resolve the three layer paths without reading them."""
    env = os.environ if env is None else env
    start = Path.cwd() if start is None else Path(start)
    return ConfigFiles(
        system=_system_path(env),
        user=_user_path(env),
        project=_project_path(start, env),
    )


def _kind(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


def _known_themes() -> tuple[str, ...]:
    from textual.theme import BUILTIN_THEMES

    return tuple(sorted(BUILTIN_THEMES))


def _valid_color(value: str) -> bool:
    from textual.color import Color, ColorParseError

    try:
        Color.parse(value)
    except ColorParseError:
        return False
    return True


def _validate(key: str, value: object, origin: Path) -> None:
    option = OPTIONS_BY_KEY[key]
    if type(value) is not type(option.default):
        raise ConfigError(
            f"{origin}: {key} must be a {_kind(option.default)}, "
            f"got a {_kind(value)}"
        )
    if key == "cache.dir" and not str(value).strip():
        raise ConfigError(f"{origin}: cache.dir must not be empty")
    if key == "yank.format" and value not in BY_NAME:
        names = ", ".join(sorted(BY_NAME))
        raise ConfigError(
            f"{origin}: yank.format must be one of {names}, got {value!r}"
        )
    if key == "yank.provider" and value not in PROVIDERS:
        names = ", ".join(PROVIDERS)
        raise ConfigError(
            f"{origin}: yank.provider must be one of {names}, got {value!r}"
        )
    if key == "tui.theme" and value not in _known_themes():
        names = ", ".join(_known_themes())
        raise ConfigError(
            f"{origin}: tui.theme must be a Textual theme name "
            f"({names}), got {value!r}"
        )
    if (
        key in HIERARCHY_COLOR_KEYS
        and value not in (THEME, NONE)
        and not _valid_color(str(value))
    ):
        raise ConfigError(
            f"{origin}: {key} must be 'theme', 'none', or a Textual "
            f"colour, got {value!r}"
        )


def _read_layer(path: Path) -> dict[str, object]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"{path}: cannot read the file: {exc}") from exc
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc

    values: dict[str, object] = {}

    def read_table(prefix: str, body: Mapping[str, object]) -> None:
        for name, value in body.items():
            key = f"{prefix}.{name}" if prefix else name
            if isinstance(value, dict):
                read_table(key, value)
                continue
            if key not in OPTIONS_BY_KEY:
                known = ", ".join(OPTIONS_BY_KEY)
                raise ConfigError(
                    f"{path}: unknown option {key!r} (known options: {known})"
                )
            _validate(key, value, path)
            values[key] = value

    for section, body in data.items():
        if not isinstance(body, dict):
            raise ConfigError(
                f"{path}: {section!r} must be a table, not a top-level value"
            )
        read_table(section, body)
    return values


def load_config(
    files: ConfigFiles | None = None,
    *,
    start: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> LoadedConfig:
    """Merge every layer into the effective configuration.

    ``files`` bypasses discovery. Otherwise the paths come from ``start``
    (default: current directory) and ``env`` (default: the process
    environment).
    """
    files = discover_files(start, env) if files is None else files
    values = {option.key: option.default for option in OPTIONS}
    origins = {option.key: DEFAULT_ORIGIN for option in OPTIONS}
    for path in files.layers():
        if not path.is_file():
            continue
        for key, value in _read_layer(path).items():
            if (
                path == files.project
                and key == "editor.command"
                and value
            ):
                raise ConfigError(
                    f"{path}: editor.command may only be set in the system "
                    "or user configuration; a project file must not choose "
                    "an executable"
                )
            values[key] = value
            origins[key] = str(path)
    config = Config(**{option.field: values[option.key] for option in OPTIONS})
    return LoadedConfig(config=config, values=values, origins=origins, files=files)


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(str(value), ensure_ascii=True)


def render_export(loaded: LoadedConfig) -> str:
    """A complete TOML template at the effective values."""
    lines = [
        "# TREN configuration.",
        "# Every key is present and set to the effective value, so this file",
        "# works as a template and as a snapshot of the current settings.",
        "# Precedence: defaults -> system -> user -> project.",
        "# A relative cache.dir resolves from the current working directory.",
    ]
    section: str | None = None
    for option in OPTIONS:
        section_name, _, name = option.key.rpartition(".")
        if section_name != section:
            section = section_name
            lines.extend(["", f"[{section}]"])
        lines.extend(
            [
                "",
                f"# {option.description}",
                f"{name} = {_toml_value(loaded.values[option.key])}",
            ]
        )
    return "\n".join(lines) + "\n"


def render_origins(loaded: LoadedConfig) -> str:
    """Only the effective values that a TOML file set, grouped by file."""
    groups: dict[str, list[str]] = {}
    for option in OPTIONS:
        origin = loaded.origins[option.key]
        if origin != DEFAULT_ORIGIN:
            groups.setdefault(origin, []).append(option.key)
    if not groups:
        return "no configuration values set by a TOML file\n"

    lines: list[str] = []
    for path in (str(p) for p in loaded.files.layers()):
        keys = groups.get(path)
        if not keys:
            continue
        if lines:
            lines.append("")
        lines.append(path)
        for key in keys:
            value = _toml_value(loaded.values[key])
            lines.append(f"  {key} = {value}")
    return "\n".join(lines) + "\n"


def render_show(loaded: LoadedConfig) -> str:
    """Every effective value with its source file or ``default``."""
    lines = ["files:"]
    for label, path in (
        ("system", loaded.files.system),
        ("user", loaded.files.user),
        ("project", loaded.files.project),
    ):
        if path is None:
            lines.append(f"  {label:<8} (no file)")
        else:
            state = "loaded" if path.is_file() else "missing"
            lines.append(f"  {label:<8} {path} ({state})")
    lines.extend(["", "options:"])
    for option in OPTIONS:
        value = _toml_value(loaded.values[option.key])
        origin = loaded.origins[option.key]
        lines.append(f"  {option.key:<24} = {value:<20} [{origin}]")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tren config",
        description=(
            "Show the effective configuration, or export a complete TOML "
            "template."
        ),
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("show", help="print every value and its origin (default)")
    export = sub.add_parser("export", help="write a complete TOML template")
    export.add_argument(
        "--out", type=Path, default=None,
        help=(
            "write the template here instead of standard output (a "
            "directory writes <dir>/config.toml)"
        ),
    )
    args = parser.parse_args(argv)

    try:
        loaded = load_config()
    except ConfigError as exc:
        print(f"tren config: {exc}", file=sys.stderr)
        return 2

    if args.command == "export":
        text = render_export(loaded)
        if args.out is None:
            sys.stdout.write(text)
            return 0
        out = args.out / "config.toml" if args.out.is_dir() else args.out
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
        except OSError as exc:
            print(f"tren config: cannot write {out}: {exc}", file=sys.stderr)
            return 2
        print(f"wrote {out}")
        return 0
    print(render_show(loaded), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
