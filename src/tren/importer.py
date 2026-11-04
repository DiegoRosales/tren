"""Manifest importer: turn a source layout into a design manifest.

The first importer is the plain SystemVerilog filelist (``.f``) -- the
least-work option. slang consumes these natively as *command files* (``-f``
cwd-relative, ``-F`` file-relative). Verified on sv-lang 12.0 that ``//``
line comments, ``-D``/``-I``, ``+incdir+`` and source paths all parse inside
one, and that an unknown option is a hard error. This importer mirrors that
grammar, reads paths *file-relative* (the ``-F`` reading, so the manifest
survives moving the repo), and writes every path relative to the invocation
root -- the same convention the elaborator and the hand-written demo manifest
use.

Without a filelist, a bare directory is scanned for ``*.sv``/``*.v`` sources,
which is what makes ``tren import rtl`` work for the demo fixture.

Every imported manifest carries ``IMPORT_SLANG_FLAGS``: the slang tolerances
for constructs Vivado and yosys accept (see the constant). The reader edits or
removes them in the manifest, so the tolerance stays visible with the design.

Importers are bootstrap, not a dependency: they produce the same manifest a
human would hand-write. Nested ``-f`` command files are not followed, and
unknown ``-``/``+`` options warn rather than fail -- slang itself would hard-
error on them at elaboration time, so a warning is the honest minimum.

CLI (from the repo root, inside the nix dev shell):

    nix develop . -c tren import tests/rtl              # -> tests/rtl/manifest.json
    nix develop . -c tren import tests/rtl/sources.f --name demo
    nix develop . -c tren import tests/rtl/demo.sv
    nix develop . -c tren import -f a.sv -f b.sv        # explicit source list
    nix develop . -c tren import -f top.sv -G WIDTH=16  # top-level override
    nix develop . -c tren import <vivado-project.xpr>   # Vivado (auto-detected)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from .manifest import Manifest
from .vivado import parse_vivado, to_manifest_layout

_SOURCE_SUFFIXES = (".sv", ".v")
_FORMATS = ("auto", "f", "vivado")
_PARAM_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*\Z")

# Slang tolerances every imported manifest carries. Vivado and yosys accept
# the constructs behind each flag, so a design that builds in its own
# toolchain must not stop at the first `tren open`:
#   --allow-use-before-declare  an implicit net, used before the named port
#                               connection that creates it
#   --timescale                 a design that mixes files with a `timescale
#                               and files without one (Vivado has a default)
#   -Wno-redefinition           an ANSI port declared again as a body wire
# The manifest is editable, so a reader that wants slang strict removes them.
IMPORT_SLANG_FLAGS: tuple[str, ...] = (
    "--allow-use-before-declare",
    "--timescale=1ns/1ps",
    "-Wno-redefinition",
)


def detect_format(target: str | Path) -> str:
    """Pick an importer for a target: Vivado .xpr, else the plain filelist."""
    target = Path(target)
    if target.is_dir() and (target / "project.xpr").is_file():
        return "vivado"
    if target.is_file() and target.suffix == ".xpr":
        return "vivado"
    return "f"


def _strip_comment(line: str) -> str:
    return line.split("//", 1)[0]


def _tokenize(text: str) -> list[str]:
    """Shell-like split honouring double-quoted tokens."""
    tokens: list[str] = []
    cur: list[str] = []
    in_quote = False
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\" and in_quote and i + 1 < n:
            cur.append(text[i + 1])
            i += 2
        elif c == '"':
            in_quote = not in_quote
            i += 1
        elif c.isspace() and not in_quote:
            if cur:
                tokens.append("".join(cur))
                cur = []
            i += 1
        else:
            cur.append(c)
            i += 1
    if cur:
        tokens.append("".join(cur))
    return tokens


def _def_pair(text: str) -> tuple[str, str]:
    name, _, value = text.partition("=")
    return name.strip(), value or "1"


def parse_param(text: str) -> tuple[str, str]:
    """Parse ``NAME=VALUE`` for a top-level parameter override.

    The value must be one token and must not contain ``;``: yosys receives it
    as a command-line script argument, where whitespace changes the command.
    """
    name, sep, value = text.partition("=")
    name = name.strip()
    value = value.strip()
    if not sep or not name or not value:
        raise ValueError(
            f"malformed parameter override: {text!r} (expected NAME=VALUE)"
        )
    if _PARAM_NAME.fullmatch(name) is None:
        raise ValueError(f"invalid parameter name: {name!r}")
    if any(ch.isspace() for ch in value) or ";" in value:
        raise ValueError(
            f"invalid parameter value: {value!r} (no whitespace or ';')"
        )
    return name, value


def merge_pairs(
    base: list[tuple[str, str]] | tuple[tuple[str, str], ...],
    override: list[tuple[str, str]] | tuple[tuple[str, str], ...] | None,
    *,
    warnings_out: list[str] | None = None,
) -> tuple[tuple[str, str], ...]:
    """Merge ``override`` over ``base``; the last value wins on a name match.

    A name that occurs more than once produces one warning. The warning names
    the retained value, so the reader can see which definition won.
    """
    merged: dict[str, str] = {}
    seen: set[str] = set()
    duplicates: list[str] = []
    for name, value in list(base) + list(override or ()):
        if name in seen and name not in duplicates:
            duplicates.append(name)
        seen.add(name)
        merged[name] = value
    if warnings_out is not None:
        for name in duplicates:
            warnings_out.append(
                f"parameter {name} is defined more than once; "
                f"using the last value {merged[name]}"
            )
    return tuple(merged.items())


def parse_filelist(
    text: str,
) -> tuple[
    list[str],
    list[str],
    list[tuple[str, str]],
    list[tuple[str, str]],
    list[str],
]:
    """Parse command-file/filelist text.

    The result is ``(sources, incdirs, defines, params, warnings)``. ``//``
    line comments are stripped; values may be double-quoted. Paths are
    returned as written (relative to the filelist) -- the caller anchors them.
    """
    sources: list[str] = []
    incdirs: list[str] = []
    defines: list[tuple[str, str]] = []
    params: list[tuple[str, str]] = []
    warnings: list[str] = []

    tokens: list[str] = []
    for line in text.splitlines():
        tokens.extend(_tokenize(_strip_comment(line)))

    n = len(tokens)
    i = 0
    while i < n:
        tok = tokens[i]

        def value(what: str) -> str | None:
            if i + 1 >= n:
                warnings.append(f"dangling {what} at end of filelist")
                return None
            return tokens[i + 1]

        if tok.startswith("+incdir+") and len(tok) > len("+incdir+"):
            incdirs.append(tok[len("+incdir+") :].strip('"'))
        elif tok in ("+incdir", "-I", "--include-directory"):
            v = value(tok)
            if v is not None:
                incdirs.append(v.strip('"'))
            i += 1
        elif tok.startswith("-I") and len(tok) > 2:
            incdirs.append(tok[2:].strip('"'))
        elif tok.startswith("+define+") and len(tok) > len("+define+"):
            defines.append(_def_pair(tok[len("+define+") :]))
        elif tok in ("-D", "--define-macro"):
            v = value(tok)
            if v is not None:
                defines.append(_def_pair(v))
            i += 1
        elif tok.startswith("-D") and len(tok) > 2 and not tok.startswith("--"):
            defines.append(_def_pair(tok[2:]))
        elif tok == "-G":
            if i + 1 >= n:
                raise ValueError(
                    "dangling parameter override -G at end of filelist "
                    "(expected -G NAME=VALUE)"
                )
            params.append(parse_param(tokens[i + 1]))
            i += 1
        elif tok.startswith("-G") and len(tok) > 2:
            params.append(parse_param(tok[2:]))
        elif tok.startswith("-"):
            warnings.append(f"ignoring option not carried into the manifest: {tok}")
        else:
            sources.append(tok.strip('"'))
        i += 1
    return sources, incdirs, defines, params, warnings


def _store(path: str, anchor: Path | None, root: Path) -> str:
    """Express ``path`` relative to ``root`` (the manifest's anchor).

    ``anchor`` is the directory filelist-entries are relative to (the ``-F``
    reading). ``anchor=None`` means the path is already root-relative or
    absolute (single-file and directory-scan callers). Absolute inputs and
    paths outside ``root`` resolve to ``../``-prefixed relative forms, which
    the elaborator (cwd=root) still resolves correctly.
    """
    p = Path(path)
    if anchor is not None and not p.is_absolute():
        p = anchor / p
    return os.path.relpath(p.resolve(), root)


def _default_incdirs(sources: list[str], anchor: Path | None, root: Path) -> list[str]:
    """Directories holding the sources, root-relative, deduped, sorted.

    slang and Vivado resolve a `` `include `` relative to the
    including file's directory; yosys `read_verilog` searches the cwd and that
    directory before its ``-I`` dirs (verified against yosys 0.70 - a
    cross-directory include needs ``-I``). slang does not search the cwd, so a
    source at the root records ``.``: a root header included from a
    subdirectory source needs ``-I.``. Explicit filelist incdirs are merged
    in, not replaced (the caller unions them after this).
    """
    dirs: set[str] = set()
    for s in sources:
        stored = _store(s, anchor, root)
        dirs.add(os.path.dirname(stored) or ".")
    return sorted(dirs)


def import_sources(
    sources: list[str] | tuple[str, ...],
    *,
    root: str | Path | None = None,
    name: str | None = None,
    top: str | None = None,
    params: list[tuple[str, str]] | tuple[tuple[str, str], ...] | None = None,
    warnings_out: list[str] | None = None,
) -> Manifest:
    """Emit a Manifest for an explicit list of source files.

    ``root`` (default: the invocation cwd) is the anchor every stored path is
    relative to. The order of ``sources`` is kept because a filelist order is
    meaningful to the parser. A missing source produces a warning and is left
    out of the manifest, so one bad path does not stop the others. Source
    directories become incdirs, the same rule ``import_manifest`` uses for a
    directory scan. ``params`` are top-level parameter overrides.
    """
    if not sources:
        raise ValueError("at least one source file is required")
    warnings = [] if warnings_out is None else warnings_out
    root = Path.cwd() if root is None else Path(root)
    found: list[str] = []
    for source in sources:
        if Path(source).is_file():
            found.append(str(source))
        else:
            warnings.append(f"skipping missing source file: {source}")
    if not found:
        raise ValueError("no source files remain; all listed files are missing")
    stored = tuple(_store(source, None, root) for source in found)
    return Manifest(
        name=name or root.name or "design",
        sources=stored,
        incdirs=tuple(_default_incdirs(found, None, root)),
        params=merge_pairs((), params, warnings_out=warnings),
        top=top or "top",
        slang_flags=IMPORT_SLANG_FLAGS,
    )


def import_manifest(
    target: str | Path,
    *,
    root: str | Path | None = None,
    name: str | None = None,
    top: str | None = None,
    params: list[tuple[str, str]] | tuple[tuple[str, str], ...] | None = None,
    fmt: str = "auto",
    warnings_out: list[str] | None = None,
) -> Manifest:
    """Emit a Manifest for a filelist, a single source, or a directory.

    ``root`` (default: the invocation cwd) is the anchor every stored path is
    relative to -- the same anchor the elaborator resolves against. ``fmt``
    selects the importer: ``auto`` detects a Vivado ``project.xpr``,
    otherwise the plain filelist/dir importer is used. ``params`` are
    top-level parameter overrides; a CLI value wins over a filelist value.
    """
    target = Path(target)
    root = Path.cwd() if root is None else Path(root)
    warnings = [] if warnings_out is None else warnings_out

    fmt = fmt if fmt and fmt != "auto" else detect_format(target)
    if fmt == "vivado":
        return _import_vivado(
            target, root, name=name, top=top, params=params, warnings_out=warnings
        )
    if fmt != "f":
        raise ValueError(f"unknown importer format: {fmt!r} (choose from {_FORMATS})")

    if target.is_file():
        if target.suffix == ".f":
            sources, incdirs, defines, file_params, warn = parse_filelist(
                target.read_text()
            )
            warnings.extend(warn)
            anchor: Path | None = target.parent
        else:
            sources, incdirs, defines = [str(target)], [], []
            file_params = []
            anchor = None
    elif target.is_dir():
        filelists = sorted(target.glob("*.f"))
        if filelists:
            if len(filelists) > 1:
                raise ValueError(
                    "multiple filelists found: "
                    + ", ".join(str(p) for p in filelists)
                    + " -- pass one explicitly"
                )
            sources, incdirs, defines, file_params, warn = parse_filelist(
                filelists[0].read_text()
            )
            warnings.extend(warn)
            anchor = filelists[0].parent
        else:
            sources = sorted(
                str(p) for p in target.rglob("*") if p.suffix in _SOURCE_SUFFIXES
            )
            incdirs, defines = [], []
            file_params = []
            anchor = None
    else:
        raise FileNotFoundError(f"no such file or directory: {target}")

    if name is None:
        name = target.stem if target.is_file() else target.name

    stored_sources = tuple(_store(s, anchor, root) for s in sources)
    stored_incdirs = tuple(_store(i, anchor, root) for i in incdirs)
    # yosys needs a `` `include ``'s directory spelled out with -I;
    # slang/Vivado imply it. Source dirs are the floor - explicit filelist
    # incdirs stay merged in, never replaced.
    incdirs_all = tuple(
        sorted(set(stored_incdirs) | set(_default_incdirs(sources, anchor, root)))
    )

    return Manifest(
        name=name,
        sources=stored_sources,
        incdirs=incdirs_all,
        defines=tuple(defines),
        params=merge_pairs(file_params, params, warnings_out=warnings),
        top=top or "top",
        slang_flags=IMPORT_SLANG_FLAGS,
    )


def _import_vivado(
    target: Path,
    root: Path,
    *,
    name: str | None = None,
    top: str | None = None,
    params: list[tuple[str, str]] | tuple[tuple[str, str], ...] | None = None,
    warnings_out: list[str] | None = None,
) -> Manifest:
    warnings = [] if warnings_out is None else warnings_out
    proj = parse_vivado(target, name=name, top=top)
    warnings.extend(proj.warnings)
    sources, incdirs, parsed_top = to_manifest_layout(proj, root)
    return Manifest(
        name=proj.name,
        sources=sources,
        incdirs=incdirs,
        params=merge_pairs((), params, warnings_out=warnings),
        top=parsed_top or "top",
        slang_flags=IMPORT_SLANG_FLAGS,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, prog="tren import"
    )
    parser.add_argument(
        "target",
        type=Path,
        nargs="?",
        help=".f filelist, .sv file, source directory, or Vivado project",
    )
    parser.add_argument(
        "-f", "--file", action="append", dest="files", metavar="PATH",
        help="add a source file; repeat the option to list several files",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="manifest path (default: <target dir>/manifest.json, "
        "or <cwd>/manifest.json with -f)",
    )
    parser.add_argument(
        "--name", default=None,
        help="design name (default: target name, or the cwd name with -f)",
    )
    parser.add_argument(
        "--top", default=None,
        help="top module (default: 'top', or the Vivado TopModule / recorded top)",
    )
    parser.add_argument(
        "-G", "--param", action="append", dest="params", metavar="NAME=VALUE",
        help="override a top-level parameter; repeat the option for more",
    )
    parser.add_argument(
        "--format", choices=_FORMATS, default="auto",
        help="importer: auto detects a Vivado project.xpr, else plain filelist "
        "(default: auto)",
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

    warnings: list[str] = []
    try:
        if args.files:
            manifest = import_sources(
                args.files,
                name=args.name,
                top=args.top,
                params=params,
                warnings_out=warnings,
            )
            base = Path.cwd()
            effective = "files"
        else:
            effective = (
                args.format if args.format != "auto" else detect_format(args.target)
            )
            manifest = import_manifest(
                args.target,
                name=args.name,
                top=args.top,
                params=params,
                fmt=effective,
                warnings_out=warnings,
            )
            base = args.target.parent if args.target.is_file() else args.target
        out = args.out if args.out is not None else base / "manifest.json"
        out = out.resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        manifest.write(out)
    except (OSError, ValueError) as exc:
        for warning in warnings:
            print(f"  warning: {warning}", file=sys.stderr)
        print(f"tren import: {exc}", file=sys.stderr)
        return 2

    print(
        f"wrote manifest for {len(manifest.sources)} source(s) to {out}\n"
        f"  name={manifest.name} top={manifest.top} format={effective}"
    )
    for inc in manifest.incdirs:
        print(f"  incdir {inc}")
    for dname, dvalue in manifest.defines:
        print(f"  define {dname}={dvalue}")
    for pname, pvalue in manifest.params:
        print(f"  param {pname}={pvalue}")
    for flag in manifest.slang_flags:
        print(f"  slang {flag}")
    for w in warnings:
        print(f"  warning: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())