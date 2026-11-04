"""One-command bringup: target -> manifest -> IR -> TUI.

``tren open <target>`` takes a source tree to the explorer in one step. The
target is either a manifest JSON or anything an importer accepts (``.f``
filelist, ``.sv``/``.v`` file, source directory, Vivado ``.xpr``). A repeated
``-f``/``--file`` option lists the sources instead of a target. A source
target is imported in memory; the manifest is written only when ``--out`` is
given. The elaborator then runs in-process and the TUI opens the result.

The two subcommands stay available for scripted, non-interactive use:
``tren import`` writes a manifest and ``tren elaborate`` builds only the IR.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from .config import Config, ConfigError, load_config
from .elaborate import IrCache, design_summary, elaborate, tool_failure_message
from .importer import import_manifest, import_sources, parse_param
from .ir import Design
from .manifest import Manifest


def is_manifest_target(target: str | Path) -> bool:
    """A manifest target is an existing ``.json`` file; all else is imported."""
    target = Path(target)
    return target.is_file() and target.suffix == ".json"


def _manifest_out_path(out: str | Path) -> Path:
    """Resolve ``--out``; an existing directory receives ``manifest.json``."""
    path = Path(out).resolve()
    return path / "manifest.json" if path.is_dir() else path


def load_target(
    target: str | Path | None = None,
    *,
    files: list[str] | tuple[str, ...] | None = None,
    root: str | Path | None = None,
    cache: str | Path | None = None,
    refresh: bool = False,
    out: str | Path | None = None,
    name: str | None = None,
    top: str | None = None,
    params: list[tuple[str, str]] | tuple[tuple[str, str], ...] | None = None,
    fmt: str = "auto",
    warnings_out: list[str] | None = None,
) -> tuple[Manifest, Design]:
    """Import (or read) the target, then elaborate it, all in one process.

    ``files`` lists the sources explicitly, as ``-f``/``--file`` does. It
    replaces ``target``; give one or the other. ``root`` is the anchor for
    stored paths and tool invocation (default: current directory). ``out``
    writes the imported manifest; without it the manifest stays in memory.
    ``params`` are top-level parameter overrides. Importer warnings go to
    ``warnings_out`` when given, so the caller can show them before the TUI
    takes the screen.
    """
    root = Path.cwd() if root is None else Path(root)
    if files is not None:
        if target is not None:
            raise ValueError("give a target or -f/--file sources, not both")
        if fmt != "auto":
            raise ValueError("--format does not apply with -f/--file")
        manifest = import_sources(
            files,
            root=root,
            name=name,
            top=top,
            params=params,
            warnings_out=warnings_out,
        )
        if out is not None:
            out_path = _manifest_out_path(out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            manifest.write(out_path)
    else:
        if target is None:
            raise ValueError("a target or at least one -f/--file is required")
        target = Path(target)
        if is_manifest_target(target):
            if (
                out is not None
                or name is not None
                or top is not None
                or params
                or fmt != "auto"
            ):
                raise ValueError(
                    "--out/--name/--top/--param/--format only apply when "
                    "importing a source target"
                )
            manifest = Manifest.read(target)
            duplicates = manifest.duplicate_params()
            if warnings_out is not None:
                for name, value in duplicates.items():
                    warnings_out.append(
                        f"parameter {name} is defined more than once; "
                        f"using the last value {value}"
                    )
            if duplicates:
                manifest = replace(
                    manifest, params=tuple(manifest.params_map().items())
                )
        else:
            manifest = import_manifest(
                target,
                root=root,
                name=name,
                top=top,
                params=params,
                fmt=fmt,
                warnings_out=warnings_out,
            )
            if out is not None:
                out_path = _manifest_out_path(out)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                manifest.write(out_path)
    design = elaborate(
        manifest,
        root,
        cache=IrCache(cache, root=root) if cache is not None else None,
        refresh=refresh,
    )
    return manifest, design


def run_tui(
    design: Design,
    root: Path,
    settings: Config | None = None,
) -> None:
    """Open the explorer on an elaborated design (a seam for unit tests)."""
    from .tui import TrenApp

    TrenApp(design, root=root, settings=settings).run()


def _print_warnings(warnings: list[str]) -> None:
    for warning in warnings:
        print(f"  warning: {warning}", file=sys.stderr)


def main(argv: list[str] | None = None, *, prog: str = "tren open") -> int:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Import a source target (or read a manifest), elaborate it and "
            "open the TUI."
        ),
    )
    parser.add_argument(
        "target",
        type=Path,
        nargs="?",
        help="manifest JSON, .f filelist, .sv/.v file, source directory, or Vivado project",
    )
    parser.add_argument(
        "-f", "--file", action="append", dest="files", metavar="PATH",
        help="add a source file; repeat the option to list several files",
    )
    parser.add_argument(
        "--root", type=Path, default=Path.cwd(),
        help="anchor for stored relative paths and tools (default: current directory)",
    )
    parser.add_argument(
        "--cache", type=Path, default=None,
        help="IR cache directory (default: the cache.dir setting)",
    )
    parser.add_argument(
        "--refresh", action="store_true", help="rebuild even on a cache hit"
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help=(
            "write the imported manifest here (a directory writes "
            "<dir>/manifest.json; default: keep it in memory)"
        ),
    )
    parser.add_argument(
        "--name", default=None,
        help="design name (default: target name, or the root directory name with -f)",
    )
    parser.add_argument(
        "--top", default=None,
        help="top module (default: 'top', or the Vivado TopModule)",
    )
    parser.add_argument(
        "-G", "--param", action="append", dest="params", metavar="NAME=VALUE",
        help="override a top-level parameter; repeat the option for more",
    )
    parser.add_argument(
        "--format", choices=("auto", "f", "vivado"), default="auto",
        help="importer format (default: auto)",
    )
    args = parser.parse_args(argv)
    if args.files and args.target is not None:
        parser.error("give a target or -f/--file sources, not both")
    if not args.files and args.target is None:
        parser.error("a target or at least one -f/--file is required")
    if args.files and args.format != "auto":
        parser.error("--format does not apply with -f/--file")
    try:
        params = [parse_param(text) for text in (args.params or [])]
    except ValueError as exc:
        parser.error(str(exc))

    try:
        settings = load_config().config
    except ConfigError as exc:
        print(f"tren: {exc}", file=sys.stderr)
        return 2
    cache = args.cache if args.cache is not None else Path(settings.cache_dir)

    imported = args.files is not None or not is_manifest_target(args.target)
    warnings: list[str] = []
    try:
        manifest, design = load_target(
            args.target,
            files=args.files,
            root=args.root,
            cache=cache,
            refresh=args.refresh,
            out=args.out,
            name=args.name,
            top=args.top,
            params=params,
            fmt=args.format,
            warnings_out=warnings,
        )
    except subprocess.CalledProcessError as exc:
        _print_warnings(warnings)
        print(tool_failure_message(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        _print_warnings(warnings)
        print(f"tren: {exc}", file=sys.stderr)
        return 2

    if imported:
        print(
            f"imported {len(manifest.sources)} source(s) for {manifest.name} "
            f"(top={manifest.top})"
        )
        if args.out is not None:
            print(f"  manifest: {_manifest_out_path(args.out)}")
    _print_warnings(warnings)
    for line in design_summary(design):
        print(line)
    run_tui(design, Path(args.root), settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
