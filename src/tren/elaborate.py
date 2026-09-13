"""Elaboration runner: a manifest -> the TREN IR.

Wraps the pinned CLIs (versions recorded in README.md):

- ``slang-hier --params`` — the scope tree (every instance path, generate
  scopes included) plus per-instance resolved parameters (``W=32'd32``).
- ``yosys ... ; hierarchy -top <top>; proc; write_json`` — connectivity
  (per net bit, drivers/loads within the module), boundary links (submodule
  cell ports), black-box detection and ports, and net widths/src. The yosys
  rules from AGENTS.md apply: ``proc`` required, never ``opt``, never
  ``flatten``; bit IDs are positional-only across scopes.
- ``slang --ast-json --ast-json-source-info --ast-json-detailed-types`` —
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
import re
import subprocess
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


def _run(cmd: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        cmd, cwd=str(cwd), check=True, capture_output=True, text=True
    )
    return proc.stdout


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
    """Scope tree + resolved params: ``slang-hier --params`` parsed."""
    args = ["slang-hier", "--params"]
    for inc in manifest.incdirs:
        args += ["-I", str(root / inc)]
    for name, value in manifest.defines:
        args += ["-D", f"{name}={value}" if value else name]
    args += [str(root / s) for s in manifest.sources]
    nodes: list[dict[str, Any]] = []
    for line in _run(args, root).splitlines():
        node = parse_hier_line(line)
        if node is not None:
            nodes.append(node)
    return nodes


def run_yosys(manifest: Manifest, root: Path, out_json: str) -> None:
    """Netlist for connectivity/boundaries/black-box ports."""
    read_args: list[str] = []
    for name, value in manifest.defines:
        read_args.append(f"-D{name}={value}" if value else f"-D{name}")
    for inc in manifest.incdirs:
        read_args.append(f"-I{inc}")
    sources = " ".join(read_args + [str(root / s) for s in manifest.sources])
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
    ]
    for inc in manifest.incdirs:
        args += ["-I", str(root / inc)]
    for name, value in manifest.defines:
        args += ["-D", f"{name}={value}" if value else name]
    args += [str(root / s) for s in manifest.sources]
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


def _parse_ast_decls(ast: dict) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (module_decls, generate_info).

    module_decls[module] = {"params": {name: {value, isLocal, src}},
                            "ports": {name: {direction, width, src}}}
    generate_info[module][name] = {"count": N, "src": ...}
    """
    decls: dict[str, dict[str, Any]] = {}
    generates: dict[str, dict[str, Any]] = {}

    def decl(module: str) -> dict[str, Any]:
        return decls.setdefault(
            module, {"params": {}, "ports": {}}
        )

    def visit_members(members: list[dict], module: str) -> None:
        for m in members or []:
            kind = m.get("kind")
            if kind in ("Parameter", "LocalParameter"):
                decl(module)["params"][m["name"]] = {
                    "value": m.get("value", ""),
                    "isLocal": bool(m.get("isLocal")),
                    "src": _ast_src(m),
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
                for block in blocks:
                    visit_members(block.get("members"), module)
            elif kind == "GenerateBlock":
                visit_members(m.get("members"), module)

    root = next(
        (m for m in (ast.get("design") or {}).get("members") or [] if m.get("kind") == "Instance"),
        None,
    )
    if root is not None:
        body = root.get("body")
        if isinstance(body, dict):
            visit_members(body.get("members"), root.get("name"))
    return decls, generates


# --------------------------------------------------------------------------
# IR assembly
# --------------------------------------------------------------------------


def _is_blackbox(mod: dict) -> bool:
    return bool((mod.get("attributes") or {}).get("blackbox"))


def _ports_for(mod: dict, decl: dict[str, Any] | None) -> tuple[Port, ...]:
    ports: list[Port] = []
    decl_ports = (decl or {}).get("ports") or {}
    for name, p in (mod.get("ports") or {}).items():
        direction = {
            "input": "in",
            "output": "out",
            "inout": "inout",
        }.get(p.get("direction"), "in")
        src = (decl_ports.get(name) or {}).get("src")
        ports.append(
            Port(name=name, direction=direction, width=len(p.get("bits") or []), src=src)
        )
    return tuple(ports)  # source (declaration) order; yosys JSON is stable


def _nets_for(mod: dict) -> tuple[Net, ...]:
    nets: list[Net] = []
    for name, n in (mod.get("netnames") or {}).items():
        if name.startswith("$"):
            continue
        bits = n.get("bits") or []
        nets.append(
            Net(
                name=name,
                width=len(bits),
                bits=tuple(int(b) for b in bits),
                src=(n.get("attributes") or {}).get("src"),
            )
        )
    return tuple(sorted(nets, key=lambda n: n.name))


def _params_for(
    node: dict[str, Any], decl: dict[str, Any] | None
) -> tuple[Param, ...]:
    decl_params = (decl or {}).get("params") or {}
    params = []
    for name, value in (node.get("params") or {}).items():
        kind = "localparam" if (decl_params.get(name) or {}).get("isLocal") else "parameter"
        params.append(Param(name=name, value=value, kind=kind))
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
                rows = sorted(hits.get(int(b), []), key=lambda r: (r[0], r[1], r[2]))
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
                continue  # internal $cell or unresolved — not a hierarchy edge
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
            nets=_nets_for(mod),
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

    # --- instances: direct child scopes ------------------------------------
    for path, scope in scopes.items():
        children = sorted(q for q in scopes if _parent(q) == path)
        scopes[path] = replace(
            scope,
            instances=tuple(
                Instance(
                    name=q.rsplit(".", 1)[1],
                    module=scopes[q].module,
                    params=scopes[q].params,
                    blackbox=scopes[q].blackbox,
                    src=scopes[q].src,
                )
                for q in children
            ),
        )

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
    design = build_design(manifest, hier_nodes, netlist, _parse_ast_decls(ast), root)
    if cache is not None:
        cache.put(manifest, design)
    return design


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="manifest JSON (sources relative to --root)")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repo root for relative paths")
    parser.add_argument("--cache", type=Path, default=Path(".cache/tren"), help="cache directory")
    parser.add_argument("--refresh", action="store_true", help="rebuild even on a cache hit")
    args = parser.parse_args(argv)

    manifest = Manifest.read(args.manifest)
    design = elaborate(
        manifest,
        args.root,
        cache=IrCache(args.cache, root=args.root),
        refresh=args.refresh,
    )

    n_scopes = len(design.scopes)
    n_modules = sum(1 for s in design.scopes if s.module is not None)
    n_gen = n_scopes - n_modules
    n_bb = sum(1 for s in design.scopes if s.blackbox)
    core = design.scope("top.u_core")
    param_example = ""
    if core is not None and core.params:
        p = core.params[0]
        param_example = f", e.g. {p.name}={p.value!r} ({p.kind})"
    print(
        f"top={design.top}: {n_scopes} scopes "
        f"({n_modules} modules, {n_gen} generate, {n_bb} black-box){param_example}"
    )
    print(f"  connectivity: {sum(len(c.bits) for c in design.connectivity)} bit-links "
          f"across {len(design.connectivity)} nets")
    print(f"  boundaries:   {len(design.boundaries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
