"""Inferred black boxes: one body-less declaration per undeclared module.

``read_slang`` (sv-elab) refuses to elaborate an instantiated module that has
no declaration, and the option that used to tolerate one is retired upstream.
A design that needs the slang frontend and also instantiates vendor RTL
therefore does not elaborate at all, although every instance it cannot read is
a black box TREN would happily show.

This module answers "which modules are black boxes" from the design itself.
``slang --ast-json`` reports every instantiation of an undeclared module as an
``UninstantiatedDef`` node, which carries the module name, the port
connections and their self-determined types, and the parameter values. From
those nodes TREN renders a stub file. The stub exists only to make elaboration
possible; it is a hint, not a model:

- a port is an ``input``, because that is the only direction that accepts an
  expression such as ``.data({a, b})``;
- port order follows the instantiation, so the module's real order is lost;
- a positional connection has no name, so it gets ``p0``, ``p1`` and so on;
- only the widths of the connected expressions are known, and an unconnected
  port is declared one bit wide;
- a parameter takes an ``int`` type, which accepts integer and string
  overrides alike.

TREN drops the stub modules from the netlist again before it builds the IR, so
a box that needed a stub looks exactly like any other unresolved module: its
ports come from the cell, and its directions are unknown.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

# Parameter names are declared with this type: it accepts an integer override
# and a string override alike, and the stub never reads the value.
_PARAM_TYPE = "int"

_INTEGER_WIDTHS = {
    "byte": 8,
    "shortint": 16,
    "int": 32,
    "integer": 32,
    "longint": 64,
    "time": 64,
}


@dataclass(frozen=True)
class Stub:
    """One inferred black-box declaration."""

    module: str
    ports: tuple[tuple[str, int], ...] = ()
    params: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass
class _Draft:
    ports: dict[str, int] = field(default_factory=dict)
    params: list[str] = field(default_factory=list)
    positional: int = 0
    notes: list[str] = field(default_factory=list)


def _walk(node: Any, out: list[dict], seen: set[int] | None = None) -> None:
    """Collect every ``UninstantiatedDef`` once, in document order."""
    if seen is None:
        seen = set()
    if isinstance(node, dict):
        if node.get("kind") == "UninstantiatedDef" and id(node) not in seen:
            seen.add(id(node))
            out.append(node)
        for value in node.values():
            _walk(value, out, seen)
    elif isinstance(node, list):
        for value in node:
            _walk(value, out, seen)


def _type_width(node: dict | None) -> int | None:
    """Width of a slang type node, or None when it does not name one."""
    if not isinstance(node, dict):
        return None
    kind = node.get("kind")
    if kind == "ScalarType":
        return 1
    if kind == "PackedArrayType":
        m = re.fullmatch(r"\[\s*(-?\d+)\s*:\s*(-?\d+)\s*\]", node.get("range") or "")
        if m is None:
            return None
        return abs(int(m.group(1)) - int(m.group(2))) + 1
    if kind == "PredefinedIntegerType":
        return _INTEGER_WIDTHS.get(node.get("name") or "")
    return None


def _expr_width(expr: dict | None) -> int | None:
    """Width of a connection expression, including the error-typed ones.

    slang binds a connection that needs a port type as an ``Invalid`` node
    whose ``child`` is the real expression. ``'{default: 4'h0}`` keeps its
    width on the default setter, so most of what looks untyped is readable
    one level down.
    """
    if not isinstance(expr, dict):
        return None
    width = _type_width(expr.get("type"))
    if width is not None:
        return width
    child = expr.get("child") if expr.get("kind") == "Invalid" else None
    if not isinstance(child, dict):
        return None
    return _pattern_width(child)


def _pattern_width(node: dict) -> int | None:
    """Width of the expression slang put under an ``Invalid`` node.

    An assignment pattern keeps its width on the default setter or on its
    elements. A plain expression that only needed the port type to size it
    keeps its own self-determined type, so read that first: without it an
    ``Invalid``-wrapped ``NamedValue`` would be declared one bit wide and
    the connection would be truncated.
    """
    width = _type_width(node.get("type"))
    if width is not None:
        return width
    widths: list[int] = []
    setter = node.get("defaultSetter")
    if isinstance(setter, dict):
        widths.append(_type_width(setter.get("type")) or 0)
    for key in ("setters", "elements"):
        for item in node.get(key) or []:
            if not isinstance(item, dict):
                continue
            value = item.get("expr") if "expr" in item else item
            widths.append(_expr_width(value) or 0)
    operand = node.get("operand")
    if isinstance(operand, dict):
        widths.append(_expr_width(operand) or 0)
    known = [w for w in widths if w]
    return max(known) if known else None


@lru_cache(maxsize=32)
def _source_lines(path: Path) -> tuple[str, ...] | None:
    try:
        return tuple(path.read_text().splitlines())
    except OSError:
        return None


def _resolve_source(source_file: str | None, root: Path) -> Path | None:
    if not source_file:
        return None
    path = Path(source_file)
    if not path.is_absolute():
        path = root / path
    try:
        return path.resolve()
    except OSError:
        return None


_PARAM_NAME_RE = re.compile(r"\.\s*([A-Za-z_][\w$]*)")


def _param_names(root: Path, node: dict) -> tuple[str, ...] | None:
    """Parameter names of one instantiation, read back from the source text.

    ``slang --ast-json`` carries the parameter *values* in order but not their
    names, and a named override only binds to a parameter of that name, so the
    names have to come from the instantiation site. Walk back from the
    instance name to ``#(`` and read the ``.NAME`` tokens.

    Return None when the list cannot be read, so the caller can say why.
    """
    if not node.get("parameters"):
        return ()
    path = _resolve_source(node.get("source_file"), root)
    lines = _source_lines(path) if path is not None else None
    line = node.get("source_line") or 0
    column = node.get("source_column") or 0
    if not lines or not (1 <= line <= len(lines)):
        return None
    offset = sum(len(text) + 1 for text in lines[: line - 1]) + (column - 1)
    head = "\n".join(lines)[:offset].rstrip()
    if not head.endswith(")"):
        return None
    depth = 0
    start = -1
    for i in range(len(head) - 1, -1, -1):
        if head[i] == ")":
            depth += 1
        elif head[i] == "(":
            depth -= 1
            if depth == 0:
                start = i
                break
    # SystemVerilog allows whitespace between the '#' and the '('.
    if start <= 0 or not head[:start].rstrip().endswith("#"):
        return None
    inner = head[start + 1 : -1]
    names = _PARAM_NAME_RE.findall(inner)
    if names:
        return tuple(dict.fromkeys(names))
    # A positional parameter list has no names to recover. Name them by
    # position, which is how a positional override binds anyway.
    count = len(node.get("parameters") or [])
    return tuple(f"P{i}" for i in range(count)) if count else None


def infer(ast: dict, root: str | Path) -> dict[str, Stub]:
    """Black-box stubs for every module the design instantiates but never declares.

    ``ast`` is ``slang --ast-json`` output for the manifest's source set.
    """
    root = Path(root)
    nodes: list[dict] = []
    _walk(ast, nodes)

    drafts: dict[str, _Draft] = {}
    for node in nodes:
        module = node.get("definitionName") or ""
        if not module or module.startswith("$"):
            continue
        draft = drafts.setdefault(module, _Draft())
        names = _param_names(root, node)
        if names is None:
            draft.notes.append("parameter names could not be read from the source")
        else:
            for name in names:
                if name not in draft.params:
                    draft.params.append(name)
        for port in node.get("ports") or []:
            name = port.get("name")
            expr = port.get("expr") or {}
            if name is None:
                name = f"p{draft.positional}"
                draft.positional += 1
                draft.notes.append(f"positional connection shown as {name}")
            if expr.get("kind") == "EmptyArgument":
                draft.ports.setdefault(name, 1)
                continue
            width = _expr_width(expr)
            if width is None:
                draft.notes.append(
                    f"width of {name} is not in the AST; declared one bit"
                )
                width = 1
            draft.ports[name] = max(draft.ports.get(name) or 0, width)

    return {
        module: Stub(
            module=module,
            ports=tuple(draft.ports.items()),
            params=tuple(draft.params),
            notes=tuple(dict.fromkeys(draft.notes)),
        )
        for module, draft in sorted(drafts.items())
    }


def render(stubs: dict[str, Stub] | Iterable[Stub], source: str = "") -> str:
    """The stub file: one ``(* blackbox *)`` module per undeclared module."""
    items = sorted(
        stubs.values() if isinstance(stubs, dict) else stubs,
        key=lambda stub: stub.module,
    )
    header = (
        "// Generated by TREN from the instantiations of modules with no\n"
        "// declaration. This file is not part of the design: it only lets\n"
        "// the elaboration run. Port order, names and widths are inferred.\n"
    )
    if source:
        header += f"// design: {source}\n"
    blocks = [header]
    for stub in items:
        params = ""
        if stub.params:
            params = " #(" + ", ".join(
                f"parameter {_PARAM_TYPE} {name} = 0" for name in stub.params
            ) + ")"
        ports = ", ".join(
            f"input logic [{width - 1}:0] {name}"
            for name, width in stub.ports
            if width >= 1
        )
        blocks.append(
            f"\n(* blackbox *)\nmodule {stub.module}{params} ({ports});\nendmodule\n"
        )
    return "".join(blocks)


def summary(stubs: dict[str, Stub], stub_file: Path | None = None) -> str | None:
    """The warning that names every inferred black box, or None.

    A box TREN invents must never be invisible: the user asked to explore a
    design, not to have a missing module glossed over.
    """
    if not stubs:
        return None
    names = ", ".join(sorted(stubs))
    lines = [
        f"no declaration for {len(stubs)} module(s); elaborated as inferred "
        f"black boxes: {names}"
    ]
    if stub_file is not None:
        lines.append(f"  stub: {stub_file}")
    for stub in stubs.values():
        for note in stub.notes:
            lines.append(f"  {stub.module}: {note}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """``tren stub``: write the inferred black-box file and show what it holds.

    A debugging aid. Elaboration writes the same file by itself, so this is
    for reading what TREN assumes about a module it cannot see.
    """
    import argparse
    import subprocess
    import sys
    import tempfile

    from .elaborate import run_slang_ast
    from .manifest import Manifest

    parser = argparse.ArgumentParser(
        prog="tren stub",
        description=(
            "Write the black-box declarations TREN infers for modules the "
            "source set does not declare. The file goes to --out, or to "
            "standard output."
        ),
    )
    parser.add_argument("manifest", type=Path, help="manifest JSON")
    parser.add_argument(
        "--root", type=Path, default=Path.cwd(),
        help="repo root for the manifest's relative paths",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="write the stub file here instead of standard output",
    )
    args = parser.parse_args(argv)

    try:
        manifest = Manifest.read(args.manifest)
    except (OSError, ValueError) as exc:
        print(f"tren: {exc}", file=sys.stderr)
        return 2

    try:
        with tempfile.TemporaryDirectory(prefix="tren-stub-") as td:
            ast_path = str(Path(td) / "ast.json")
            run_slang_ast(manifest, args.root, ast_path)
            ast = json.loads(Path(ast_path).read_text())
    except subprocess.CalledProcessError as exc:
        from .elaborate import tool_failure_message

        print(tool_failure_message(exc), file=sys.stderr)
        return 1

    stubs = infer(ast, args.root)
    if not stubs:
        print(
            "tren stub: every instantiated module has a declaration",
            file=sys.stderr,
        )
        return 0
    text = render(stubs, source=manifest.name)
    if args.out is None:
        sys.stdout.write(text)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
        print(f"tren stub: wrote {args.out}", file=sys.stderr)
    warning = summary(stubs)
    if warning:
        for line in warning.splitlines():
            print(f"  {line}", file=sys.stderr)
    return 0
