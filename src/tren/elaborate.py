"""Elaboration runner: a manifest -> the TREN IR.

Wraps the pinned CLIs (versions recorded in README.md):

- ``slang-hier --params`` - the scope tree (every instance path, generate
  scopes included) plus per-instance resolved parameters (``W=32'd32``).
- ``yosys ... ; hierarchy -top <top>; proc; write_json`` - connectivity
  (per net bit, drivers/loads within the module), boundary links (submodule
  cell ports), black-box detection and ports, and net widths/src. The yosys
  rules from AGENTS.md apply: ``proc`` required, never ``opt``, never
  ``flatten``; bit IDs are positional-only across scopes.
- ``slang --ast-json --ast-json-source-info --ast-json-detailed-types`` -
  declarations: parameter kinds (parameter vs localparam) and source
  locations for ports.

Scopes are keyed by their hierarchical instance path (``top.u_core.g[0]``)
and carry their resolved params; generate blocks become their own scopes
(``module is None``). The hand-written fixture (tui/fixtures/demo-tree.json)
is the module-keyed stand-in for UI tests; this module produces the real,
path-keyed IR.

Usage (from the repo root, inside the nix dev shell):

    nix develop . -c bash -c "PYTHONPATH=src python -m tren.elaborate rtl/demo.manifest.json"
    nix develop . -c bash -c "PYTHONPATH=src python -m tren.elaborate --refresh rtl/demo.manifest.json"
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
from typing import Any

from .bits import output_ports, unmangle
from .cache import IrCache
from .ir import (
    BitLink,
    BoundaryLink,
    Design,
    Endpoint,
    Instance,
    Net,
    NetConnectivity,
    Param,
    Port,
    Scope,
)
from .manifest import Manifest

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
    runs with ``cwd=root``, so tool-reported src stays ``rtl/demo.sv:...``
    (relative) rather than absolute.
    """
    args = ["slang-hier", "--params"] + list(_SLANG_DEFAULT_FLAGS)
    for flag in manifest.slang_flags:
        args.append(flag)
    for inc in _effective_incdirs(manifest):
        args += ["-I", str(inc)]
    for name, value in manifest.defines:
        args += ["-D", f"{name}={value}" if value else name]
    args += [str(s) for s in manifest.sources]
    nodes: list[dict[str, Any]] = []
    for line in _run(args, root).splitlines():
        node = parse_hier_line(line)
        if node is not None:
            nodes.append(node)
    return nodes


def run_yosys(manifest: Manifest, root: Path, out_json: str) -> None:
    """Netlist for connectivity/boundaries/black-box ports (srcs relative)."""
    read_args: list[str] = list(_YOSYS_DEFAULT_FLAGS)
    for flag in manifest.yosys_flags:
        read_args.append(flag)
    for name, value in manifest.defines:
        read_args.append(f"-D{name}={value}" if value else f"-D{name}")
    for inc in _effective_incdirs(manifest):
        read_args.append(f"-I{inc}")
    sources = " ".join(read_args + [str(s) for s in manifest.sources])
    script = (
        f"read_verilog -sv {sources}; "
        f"hierarchy -top {manifest.top}; "
        "proc; "
        f"write_json {out_json}"
    )
    _run(["yosys", "-Q", "-p", script], root)


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
    for name, value in manifest.defines:
        args += ["-D", f"{name}={value}" if value else name]
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
    """'rtl/demo.sv:45:38' -> ('rtl/demo.sv', 45, 38)."""
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


def _type_range(node: dict | None) -> str | None:
    """Declared range of a slang type node ('[7:1]'), or None for a scalar.

    slang keeps the range as the source writes it - verified sv-lang 11:
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
    declared default (verified sv-lang 11: only resolved value + the
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
    """Leading line number of a src string ('rtl/demo.sv:77.14-77.65')."""
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
    generate_info[module][name] = {"count": N, "src": ...}

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

    def visit_members(members: list[dict], module: str, prefix: str = "") -> None:
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
                    "type": _type_string(m.get("type")),
                    "range": _type_range(m.get("type")),
                    "src": _ast_src(m),
                }
            elif kind == "Instance":
                sub = _body_module(m)
                body = m.get("body")
                if sub is not None and isinstance(body, dict):
                    visit_members(body.get("members"), sub)
            elif kind == "GenerateBlockArray":
                blocks = [b for b in (m.get("members") or []) if b.get("kind") == "GenerateBlock"]
                generates.setdefault(module, {})[m["name"]] = {
                    "count": len(blocks),
                    "src": _ast_src(m),
                }
                # slang leaves the block names empty; yosys spells them
                # 'g[0]' - index the blocks in order, as the generate scopes
                # above do.
                for i, block in enumerate(blocks):
                    visit_members(block.get("members"), module, f"{prefix}{m['name']}[{i}].")
            elif kind == "GenerateBlock":
                visit_members(m.get("members"), module, prefix)

    root_instance = next(
        (m for m in (ast.get("design") or {}).get("members") or [] if m.get("kind") == "Instance"),
        None,
    )
    if root_instance is not None:
        body = root_instance.get("body")
        if isinstance(body, dict):
            visit_members(body.get("members"), root_instance.get("name"))
    return decls, generates


# --------------------------------------------------------------------------
# IR assembly
# --------------------------------------------------------------------------


def _is_blackbox(mod: dict) -> bool:
    return bool((mod.get("attributes") or {}).get("blackbox"))


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
    """Nets of one module: yosys for the bits, slang's declaration for the range.

    yosys carries a bit count only, so a net declared ``[7:1]`` or ``[0:7]``
    has no range to show without the overlay. A net the AST walk
    never reached keeps its range None, and the reader falls back to the
    width.
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
            )
        )
    return tuple(sorted(nets, key=lambda n: n.name))


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
    out: list[NetConnectivity] = []
    for base, key in sorted(base_to_key.items()):
        mod = netlist["modules"].get(key) or {}
        if not mod.get("cells"):
            continue
        hits: dict[int, list[tuple[str, str, int, bool]]] = {}
        for cname, cell in sorted((mod.get("cells") or {}).items()):
            outs = output_ports(cell, cell.get("type", ""))
            for port, conn in (cell.get("connections") or {}).items():
                for pos, b in enumerate(conn):
                    if isinstance(b, int):
                        hits.setdefault(b, []).append((cname, port, pos, port in outs))
        for nname, n in sorted((mod.get("netnames") or {}).items()):
            if nname.startswith("$"):
                continue
            bits = n.get("bits") or []
            links: list[BitLink] = []
            for position, b in enumerate(bits):
                rows = sorted(hits.get(_bit_key(b), []), key=lambda r: (r[0], r[1], r[2]))
                drivers = tuple(Endpoint(cell=c, port=p, bit=pos) for (c, p, pos, o) in rows if o)
                loads = tuple(Endpoint(cell=c, port=p, bit=pos) for (c, p, pos, o) in rows if not o)
                links.append(BitLink(position=position, drivers=drivers, loads=loads))
            out.append(NetConnectivity(scope=base, net=nname, bits=tuple(links)))
    return tuple(sorted(out, key=lambda c: (c.scope, c.net)))


def _build_boundaries(
    netlist: dict,
    base_to_key: dict[str, str],
    module_of: dict[str, str],
) -> tuple[BoundaryLink, ...]:
    out: list[BoundaryLink] = []
    for path, base in sorted(module_of.items()):
        key = base_to_key.get(base)
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
) -> Design:
    del root
    decls, generates = ast_decls

    base_to_key: dict[str, str] = {}
    for key in netlist["modules"]:
        base, _ = unmangle(key)
        base_to_key.setdefault(base, key)

    # --- module scopes (from slang-hier), ports/nets/blackbox from yosys ----
    scopes: dict[str, Scope] = {}
    module_of: dict[str, str] = {}
    for node in hier_nodes:
        path = node["inst"]
        base = node["module"]
        module_of[path] = base
        key = base_to_key.get(base)
        mod = netlist["modules"].get(key) or {}
        scopes[path] = Scope(
            name=path,
            module=base,
            blackbox=_is_blackbox(mod),
            params=_params_for(node, decls.get(base)),
            ports=_ports_for(mod, decls.get(base)),
            nets=_nets_for(mod, decls.get(base)),
            src=((mod.get("attributes") or {}).get("src") or node.get("file")),
        )

    # --- generate scopes: array-index path segments (g[0], genblk1, ...) ----
    for path in list(scopes):
        parts = path.split(".")
        for i in range(1, len(parts)):
            if "[" not in parts[i]:
                continue
            gpath = ".".join(parts[: i + 1])
            if gpath not in scopes:
                parent_base = module_of.get(".".join(parts[:i]))
                gen = ((generates.get(parent_base) or {}).get(re.sub(r"\[\d+\]", "", parts[i])) if parent_base else None) or {}
                scopes[gpath] = Scope(
                    name=gpath,
                    module=None,
                    src=gen.get("src"),
                )

    # --- instances: direct child scopes; src = instantiation site ----------
    # The instance's src is the *instantiation in the parent* (the yosys cell
    # src, e.g. rtl/demo.sv:77.14-77.65), not the child module's declaration -
    # that is what the source pane focuses. Fall back to the child scope's own
    # src when the cell src is unknown (generate-block children). Children are
    # ordered by src line (source order), name as tiebreak.
    for path, scope in scopes.items():
        children = [q for q in scopes if _parent(q) == path]
        key = base_to_key.get(scope.module) if scope.module else None
        mod = netlist["modules"].get(key) or {} if key else {}
        insts: list[Instance] = []
        for q in children:
            name = q.rsplit(".", 1)[1]
            cell = (mod.get("cells") or {}).get(name) or {}
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
        boundaries=_build_boundaries(netlist, base_to_key, module_of),
        created_by="tren.elaborate (slang-hier + yosys + slang --ast-json)",
    )


# --------------------------------------------------------------------------
# Public entry point + CLI
# --------------------------------------------------------------------------


def elaborate(
    manifest: Manifest,
    root: str | Path,
    *,
    cache: IrCache | None = None,
    refresh: bool = False,
) -> Design:
    root = Path(root)
    if cache is not None and not refresh:
        hit = cache.get(manifest)
        if hit is not None:
            return hit
    hier_nodes = run_slang_hier(manifest, root)
    with tempfile.TemporaryDirectory(prefix="tren-elab-") as td:
        netlist_path = str(Path(td) / "netlist.json")
        run_yosys(manifest, root, netlist_path)
        netlist = json.loads(Path(netlist_path).read_text())
        ast_path = str(Path(td) / "ast.json")
        run_slang_ast(manifest, root, ast_path)
        ast = json.loads(Path(ast_path).read_text())
    design = build_design(manifest, hier_nodes, netlist, _parse_ast_decls(ast, root=root), root)
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
    parser.add_argument("--cache", type=Path, default=Path(".cache/tren"), help="cache directory")
    parser.add_argument("--refresh", action="store_true", help="rebuild even on a cache hit")
    args = parser.parse_args(argv)

    manifest = Manifest.read(args.manifest)
    try:
        design = elaborate(
            manifest,
            args.root,
            cache=IrCache(args.cache, root=args.root),
            refresh=args.refresh,
        )
    except subprocess.CalledProcessError as exc:
        print(tool_failure_message(exc), file=sys.stderr)
        return 1

    for line in design_summary(design):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
