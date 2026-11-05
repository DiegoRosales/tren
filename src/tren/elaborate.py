"""Elaboration runner: a manifest -> the TREN IR.

Wraps the pinned CLIs (versions recorded in README.md):

- ``slang-hier --params`` - the leaf instance tree (generate paths included)
  plus per-instance resolved parameters (``W=32'd32``). It does not list the
  generate block nodes themselves. A manifest top-level override reaches
  slang as ``-G NAME=VALUE``.
- ``yosys ... ; hierarchy -top <top>; proc; write_json`` - connectivity
  (per net bit, drivers/loads within the module), boundary links (submodule
  cell ports), black-box detection and ports, and net widths/src. The yosys
  rules from AGENTS.md apply: ``proc`` required, never ``opt``, never
  ``flatten``; bit IDs are positional-only across scopes. The read pass is
  ``read_verilog -sv``, with ``read_slang --keep-hierarchy`` as the fallback
  for a design it cannot parse (a package import, an assignment pattern, a
  streaming operator).
- ``slang --ast-json --ast-json-source-info --ast-json-detailed-types`` -
  declarations (parameter kinds, source locations for ports) and the
  ``GenerateBlock`` / ``GenerateBlockArray`` nodes that become generate
  scopes.

Scopes are keyed by their hierarchical instance path (``top.u_core.g[0]``)
and carry their resolved params; generate blocks become their own scopes
(``module is None``). The hand-written fixture
(tests/fixtures/demo-tree.json) is the module-keyed stand-in for UI tests;
this module produces the real, path-keyed IR.

Usage (from the repo root, inside the nix dev shell):

    nix develop . -c bash -c "PYTHONPATH=src python -m tren.elaborate tests/rtl/demo.manifest.json"
    nix develop . -c bash -c "PYTHONPATH=src python -m tren.elaborate --refresh tests/rtl/demo.manifest.json"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from .bits import cell_directions, unmangle
from .cache import IrCache
from .config import ConfigError, load_config
from .ir import (
    BitLink,
    BoundaryLink,
    Cell,
    CellPin,
    Design,
    Endpoint,
    Instance,
    Net,
    NetConnectivity,
    Param,
    Port,
    Scope,
)
from .manifest import FRONTENDS, Manifest
from .stubs import Stub, infer as infer_stubs, render as render_stubs, summary as stub_summary

# --------------------------------------------------------------------------
# CLI wrappers
# --------------------------------------------------------------------------

_HIER_RE = re.compile(
    r'Module="(?P<module>[^"]+)" Instance="(?P<inst>[^"]+)" File="(?P<file>[^"]+)"'
    r"(?: Parameters: (?P<params>.*))?$"
)


# Backend tolerance defaults. These only ever help a *viewer*:
# TREN's premise is that unresolved modules are normal black boxes, and it
# wants the net graph, not a synthesis result. The design-specific flags
# (timescale, use-before-declare, copy-instantiation warnings) travel in the
# manifest's `slang_flags` / `yosys_flags`.
_SLANG_DEFAULT_FLAGS = ("--ignore-unknown-modules",)
_YOSYS_DEFAULT_FLAGS = ("-nolatches",)

# sv-elab (the yosys `read_slang` backend) rejects these outright. A manifest
# that lists them for slang-hier must still elaborate here, so drop them.
_SLANG_READ_UNSUPPORTED = frozenset({"--ignore-unknown-modules"})


def _run(cmd: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        cmd, cwd=str(cwd), check=True, capture_output=True, text=True
    )
    return proc.stdout


def _tool_diagnostics(proc: subprocess.CalledProcessError, limit: int = 40) -> str:
    """Trimmed tool stderr for the user - the reason flames are shown TOK_ID."""
    text = (proc.stderr or proc.stdout or "").strip()
    lines = text.splitlines()
    if len(lines) <= limit:
        return text
    return "..." + "\n".join(lines[-limit:])


def _effective_incdirs(manifest: Manifest) -> tuple[str, ...]:
    """Manifest incdirs plus the directory of every manifest source.

    The importers write this floor into a new manifest. A hand-written
    manifest lists only sources, so the elaborator applies the floor again:
    yosys searches the cwd and the including file's directory, slang searches
    only that directory and the ``-I`` dirs. ``.`` covers a source at the
    root, because slang does not search the cwd.
    """
    dirs = {d for d in manifest.incdirs if d}
    for source in manifest.sources:
        dirs.add(os.path.dirname(source) or ".")
    return tuple(sorted(dirs))


def parse_hier_line(line: str) -> dict[str, Any] | None:
    """Parse one slang-hier ``Module=... Instance=...`` line."""
    m = _HIER_RE.match(line.strip())
    if m is None:
        return None
    params: dict[str, str] = {}
    if m.group("params"):
        for part in m.group("params").split(", "):
            key, _, value = part.partition("=")
            params[key.strip()] = value.strip()
    return {
        "inst": m.group("inst"),
        "module": m.group("module"),
        "file": m.group("file"),
        "params": params,
    }


def run_slang_hier(manifest: Manifest, root: Path) -> list[dict[str, Any]]:
    """Scope tree + resolved params: ``slang-hier --params`` parsed.

    Sources/incdirs are passed as manifest-relative paths - the subprocess
    runs with ``cwd=root``, so tool-reported src stays ``tests/rtl/demo.sv:...``
    (relative) rather than absolute.
    """
    args = ["slang-hier", "--params"] + list(_SLANG_DEFAULT_FLAGS)
    for flag in manifest.slang_flags:
        args.append(flag)
    for inc in _effective_incdirs(manifest):
        args += ["-I", str(inc)]
    for name, value in manifest.defines_map().items():
        args += ["-D", f"{name}={value}" if value else name]
    for name, value in manifest.params_map().items():
        args += ["-G", f"{name}={value}"]
    args += [str(s) for s in manifest.sources]
    nodes: list[dict[str, Any]] = []
    for line in _run(args, root).splitlines():
        node = parse_hier_line(line)
        if node is not None:
            nodes.append(node)
    return nodes


def _slang_read_args(manifest: Manifest, read_flags: tuple[str, ...]) -> list[str]:
    """``read_slang`` option arguments, without the source files.

    ``--keep-hierarchy`` is the point of this backend: without it sv-elab
    inlines every submodule into the top. ``--empty-blackboxes`` keeps a
    body-less module as one shared black-box module instead of cloning it per
    instance. The two together give the same scope shape ``read_verilog``
    gives, with module names spelled ``<module>$<instance path>``.

    The caller appends the stub file and the manifest sources, so every
    option stays ahead of every file name.
    """
    args = ["--keep-hierarchy", "--empty-blackboxes"]
    for flag in read_flags:
        if flag not in _SLANG_READ_UNSUPPORTED:
            args.append(flag)
    for inc in _effective_incdirs(manifest):
        args += ["-I", str(inc)]
    for name, value in manifest.defines_map().items():
        args += ["-D", f"{name}={value}" if value else name]
    for name, value in manifest.params_map().items():
        args += ["-G", f"{name}={value}"]
    return args


def _yosys_verilog_script(manifest: Manifest, out_json: str) -> str:
    read_args: list[str] = list(_YOSYS_DEFAULT_FLAGS)
    for flag in manifest.yosys_flags:
        read_args.append(flag)
    for name, value in manifest.defines_map().items():
        read_args.append(f"-D{name}={value}" if value else f"-D{name}")
    for inc in _effective_incdirs(manifest):
        read_args.append(f"-I{inc}")
    sources = " ".join(read_args + [str(s) for s in manifest.sources])
    overrides = "".join(
        f"chparam -set {name} {value} {manifest.top}; "
        for name, value in manifest.params_map().items()
    )
    return (
        f"read_verilog -sv {sources}; "
        f"{overrides}"
        f"hierarchy -top {manifest.top}; "
        "proc; "
        f"write_json {out_json}"
    )


def _yosys_slang_script(
    manifest: Manifest, out_json: str, stub_path: str | None = None
) -> str:
    parts = _slang_read_args(manifest, manifest.slang_flags)
    if stub_path:
        parts.append(stub_path)
    parts.extend(str(source) for source in manifest.sources)
    return (
        f"read_slang {' '.join(parts)}; "
        f"hierarchy -top {manifest.top}; "
        "proc; "
        f"write_json {out_json}"
    )


def run_yosys(
    manifest: Manifest,
    root: Path,
    out_json: str,
    frontend: str | None = None,
    make_stubs: Callable[[], str | None] | None = None,
) -> str:
    """Netlist for connectivity/boundaries/black-box ports (srcs relative).

    Return the frontend that produced it: ``verilog`` or ``slang``.

    ``read_verilog`` cannot parse assignment patterns (``'{default: ...}``),
    streaming operators (``{<<8{...}}``) or a package import, so a design
    that uses them has no netlist at all. ``read_slang`` (the sv-elab
    frontend built into yosys) elaborates them. It has no tolerance for an
    instantiated module with no declaration, though, and ``read_verilog``
    has: try ``read_verilog`` first, and fall back to ``read_slang`` only
    when it fails.

    ``make_stubs`` writes inferred black-box declarations and returns their
    path. It runs only when the ``read_slang`` attempt starts, so a design
    that ``read_verilog`` parses never pays for it.
    """
    frontend = frontend or manifest.frontend
    if frontend not in FRONTENDS:
        raise ValueError(
            f"frontend must be one of {', '.join(FRONTENDS)}, got {frontend!r}"
        )
    order = ("verilog", "slang") if frontend == "auto" else (frontend,)
    for position, name in enumerate(order):
        if name == "verilog":
            script = _yosys_verilog_script(manifest, out_json)
        else:
            stub_path = make_stubs() if make_stubs is not None else None
            script = _yosys_slang_script(manifest, out_json, stub_path)
        try:
            _run(["yosys", "-Q", "-p", script], root)
            return name
        except subprocess.CalledProcessError:
            if position == len(order) - 1:
                raise
    raise AssertionError("unreachable: the frontend order is never empty")


def run_slang_ast(manifest: Manifest, root: Path, out_json: str) -> dict:
    """Elaborated AST for declarations (param kinds, source locations)."""
    args = [
        "slang",
        "--ast-json",
        out_json,
        "--ast-json-source-info",
        "--ast-json-detailed-types",
    ] + list(_SLANG_DEFAULT_FLAGS)
    for flag in manifest.slang_flags:
        args.append(flag)
    for inc in _effective_incdirs(manifest):
        args += ["-I", str(inc)]
    for name, value in manifest.defines_map().items():
        args += ["-D", f"{name}={value}" if value else name]
    for name, value in manifest.params_map().items():
        args += ["-G", f"{name}={value}"]
    args += [str(s) for s in manifest.sources]
    _run(args, root)
    return json.loads(Path(out_json).read_text())


# --------------------------------------------------------------------------
# slang --ast-json declaration extraction (targeted, defensive)
# --------------------------------------------------------------------------


def _ast_src(node: dict) -> str | None:
    f = node.get("source_file")
    if not f:
        return None
    return f"{f}:{node.get('source_line')}:{node.get('source_column')}"


def _src_pos(src: str | None) -> tuple[str, int, int] | None:
    """'tests/rtl/demo.sv:45:38' -> ('tests/rtl/demo.sv', 45, 38)."""
    if not src:
        return None
    parts = src.rsplit(":", 2)
    if len(parts) != 3:
        return None
    file, line, col = parts
    if not file or not line.isdigit() or not col.isdigit():
        return None
    return file, int(line), int(col)


def _type_string(node: dict | None) -> str | None:
    """Source-faithful type string from a slang --ast-json type node.

    PackedArrayType -> 'logic [31:0]', ScalarType -> 'logic', integer/other
    typedefs -> their name. Unknown shapes yield None (degrade gracefully).
    """
    if not isinstance(node, dict):
        return None
    kind = node.get("kind")
    if kind == "PackedArrayType":
        el = node.get("elementType") or {}
        base = el.get("name") if isinstance(el, dict) else None
        return f"{base or 'logic'} {node.get('range') or ''}".strip()
    if kind in ("ScalarType", "PredefinedIntegerType", "FloatingPointType",
                "EnumType", "StringType", "BooleanType", "PredefinedRealType"):
        return node.get("name")
    return None


def _net_type_string(node: dict) -> str | None:
    """Declared type of a Variable/Net member: 'wire [3:0]', 'reg [3:0]'.

    A net keeps its net type ('wire', 'tri', ...) apart from the data type,
    so slang reports a `wire [3:0]` as net type wire and element type logic.
    A variable carries no net type; its data type names the keyword. The
    range joins the keyword, so a leaf reads as the declaration.
    """
    net_type = (node.get("netType") or {}).get("name")
    rng = _type_range(node.get("type"))
    if net_type:
        return f"{net_type} {rng}" if rng else net_type
    return _type_string(node.get("type"))


def _type_range(node: dict | None) -> str | None:
    """Declared range of a slang type node ('[7:1]'), or None for a scalar.

    slang keeps the range as the source writes it - verified sv-lang 12:
    ``logic [0:7]`` stays '[0:7]' and ``logic [15:8]`` stays '[15:8]' - and
    resolves a parametrised bound to the instance's value.
    """
    if not isinstance(node, dict) or node.get("kind") != "PackedArrayType":
        return None
    rng = node.get("range")
    return rng or None


def _decl_default(lines: list[str], src: str | None) -> str | None:
    """Source-text default of a parameter at its declaration span.

    slang's --ast-json resolves the parameter *value* but does not expose the
    declared default (verified sv-lang 12: only resolved value + the
    per-instance initializer exist). TREN reads the default where it is
    written: the same source line as the declaration, the text after the
    first '=' up to the next terminator (',', ';', or a ')' that closes the
    declaration) or end of line.

    A bracket that the default opens itself does not end it: the scan keeps
    the whole expression of ``parameter B = (32 / 8);`` and stops on the
    ')' of ``#(parameter int W = 8)`` only because that one is unbalanced.
    """
    pos = _src_pos(src)
    if pos is None or not lines:
        return None
    _, line, col = pos
    if not 1 <= line <= len(lines):
        return None
    text = lines[line - 1]
    after = text[max(0, col - 1):] if col else text
    eq = after.find("=")
    if eq >= 0:
        rest = after[eq + 1:]
    else:
        # the AST column can land just past the initialiser on a single-line
        # declaration; fall back to the whole line when that still finds one
        eq = text.find("=")
        if eq < 0:
            return None
        rest = text[eq + 1:]
    end = len(rest)
    depth = 0
    for i, ch in enumerate(rest):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                end = i
                break
            depth -= 1
        elif ch in ",;" and depth == 0:
            end = i
            break
    tok = rest[:end].strip()
    return tok or None


def _src_line(src: str | None) -> int | None:
    """Leading line number of a src string ('tests/rtl/demo.sv:77.14-77.65')."""
    if not src:
        return None
    m = re.search(r":(\d+)", src)
    return int(m.group(1)) if m is not None else None


def _range_width(rng: str) -> int | None:
    m = re.fullmatch(r"\[(-?\d+)\s*:\s*(-?\d+)\]", rng)
    if m is None:
        return None
    return abs(int(m.group(1)) - int(m.group(2))) + 1


def _type_width(node: dict) -> int | None:
    kind = node.get("kind")
    if kind == "ScalarType":
        return 1
    if kind == "PackedArrayType":
        return _range_width(node.get("range") or "")
    return None


def _body_module(instance: dict) -> str | None:
    body = instance.get("body")
    if isinstance(body, dict):
        return body.get("name")
    return None


def _parse_ast_decls(
    ast: dict, root: Path | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (module_decls, generate_info).

    module_decls[module] = {"params": {name: {value, default, isLocal, src}},
                            "ports": {name: {direction, width, type, range, src}},
                            "nets": {name: {type, range, src}}}
    generate_info[instance][path] = {"kind": "array" | "block", "count": N,
                                     "src": ...}

    An array records its loop path plus one "block" entry per active index.
    A named or anonymous generate-if/case block records its own path. The
    zero-width array records only the "array" entry, so no scope appears.
    The key is the instance path, not the module: two instances of one
    module can activate different generate branches.

    A net of a generate block carries the block path yosys gives it
    ("g[0].gr"), so the two layers join on one name.

    ``root`` anchors the AST's relative source paths when reading declared
    parameter defaults from the source text (slang does not expose them).
    """
    decls: dict[str, dict[str, Any]] = {}
    generates: dict[str, dict[str, Any]] = {}
    src_cache: dict[str, list[str]] = {}

    def lines_for(src: str | None) -> list[str]:
        if not src:
            return []
        file = src.rsplit(":", 2)[0]
        if file not in src_cache:
            try:
                p = Path(file)
                if not p.is_absolute() and root is not None:
                    p = root / file
                src_cache[file] = p.read_text().splitlines()
            except OSError:
                src_cache[file] = []
        return src_cache[file]

    def decl(module: str) -> dict[str, Any]:
        return decls.setdefault(
            module, {"params": {}, "ports": {}, "nets": {}}
        )

    def visit_members(
        members: list[dict], module: str, instance: str, prefix: str = ""
    ) -> None:
        for m in members or []:
            kind = m.get("kind")
            if kind in ("Parameter", "LocalParameter"):
                src = _ast_src(m)
                decl(module)["params"][m["name"]] = {
                    "value": m.get("value", ""),
                    "default": _decl_default(lines_for(src), src),
                    "isLocal": bool(m.get("isLocal")),
                    "src": src,
                }
            elif kind == "Port":
                direction = {
                    "In": "in",
                    "Out": "out",
                    "InOut": "inout",
                }.get(m.get("direction"), "in")
                decl(module)["ports"][m["name"]] = {
                    "direction": direction,
                    "width": _type_width(m.get("type")),
                    "type": _type_string(m.get("type")),
                    "range": _type_range(m.get("type")),
                    "src": _ast_src(m),
                }
            elif kind in ("Variable", "Net"):
                # The body declarations: 'logic [W-1:0] x' and 'wire [3:0] w'.
                # A port appears here a second time, with the same type.
                decl(module)["nets"][f"{prefix}{m['name']}"] = {
                    "type": _net_type_string(m),
                    "range": _type_range(m.get("type")),
                    "src": _ast_src(m),
                }
            elif kind == "Instance":
                sub = _body_module(m)
                body = m.get("body")
                if sub is not None and isinstance(body, dict):
                    child_path = f"{instance}.{prefix}{m['name']}"
                    visit_members(body.get("members"), sub, child_path)
            elif kind == "GenerateBlockArray":
                blocks = [b for b in (m.get("members") or []) if b.get("kind") == "GenerateBlock"]
                array_path = f"{prefix}{m['name']}"
                generates.setdefault(instance, {})[array_path] = {
                    "kind": "array",
                    "count": len(blocks),
                    "src": _ast_src(m),
                }
                # slang leaves the block names empty; yosys spells them
                # 'g[0]' - index the blocks in order, as the generate scopes
                # above do.
                for i, block in enumerate(blocks):
                    block_path = f"{array_path}[{i}]"
                    generates[instance][block_path] = {
                        "kind": "block",
                        "count": 1,
                        "src": _ast_src(block) or _ast_src(m),
                    }
                    visit_members(
                        block.get("members"), module, instance, f"{block_path}."
                    )
            elif kind == "GenerateBlock":
                name = m.get("name") or ""
                if not name:
                    visit_members(m.get("members"), module, instance, prefix)
                    continue
                block_path = f"{prefix}{name}"
                generates.setdefault(instance, {})[block_path] = {
                    "kind": "block",
                    "count": 1,
                    "src": _ast_src(m),
                }
                visit_members(
                    m.get("members"), module, instance, f"{block_path}."
                )

    # The design lists one Instance per top-level module, not one per design:
    # a source set whose modules are not all reachable from a single top gets
    # several. Walking only the first left every other module without
    # declarations, so its ports carried no src and the TUI could not
    # highlight a port declaration.
    members = (ast.get("design") or {}).get("members") or []
    for root_instance in (m for m in members if m.get("kind") == "Instance"):
        body = root_instance.get("body")
        if not isinstance(body, dict):
            continue
        module = _body_module(root_instance) or root_instance.get("name") or ""
        instance = root_instance.get("name") or module
        visit_members(body.get("members"), module, instance)
    return decls, generates


# --------------------------------------------------------------------------
# IR assembly
# --------------------------------------------------------------------------


def _is_blackbox(mod: dict) -> bool:
    return bool((mod.get("attributes") or {}).get("blackbox"))


def _module_base(key: str) -> str:
    """Source module name of a yosys netlist module key.

    ``read_verilog`` mangles a parameterised module as
    ``$paramod\\core\\W=...``; ``read_slang --keep-hierarchy`` clones a
    module once per instance as ``core$top.u_core``. Both spell the source
    name first, and neither a module nor an instance name can hold ``$``.
    """
    base, _ = unmangle(key)
    if base == key:
        return key.partition("$")[0]
    return base


def module_key(
    modules: dict[str, Any],
    module: str,
    path: str,
    base_to_key: dict[str, str] | None = None,
) -> str | None:
    """Netlist key of ``module`` as instantiated at ``path``, or None.

    A ``read_slang`` netlist names the module after the instance path, so
    two instances of one module become two keys. A module that is not cloned
    (the top, or a shared black box) keeps its plain name. ``base_to_key``
    covers the ``read_verilog`` netlist, whose keys are ``$paramod``-mangled
    and carry the source name nowhere but inside the mangle.
    """
    exact = f"{module}${path}"
    if exact in modules:
        return exact
    if module in modules:
        return module
    if base_to_key is not None:
        return base_to_key.get(module)
    return None


def _ports_for(mod: dict, decl: dict[str, Any] | None) -> tuple[Port, ...]:
    """Ports of one module: yosys for the bits, slang's declaration for the rest.

    Direction comes from slang where it has an opinion (the overlay
    is the source-faithful layer, yosys is connectivity), falling back to the
    netlist for modules the AST walk never reached. They agree on the demo;
    the declaration is simply the one that stays right when they don't.

    Width stays yosys's bit count: it is what the connectivity map indexes.
    """
    ports: list[Port] = []
    decl_ports = (decl or {}).get("ports") or {}
    for name, p in (mod.get("ports") or {}).items():
        decl_port = decl_ports.get(name) or {}
        direction = decl_port.get("direction") or {
            "input": "in",
            "output": "out",
            "inout": "inout",
        }.get(p.get("direction"), "in")
        ports.append(
            Port(
                name=name,
                direction=direction,
                width=len(p.get("bits") or []),
                src=decl_port.get("src"),
                type=decl_port.get("type"),
                range=decl_port.get("range"),
            )
        )
    return tuple(ports)  # source (declaration) order; yosys JSON is stable


def _bit_key(b: Any) -> int:
    """Bit id for IR use. Yosys emits non-integer tokens ('0', 'x') for
    constant / don't-care positions of a net - those carry no connectivity
    and are represented as -1 (never a real node id, so they can never match
    a cell connection in the hit map)."""
    if isinstance(b, int):
        return b
    try:
        return int(b)
    except (TypeError, ValueError):
        return -1


def _nets_for(mod: dict, decl: dict[str, Any] | None = None) -> tuple[Net, ...]:
    """Nets of one module: yosys for the bits, slang for the type and range.

    yosys carries a bit count only, so a net declared ``wire [7:1]`` or
    ``logic [0:7]`` has no type or range to show without the overlay. A net
    the AST walk never reached keeps both None, and the reader falls back to
    the width.
    """
    decl_nets = (decl or {}).get("nets") or {}
    nets: list[Net] = []
    for name, n in (mod.get("netnames") or {}).items():
        if name.startswith("$"):
            continue
        bits = n.get("bits") or []
        decl_net = decl_nets.get(name) or {}
        nets.append(
            Net(
                name=name,
                width=len(bits),
                bits=tuple(_bit_key(b) for b in bits),
                src=(n.get("attributes") or {}).get("src"),
                range=decl_net.get("range"),
                type=decl_net.get("type"),
            )
        )
    return tuple(sorted(nets, key=lambda n: n.name))


def _cells_for(mod: dict, modules: dict[str, Any]) -> tuple[Cell, ...]:
    """Internal netlist cells of one module, with their pin directions.

    Submodule instance cells are skipped: they are the scope's `instances`
    and the trace crosses them by boundary, never as a cell. A cell row in
    the trace steps to the nets on its other side, so the directions matter:
    the inputs of a cell that drives the traced net, the outputs of a cell
    that loads it. An unresolved instance keeps `unknown` pins: the tree
    must not invent an input side for it.
    """
    cells: list[Cell] = []
    for name, cell in sorted((mod.get("cells") or {}).items()):
        ctype = cell.get("type", "")
        if ctype in modules:
            continue
        directions = cell_directions(cell, ctype, modules)
        pins: list[CellPin] = []
        for port in cell.get("connections") or {}:
            direction = "unknown" if directions is None else directions.get(port, "unknown")
            pins.append(
                CellPin(
                    name=port,
                    direction=direction,
                    width=len(cell.get("connections", {}).get(port) or []),
                )
            )
        cells.append(
            Cell(
                name=name,
                type=ctype,
                src=(cell.get("attributes") or {}).get("src"),
                pins=tuple(sorted(pins, key=lambda p: p.name)),
            )
        )
    return tuple(cells)


def _params_for(
    node: dict[str, Any], decl: dict[str, Any] | None
) -> tuple[Param, ...]:
    decl_params = (decl or {}).get("params") or {}
    params = []
    for name, value in (node.get("params") or {}).items():
        d = decl_params.get(name) or {}
        kind = "localparam" if d.get("isLocal") else "parameter"
        params.append(
            Param(
                name=name,
                value=value,
                default=d.get("default"),
                kind=kind,
            )
        )
    return tuple(params)


def _build_connectivity(
    netlist: dict, base_to_key: dict[str, str]
) -> tuple[NetConnectivity, ...]:
    """Connectivity for every module base, from one netlist key per base.

    ``NetConnectivity.scope`` is the source module name, the key the TUI uses
    to look connectivity up for any instance of that module. A ``read_slang``
    netlist clones a module per instance, so one key per base is enough while
    every instance elaborates the same netlist. Two instances that override
    different parameters share the first key's connectivity; ``read_verilog``
    already had that limit with its ``$paramod`` keys.
    """
    out: list[NetConnectivity] = []
    for base, key in sorted(base_to_key.items()):
        mod = netlist["modules"].get(key) or {}
        if not mod.get("cells"):
            continue
        named_bits = {
            tuple(n.get("bits") or [])
            for name, n in (mod.get("netnames") or {}).items()
            if not name.startswith("$")
        }
        seen_bits = set(named_bits)
        hits: dict[int, list[tuple[str, str, int, str | None]]] = {}
        for cname, cell in sorted((mod.get("cells") or {}).items()):
            directions = cell_directions(cell, cell.get("type", ""), netlist.get("modules"))
            for port, conn in (cell.get("connections") or {}).items():
                direction = None if directions is None else directions.get(port)
                if direction == "inout":
                    direction = None
                for pos, b in enumerate(conn):
                    if isinstance(b, int):
                        hits.setdefault(b, []).append((cname, port, pos, direction))
        for nname, n in sorted((mod.get("netnames") or {}).items()):
            bits = n.get("bits") or []
            # A `$` name is a yosys intermediate wire (the cone through cells
            # needs it) unless another name already carries the same bits;
            # then that name is the one to trace.
            if nname.startswith("$") and tuple(bits) in seen_bits:
                continue
            seen_bits.add(tuple(bits))
            links: list[BitLink] = []
            for position, b in enumerate(bits):
                rows = sorted(hits.get(_bit_key(b), []), key=lambda r: (r[0], r[1], r[2]))
                drivers = tuple(Endpoint(cell=c, port=p, bit=pos) for (c, p, pos, d) in rows if d == "out")
                loads = tuple(Endpoint(cell=c, port=p, bit=pos) for (c, p, pos, d) in rows if d == "in")
                unknown = tuple(Endpoint(cell=c, port=p, bit=pos) for (c, p, pos, d) in rows if d is None)
                links.append(
                    BitLink(
                        position=position,
                        drivers=drivers,
                        loads=loads,
                        unknown=unknown,
                    )
                )
            out.append(NetConnectivity(scope=base, net=nname, bits=tuple(links)))
    return tuple(sorted(out, key=lambda c: (c.scope, c.net)))


def _build_boundaries(
    netlist: dict,
    module_of: dict[str, str],
    base_to_key: dict[str, str],
) -> tuple[BoundaryLink, ...]:
    out: list[BoundaryLink] = []
    for path, base in sorted(module_of.items()):
        key = module_key(netlist["modules"], base, path, base_to_key)
        if not key:
            continue
        mod = netlist["modules"].get(key) or {}
        for cname, cell in sorted((mod.get("cells") or {}).items()):
            if cell.get("type") not in netlist["modules"]:
                continue  # internal $cell or unresolved - not a hierarchy edge
            child_path = f"{path}.{cname}"
            for port in (cell.get("connections") or {}):
                out.append(
                    BoundaryLink(
                        instance=cname,
                        port=port,
                        parent_scope=path,
                        child_scope=child_path,
                    )
                )
    return tuple(
        sorted(set(out), key=lambda b: (b.parent_scope, b.instance, b.port))
    )


def _parent(path: str) -> str | None:
    parts = path.rsplit(".", 1)
    return parts[0] if len(parts) == 2 else None


def build_design(
    manifest: Manifest,
    hier_nodes: list[dict[str, Any]],
    netlist: dict,
    ast_decls: tuple[dict[str, Any], dict[str, Any]],
    root: Path,
    frontend: str = "read_verilog",
) -> Design:
    del root
    decls, generates = ast_decls

    base_to_key: dict[str, str] = {}
    for key in netlist["modules"]:
        base_to_key.setdefault(_module_base(key), key)

    # --- module scopes (from slang-hier), ports/nets/blackbox from yosys ----
    scopes: dict[str, Scope] = {}
    module_of: dict[str, str] = {}
    for node in hier_nodes:
        path = node["inst"]
        base = node["module"]
        module_of[path] = base
        key = module_key(netlist["modules"], base, path, base_to_key)
        mod = netlist["modules"].get(key) or {}
        scopes[path] = Scope(
            name=path,
            module=base,
            blackbox=_is_blackbox(mod),
            params=_params_for(node, decls.get(base)),
            ports=_ports_for(mod, decls.get(base)),
            nets=_nets_for(mod, decls.get(base)),
            cells=_cells_for(mod, netlist["modules"]),
            src=((mod.get("attributes") or {}).get("src") or node.get("file")),
        )

    # --- generate scopes: active blocks from the AST, per module instance ---
    # slang-hier lists only the leaf instances, not the generate blocks on
    # the path. Walk the AST block paths once per module instance, so an
    # if/case/nested block becomes a scope even when it holds no instance
    # and has no array index. The zero-width array has no block entry.
    for path in sorted(module_of):
        for rel, gen in (generates.get(path) or {}).items():
            if gen.get("kind") != "block":
                continue
            gpath = f"{path}.{rel}"
            if gpath in scopes:
                continue
            scopes[gpath] = Scope(
                name=gpath,
                module=None,
                src=gen.get("src"),
            )

    # --- unresolved module scopes: yosys cells slang did not report --------
    # slang-hier omits an instance when its module declaration is absent.
    # Yosys still keeps the cell and its connected port names in the netlist.
    for path, base in tuple(module_of.items()):
        key = module_key(netlist["modules"], base, path, base_to_key)
        mod = netlist["modules"].get(key) or {} if key else {}
        for name, cell in sorted((mod.get("cells") or {}).items()):
            cell_type = cell.get("type", "")
            if cell_type in netlist["modules"] or cell_type.startswith("$"):
                continue
            child_path = f"{path}.{name}"
            if child_path in scopes:
                continue
            directions = cell_directions(cell, cell_type, netlist["modules"])
            ports = tuple(
                Port(
                    name=port,
                    direction=(directions or {}).get(port, "unknown"),
                    width=len(bits),
                )
                for port, bits in (cell.get("connections") or {}).items()
            )
            scopes[child_path] = Scope(
                name=child_path,
                module=cell_type,
                blackbox=True,
                unresolved=True,
                ports=ports,
                src=(cell.get("attributes") or {}).get("src"),
            )
            module_of[child_path] = cell_type

    # --- instances: direct child scopes; src = instantiation site ----------
    # The instance's src is the *instantiation in the parent* (the yosys cell
    # src, e.g. tests/rtl/demo.sv:77.14-77.65), not the child module's declaration -
    # that is what the source pane focuses. Fall back to the child scope's own
    # src for a generate-block row (it has no yosys cell). Children are
    # ordered by src line (source order), name as tiebreak.
    for path, scope in scopes.items():
        children = [q for q in scopes if _parent(q) == path]
        # A generate block is transparent to yosys: the cell for a child
        # instance lives in the nearest enclosing module, under the whole
        # dotted path ('g_if.u_if'), not in the generate scope itself.
        container = path
        while container not in module_of:
            parent = _parent(container)
            if parent is None:
                container = ""
                break
            container = parent
        key = (
            module_key(
                netlist["modules"],
                module_of.get(container, ""),
                container,
                base_to_key,
            )
            if container
            else None
        )
        mod = netlist["modules"].get(key) or {} if key else {}
        insts: list[Instance] = []
        for q in children:
            name = q.rsplit(".", 1)[1]
            cell_path = q[len(container) + 1:] if container else name
            cell = (mod.get("cells") or {}).get(cell_path) or {}
            cell_src = (cell.get("attributes") or {}).get("src")
            insts.append(
                Instance(
                    name=name,
                    module=scopes[q].module,
                    params=scopes[q].params,
                    blackbox=scopes[q].blackbox,
                    src=cell_src or scopes[q].src,
                )
            )
        insts.sort(key=lambda i: (_src_line(i.src) or 10**9, i.name))
        scopes[path] = replace(scope, instances=tuple(insts))

    return Design(
        top=manifest.top,
        scopes=tuple(sorted(scopes.values(), key=lambda s: s.name)),
        connectivity=_build_connectivity(netlist, base_to_key),
        boundaries=_build_boundaries(netlist, module_of, base_to_key),
        created_by=(
            f"tren.elaborate ({frontend} + slang-hier + slang --ast-json)"
        ),
    )


# --------------------------------------------------------------------------
# Public entry point + CLI
# --------------------------------------------------------------------------


def _drop_stub_modules(netlist: dict, stub_modules: frozenset[str]) -> None:
    """Remove the inferred declarations from the netlist again.

    They exist only so the elaboration accepts the instantiations. With them
    removed, the instance carries no ``port_directions`` and its module is
    absent, which is the shape TREN already reads as a black box: ports from
    the cell, directions unknown.
    """
    modules = netlist.get("modules") or {}
    for module in stub_modules:
        modules.pop(module, None)
    for mod in modules.values():
        for cell in (mod.get("cells") or {}).values():
            if cell.get("type") in stub_modules:
                cell.pop("port_directions", None)


def _stub_writer(
    stubs: dict[str, Stub],
    directory: Path,
    manifest: Manifest,
    root: Path,
    cache: IrCache | None,
    warnings: list[str] | None,
) -> Callable[[], str | None]:
    """A callable that writes the stub file once and returns its path."""
    state: dict[str, str | None] = {}

    def write() -> str | None:
        if "path" in state:
            return state["path"]
        if not stubs:
            state["path"] = None
            return None
        path = (
            cache.stub_path(manifest)
            if cache is not None
            else directory / "tren-stubs.sv"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_stubs(stubs, source=manifest.name))
        state["path"] = str(path)
        if warnings is not None:
            # Without a cache the file lives in the elaboration's temporary
            # directory and is gone when the run ends. Naming it in the
            # warning would point the user at a file that no longer exists.
            shown = path if cache is not None else None
            text = stub_summary(stubs, shown)
            if text:
                warnings.append(text)
        return state["path"]

    return write


def elaborate(
    manifest: Manifest,
    root: str | Path,
    *,
    cache: IrCache | None = None,
    refresh: bool = False,
    frontend: str | None = None,
    warnings_out: list[str] | None = None,
) -> Design:
    root = Path(root)
    if cache is not None and not refresh:
        hit = cache.get(manifest)
        if hit is not None:
            return hit
    hier_nodes = run_slang_hier(manifest, root)
    with tempfile.TemporaryDirectory(prefix="tren-elab-") as td:
        ast_path = str(Path(td) / "ast.json")
        run_slang_ast(manifest, root, ast_path)
        ast = json.loads(Path(ast_path).read_text())
        stubs = infer_stubs(ast, root)
        netlist_path = str(Path(td) / "netlist.json")
        used = run_yosys(
            manifest,
            root,
            netlist_path,
            frontend,
            _stub_writer(stubs, Path(td), manifest, root, cache, warnings_out),
        )
        netlist = json.loads(Path(netlist_path).read_text())
    if used == "slang":
        _drop_stub_modules(netlist, frozenset(stubs))
    design = build_design(
        manifest,
        hier_nodes,
        netlist,
        _parse_ast_decls(ast, root=root),
        root,
        frontend=f"read_{used}",
    )
    if cache is not None:
        cache.put(manifest, design)
    return design


def tool_failure_message(exc: subprocess.CalledProcessError) -> str:
    """One backend failure as stderr-ready text (no Python traceback)."""
    tool = (exc.cmd[0] if isinstance(exc.cmd, list) and exc.cmd else "backend").split("/")[-1]
    return (
        f"elaborate: {tool} failed (exit {exc.returncode})\n"
        f"{_tool_diagnostics(exc)}\n"
        f"full command: {' '.join(str(a) for a in exc.cmd)}"
    )


def design_summary(design: Design) -> list[str]:
    """The lines ``tren elaborate`` prints after a successful build."""
    n_scopes = len(design.scopes)
    n_modules = sum(1 for s in design.scopes if s.module is not None)
    n_gen = n_scopes - n_modules
    n_bb = sum(1 for s in design.scopes if s.blackbox)
    core = design.scope("top.u_core")
    param_example = ""
    if core is not None and core.params:
        p = core.params[0]
        param_example = f", e.g. {p.name}={p.value!r} ({p.kind})"
    return [
        f"top={design.top}: {n_scopes} scopes "
        f"({n_modules} modules, {n_gen} generate, {n_bb} black-box){param_example}",
        f"  connectivity: {sum(len(c.bits) for c in design.connectivity)} bit-links "
        f"across {len(design.connectivity)} nets",
        f"  boundaries:   {len(design.boundaries)}",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="manifest JSON (sources relative to --root)")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repo root for relative paths")
    parser.add_argument(
        "--cache", type=Path, default=None,
        help="cache directory (default: the cache.dir setting)",
    )
    parser.add_argument("--refresh", action="store_true", help="rebuild even on a cache hit")
    parser.add_argument(
        "--frontend", choices=FRONTENDS, default=None,
        help=(
            "yosys frontend for the netlist pass (default: the manifest's "
            "frontend, itself 'auto': read_verilog, then read_slang on failure)"
        ),
    )
    args = parser.parse_args(argv)

    try:
        settings = load_config().config
    except ConfigError as exc:
        print(f"tren: {exc}", file=sys.stderr)
        return 2
    cache = args.cache if args.cache is not None else Path(settings.cache_dir)

    warnings: list[str] = []
    try:
        manifest = Manifest.read(args.manifest)
    except (OSError, ValueError) as exc:
        print(f"tren: {exc}", file=sys.stderr)
        return 2
    duplicate_params = manifest.duplicate_params()
    duplicate_defines = manifest.duplicate_defines()
    for name, value in duplicate_defines.items():
        print(
            f"  warning: macro {name} is defined more than once; "
            f"using the last value {value}",
            file=sys.stderr,
        )
    for name, value in duplicate_params.items():
        print(
            f"  warning: parameter {name} is defined more than once; "
            f"using the last value {value}",
            file=sys.stderr,
        )
    if duplicate_params:
        manifest = replace(manifest, params=tuple(manifest.params_map().items()))
    if duplicate_defines:
        manifest = replace(manifest, defines=tuple(manifest.defines_map().items()))
    try:
        design = elaborate(
            manifest,
            args.root,
            cache=IrCache(cache, root=args.root),
            refresh=args.refresh,
            frontend=args.frontend,
            warnings_out=warnings,
        )
    except subprocess.CalledProcessError as exc:
        for warning in warnings:
            print(f"  warning: {warning}", file=sys.stderr)
        print(tool_failure_message(exc), file=sys.stderr)
        return 1

    for warning in warnings:
        print(f"  warning: {warning}", file=sys.stderr)
    for line in design_summary(design):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
