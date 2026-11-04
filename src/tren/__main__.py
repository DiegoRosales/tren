"""``python -m tren`` - the subcommand front door.

Commands:

    import [<target>] [-f PATH]... [-D NAME[=VALUE]]... [-G NAME=VALUE]...
                [--out PATH] [--name NAME] [--top TOP]
                emit a design manifest for a .f filelist, a .sv file, a
                directory of sources, or an explicit -f list (tren.importer)
    elaborate <manifest> [--root DIR] [--cache DIR] [--refresh]
                elaborate a manifest into the TREN IR (tren.elaborate)
    open [<target>] [-f PATH]... [-D NAME[=VALUE]]... [-G NAME=VALUE]...
                [--root DIR] [--cache DIR] [--refresh] [--out PATH]
                import the target (or the -f list) unless it is a manifest,
                elaborate it and open the TUI (tren.bringup)
    config [show|export] [--out PATH]
                show the effective settings with their origins, or export a
                complete TOML template (tren.config)

Options:

    --version   print the package version, source SHA and Python version
    --version-debug
                print the version plus interpreter, platform, package,
                dependency, external tool and configuration paths

Debug flags:

    --debug-config
                print the effective configuration as TOML and exit
    --debug-config-origins
                print the values a TOML file set, grouped by file path, and exit

A command name is required. A target alone is not a command.
"""

from __future__ import annotations

import importlib.metadata
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__

_SOURCE_PACKAGE = Path("src") / "tren"


def _source_root() -> Path | None:
    """Return the checkout root when this package is the checkout's src/tren."""
    package = Path(__file__).resolve().parent
    for directory in package.parents:
        if not (directory / ".git").exists():
            continue
        if (directory / _SOURCE_PACKAGE).resolve() == package:
            return directory
        return None
    return None


def _git_output(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout


def _source_sha(root: Path | None = None) -> str | None:
    """Return the short commit SHA when tren runs from a source checkout."""
    root = _source_root() if root is None else root
    if root is None:
        return None
    sha = (_git_output(root, "rev-parse", "--short", "HEAD") or "").strip()
    return sha or None


def _source_dirty(root: Path) -> bool | None:
    output = _git_output(root, "status", "--porcelain")
    return None if output is None else bool(output.strip())


def _installed_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


def _tool_lines(name: str, version_flag: str) -> list[str]:
    """Version line and path for one external tool, with a missing warning."""
    path = shutil.which(name)
    if path is None:
        return [
            f"warning: {name} not found on PATH",
            f"{name}: not found",
        ]
    try:
        result = subprocess.run(
            [path, version_flag],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return [
            f"warning: {name} could not run: {exc}",
            f"{name}: unknown ({path})",
        ]
    output = (result.stdout or "").strip() or (result.stderr or "").strip()
    version = output.splitlines()[0].strip() if output else ""
    if version:
        version = version.split(" (", 1)[0].strip()
        if version.casefold().startswith(name.casefold()):
            version = version[len(name) :].strip()
        if version.casefold().startswith("version "):
            version = version[len("version ") :].strip()
    if not version:
        return [
            f"warning: {name} at {path} did not report a version",
            f"{name}: unknown ({path})",
        ]
    return [f"{name}: {version} ({path})"]


def _version_line(sha: str | None) -> str:
    details = [f"Python {platform.python_version()}"]
    if sha:
        details.insert(0, f"dev-{sha}")
    return f"tren {__version__} ({', '.join(details)})"


def _version_debug() -> str:
    root = _source_root()
    sha = _source_sha(root) if root else None
    lines = [
        _version_line(sha),
        f"python: {platform.python_version()}",
        f"implementation: {platform.python_implementation()}",
        f"executable: {sys.executable or 'unknown'}",
        f"platform: {platform.system()} {platform.release()} {platform.machine()}",
        f"package: {Path(__file__).resolve().parent}",
    ]
    if root is None:
        lines.append("source: installed")
    else:
        dirty = _source_dirty(root)
        state = "unknown" if dirty is None else ("dirty" if dirty else "clean")
        lines.append(f"source: {root} (git {sha or 'unknown'}, {state})")
    try:
        cwd = str(Path.cwd())
    except OSError:
        cwd = "unknown"
    lines.append(f"cwd: {cwd}")
    lines.append(f"textual: {_installed_version('textual')}")
    lines.append(f"rich: {_installed_version('rich')}")
    lines.extend(_tool_lines("yosys", "-V"))
    lines.extend(_tool_lines("slang", "--version"))

    from .config import discover_files

    lines.append("config:")
    files = discover_files()
    for label, path in (
        ("system", files.system),
        ("user", files.user),
        ("project", files.project),
    ):
        if path is None:
            lines.append(f"  {label}: none")
        else:
            if path.is_file():
                state = "file"
            elif path.exists():
                state = "not a file"
            else:
                state = "missing"
            lines.append(f"  {label}: {path} ({state})")
    return "\n".join(lines)

_USAGE = """\
usage: tren <command> [args]

options:
  -h, --help    show this help and exit
  --version     show the TREN version, source SHA and Python version
  --version-debug
                show the version plus interpreter, platform, package,
                dependency, external tool and configuration paths

commands:
  import [<target>] [-f PATH]... [-D NAME[=VALUE]]... [-G NAME=VALUE]...
                [--out PATH] [--name NAME] [--top TOP]
                emit a design manifest for a .f filelist, a .sv file, a
                directory of sources, or an explicit -f list
  elaborate <manifest> [--root DIR] [--cache DIR] [--refresh]
                elaborate a manifest into the TREN IR
  open [<target>] [-f PATH]... [-D NAME[=VALUE]]... [-G NAME=VALUE]...
                [--root DIR] [--cache DIR] [--refresh] [--out PATH]
                import the target (or the -f list) unless it is a manifest,
                elaborate it and open the TUI
  config [show|export] [--out PATH]
                show the effective settings with their origins, or export a
                complete TOML template

debug flags:
  --debug-config
                print the effective configuration as TOML and exit
  --debug-config-origins
                print the values a TOML file set, grouped by file path, and exit

Run 'tren open --help' for the full option list.
"""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(_USAGE)
        return 0
    if argv[0] in ("-V", "--version"):
        if len(argv) != 1:
            print(f"tren: {argv[0]} takes no arguments", file=sys.stderr)
            return 2
        print(_version_line(_source_sha()))
        return 0
    if argv[0] == "--version-debug":
        if len(argv) != 1:
            print(f"tren: {argv[0]} takes no arguments", file=sys.stderr)
            return 2
        print(_version_debug())
        return 0
    if argv[0] in ("--debug-config", "--debug-config-origins"):
        if len(argv) != 1:
            print(f"tren: {argv[0]} takes no arguments", file=sys.stderr)
            return 2
        from .config import ConfigError, load_config, render_export, render_origins

        try:
            loaded = load_config()
        except ConfigError as exc:
            print(f"tren: {exc}", file=sys.stderr)
            return 2
        text = (
            render_export(loaded)
            if argv[0] == "--debug-config"
            else render_origins(loaded)
        )
        sys.stdout.write(text)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "import":
        from .importer import main as import_main
        return import_main(rest)
    if cmd == "elaborate":
        from .elaborate import main as elaborate_main
        return elaborate_main(rest)
    if cmd == "open":
        from .bringup import main as bringup_main
        return bringup_main(rest, prog="tren open")
    if cmd == "config":
        from .config import main as config_main
        return config_main(rest)
    # A target alone is not a command. The slot stays free for a future
    # entry point, so it must not silently open a design.
    print(f"tren: unknown command: {cmd}", file=sys.stderr)
    print(
        "Run 'tren open <target>' to open a design, or 'tren --help' for "
        "the command list.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
