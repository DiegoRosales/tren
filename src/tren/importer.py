"""Manifest importer: turn a source layout into a design manifest (card 211).

The first importer is the plain SystemVerilog filelist (``.f``) — the
least-work option. slang consumes these natively as *command files* (``-f``
cwd-relative, ``-F`` file-relative). Verified on sv-lang 11.0 that ``//``
line comments, ``-D``/``-I``, ``+incdir+`` and source paths all parse inside
one, and that an unknown option is a hard error. This importer mirrors that
grammar, reads paths *file-relative* (the ``-F`` reading, so the manifest
survives moving the repo), and writes every path relative to the invocation
root — the same convention the elaborator and the hand-written demo manifest
use.

Without a filelist, a bare directory is scanned for ``*.sv``/``*.v`` sources,
which is what makes ``tren import rtl`` work for the demo fixture.

Importers are bootstrap, not a dependency: they produce the same manifest a
human would hand-write. Nested ``-f`` command files are not followed, and
unknown ``-``/``+`` options warn rather than fail — slang itself would hard-
error on them at elaboration time, so a warning is the honest minimum.

CLI (from the repo root, inside the nix dev shell):

    nix develop . -c tren import rtl                    # -> rtl/manifest.json
    nix develop . -c tren import rtl/sources.f --name demo
    nix develop . -c tren import rtl/demo.sv
    nix develop . -c tren import <vivado-project.xpr>   # Vivado (auto-detected)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .manifest import Manifest
from .vivado import parse_vivado, to_manifest_layout

_SOURCE_SUFFIXES = (".sv", ".v")
_FORMATS = ("auto", "f", "vivado")


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


def parse_filelist(
    text: str,
) -> tuple[list[str], list[str], list[tuple[str, str]], list[str]]:
    """Parse command-file/filelist text -> (sources, incdirs, defines, warnings).

    ``//`` line comments are stripped; values may be double-quoted. Paths are
    returned as written (relative to the filelist) — the caller anchors them.
    """
    sources: list[str] = []
    incdirs: list[str] = []
    defines: list[tuple[str, str]] = []
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
        elif tok.startswith("-"):
            warnings.append(f"ignoring option not carried into the manifest: {tok}")
        else:
            sources.append(tok.strip('"'))
        i += 1
    return sources, incdirs, defines, warnings


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


def import_manifest(
    target: str | Path,
    *,
    root: str | Path | None = None,
    name: str | None = None,
    top: str | None = None,
    fmt: str = "auto",
    warnings_out: list[str] | None = None,
) -> Manifest:
    """Emit a Manifest for a filelist, a single source, or a directory.

    ``root`` (default: the invocation cwd) is the anchor every stored path is
    relative to — the same anchor the elaborator resolves against. ``fmt``
    selects the importer: ``auto`` detects a Vivado ``project.xpr``,
    otherwise the plain filelist/dir importer is used.
    """
    target = Path(target)
    root = Path.cwd() if root is None else Path(root)
    warnings = [] if warnings_out is None else warnings_out

    fmt = fmt if fmt and fmt != "auto" else detect_format(target)
    if fmt == "vivado":
        return _import_vivado(target, root, name=name, top=top, warnings_out=warnings)
    if fmt != "f":
        raise ValueError(f"unknown importer format: {fmt!r} (choose from {_FORMATS})")

    if target.is_file():
        if target.suffix == ".f":
            sources, incdirs, defines, warn = parse_filelist(target.read_text())
            warnings.extend(warn)
            anchor: Path | None = target.parent
        else:
            sources, incdirs, defines = [str(target)], [], []
            anchor = None
    elif target.is_dir():
        filelists = sorted(target.glob("*.f"))
        if filelists:
            if len(filelists) > 1:
                raise ValueError(
                    "multiple filelists found: "
                    + ", ".join(str(p) for p in filelists)
                    + " — pass one explicitly"
                )
            sources, incdirs, defines, warn = parse_filelist(filelists[0].read_text())
            warnings.extend(warn)
            anchor = filelists[0].parent
        else:
            sources = sorted(
                str(p) for p in target.rglob("*") if p.suffix in _SOURCE_SUFFIXES
            )
            incdirs, defines = [], []
            anchor = None
    else:
        raise FileNotFoundError(f"no such file or directory: {target}")

    if name is None:
        name = target.stem if target.is_file() else target.name

    return Manifest(
        name=name,
        sources=tuple(_store(s, anchor, root) for s in sources),
        incdirs=tuple(_store(i, anchor, root) for i in incdirs),
        defines=tuple(defines),
        top=top or "top",
    )


def _import_vivado(
    target: Path,
    root: Path,
    *,
    name: str | None = None,
    top: str | None = None,
    warnings_out: list[str] | None = None,
) -> Manifest:
    warnings = [] if warnings_out is None else warnings_out
    proj = parse_vivado(
        target, name=name, top=top, warnings_out=warnings
    )
    sources, parsed_top = to_manifest_layout(proj, root)
    return Manifest(
        name=proj.name,
        sources=sources,
        top=parsed_top or "top",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, prog="tren import"
    )
    parser.add_argument(
        "target",
        type=Path,
        help=".f filelist, .sv file, source directory, or Vivado project",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="manifest path (default: <target dir>/manifest.json)",
    )
    parser.add_argument(
        "--name", default=None, help="design name (default: target name)"
    )
    parser.add_argument(
        "--top", default=None,
        help="top module (default: 'top', or the Vivado TopModule / recorded top)",
    )
    parser.add_argument(
        "--format", choices=_FORMATS, default="auto",
        help="importer: auto detects a Vivado project.xpr, else plain filelist "
        "(default: auto)",
    )
    args = parser.parse_args(argv)

    effective = args.format if args.format != "auto" else detect_format(args.target)
    warnings: list[str] = []
    manifest = import_manifest(
        args.target,
        name=args.name,
        top=args.top,
        fmt=effective,
        warnings_out=warnings,
    )
    base = args.target.parent if args.target.is_file() else args.target
    out = args.out if args.out is not None else base / "manifest.json"
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    manifest.write(out)

    print(
        f"wrote manifest for {len(manifest.sources)} source(s) to {out}\n"
        f"  name={manifest.name} top={manifest.top} format={effective}"
    )
    for inc in manifest.incdirs:
        print(f"  incdir {inc}")
    for dname, dvalue in manifest.defines:
        print(f"  define {dname}={dvalue}")
    for w in warnings:
        print(f"  warning: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())