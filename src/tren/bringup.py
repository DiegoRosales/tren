"""One-command bringup: target -> manifest -> IR -> TUI.

``tren <target>`` is the short path from a source tree to the explorer. The
target is either a manifest JSON or anything an importer accepts (``.f``
filelist, ``.sv``/``.v`` file, source directory, Vivado ``.xpr``). A source
target is imported in memory; the manifest is written only when ``--out`` is
given. The elaborator then runs in-process and the TUI opens the result.

The two subcommands stay available for scripted, non-interactive use:
``tren import`` writes a manifest and ``tren elaborate`` builds only the IR.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .elaborate import IrCache, design_summary, elaborate, tool_failure_message
from .importer import import_manifest
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
    target: str | Path,
    *,
    root: str | Path | None = None,
    cache: str | Path | None = None,
    refresh: bool = False,
    out: str | Path | None = None,
    name: str | None = None,
    top: str | None = None,
    fmt: str = "auto",
    warnings_out: list[str] | None = None,
) -> tuple[Manifest, Design]:
    """Import (or read) the target, then elaborate it, all in one process.

    ``root`` is the anchor for stored paths and tool invocation (default:
    current directory). ``out`` writes the imported manifest; without it the
    manifest stays in memory. Importer warnings go to ``warnings_out`` when
    given, so the caller can show them before the TUI takes the screen.
    """
    root = Path.cwd() if root is None else Path(root)
    target = Path(target)
    if is_manifest_target(target):
        if out is not None or name is not None or top is not None or fmt != "auto":
            raise ValueError(
                "--out/--name/--top/--format only apply when importing a "
                "source target"
            )
        manifest = Manifest.read(target)
    else:
        manifest = import_manifest(
            target,
            root=root,
            name=name,
            top=top,
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


def run_tui(design: Design, root: Path) -> None:
    """Open the explorer on an elaborated design (a seam for unit tests)."""
    from .tui import TrenApp

    TrenApp(design, root=root).run()


def _print_warnings(warnings: list[str]) -> None:
    for warning in warnings:
        print(f"  warning: {warning}", file=sys.stderr)


def main(argv: list[str] | None = None, *, prog: str = "tren") -> int:
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
        help="manifest JSON, .f filelist, .sv/.v file, source directory, or Vivado project",
    )
    parser.add_argument(
        "--root", type=Path, default=Path.cwd(),
        help="anchor for relative paths and tools (default: current directory)",
    )
    parser.add_argument(
        "--cache", type=Path, default=Path(".cache/tren"),
        help="IR cache directory (reused across runs)",
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
        "--name", default=None, help="design name (default: target name)"
    )
    parser.add_argument(
        "--top", default=None,
        help="top module (default: 'top', or the Vivado TopModule)",
    )
    parser.add_argument(
        "--format", choices=("auto", "f", "vivado"), default="auto",
        help="importer format (default: auto)",
    )
    args = parser.parse_args(argv)

    imported = not is_manifest_target(args.target)
    warnings: list[str] = []
    try:
        manifest, design = load_target(
            args.target,
            root=args.root,
            cache=args.cache,
            refresh=args.refresh,
            out=args.out,
            name=args.name,
            top=args.top,
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
    run_tui(design, Path(args.root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
