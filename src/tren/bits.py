"""Bit-level connectivity over a yosys `write_json` netlist.

The TREN spike: build the bit map (bit id -> the cell connections that touch
it) and answer "drivers" / "loads" for a net, crossing hierarchy boundaries.

Verified rules this module encodes (see tren_testing/t1_yosys/findings.md):
- yosys JSON must come from `... ; hierarchy -top <top>; proc; write_json`.
  `proc` is required by the JSON backend; `opt` is destructive; `flatten`
  is never used, so submodule cells keep the hierarchy.
- Bit IDs are module-local. The only valid cross-boundary link is POSITIONAL
  within a port: parent connection bit `k` of port `p` corresponds to child
  net `p` bit `k`. Raw numbers never match across scopes.
- Connection entries that are strings are constants (e.g. `"0"`, `"1"`),
  not bit IDs, and are ignored by the bit map.
- Parameterised module names come out mangled as
  `$paramod\\<name>\\<PARAM>=<value>`; `unmangle` recovers them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

Netlist = dict[str, Any]


# --------------------------------------------------------------------------
# Loading / naming
# --------------------------------------------------------------------------

def load_netlist(path: str | Path) -> Netlist:
    """Load a yosys write_json netlist."""
    return json.loads(Path(path).read_text())


def unmangle(name: str) -> tuple[str, dict[str, str]]:
    """Split a yosys module name into (base name, resolved parameters).

    ``'$paramod\\core\\W=s32\\'...'`` -> ``('core', {'W': "s32'..."})``.
    Plain names and anything not ``$paramod``-prefixed pass through with an
    empty parameter map.
    """
    if not name.startswith("$paramod"):
        return name, {}
    parts = name.split("\\")  # ["$paramod", "<name>", "PARAM=value", ...]
    base = parts[1]
    params: dict[str, str] = {}
    for part in parts[2:]:
        if "=" in part:
            key, _, value = part.partition("=")
            params[key] = value
    return base, params


def describe_module(design: Netlist) -> dict[str, dict[str, Any]]:
    """Map module key -> {'name': base name, 'params': {...}} for display."""
    out: dict[str, dict[str, Any]] = {}
    for key in design["modules"]:
        base, params = unmangle(key)
        out[key] = {"name": base, "params": params}
    return out


# --------------------------------------------------------------------------
# Cell port directions
# --------------------------------------------------------------------------

def cell_directions(
    cell: dict, cell_type: str, modules: dict[str, dict[str, Any]] | None = None
) -> dict[str, str] | None:
    """Port directions of a cell as ``in``/``out``/``inout``, or None.

    The netlist knows a cell's directions only when it has the module it
    instantiates: either the explicit ``port_directions`` map, or the child
    module's port declarations. An unresolved module - an IP with no stub in
    the source set - has neither. Return None for that case, so the caller
    can render the direction as unknown instead of guessing (a wrong
    direction silently drops a driver or a load from every trace through it).
    Internal ``$...`` cells drive through ``Y``.
    """
    directions = cell.get("port_directions")
    if isinstance(directions, dict) and directions:
        names = {"input": "in", "output": "out", "inout": "inout"}
        return {name: names.get(direction, "inout") for name, direction in directions.items()}
    module = (modules or {}).get(cell_type)
    if module is not None:
        names = {"input": "in", "output": "out", "inout": "inout"}
        known = {
            name: names[direction]
            for name, port in (module.get("ports") or {}).items()
            if (direction := port.get("direction")) in names
        }
        if known:
            return known
    if cell_type.startswith("$"):
        return {
            port: ("out" if port == "Y" else "in")
            for port in cell.get("connections") or {}
        }
    return None


def cell_connections(cell: dict) -> Iterable[tuple[str, list[int]]]:
    """Yield (port, integer bit ids) for a cell, skipping constant entries."""
    for port, entries in cell.get("connections", {}).items():
        bits = [b for b in entries if isinstance(b, int)]
        if bits:
            yield port, bits


# --------------------------------------------------------------------------
# The bit map
# --------------------------------------------------------------------------

def rows_hitting_bits(
    module: dict, bits: set[int]
) -> list[dict[str, Any]]:
    """Every cell connection that touches any of ``bits``, with direction.

    A row is: {cell, type, port, dir (in|out|unknown), bits (ordered as in
    the connection), src (cell src attribute)}. An instance of a module with
    no definition has ``dir`` unknown: the caller must not treat it as a
    driver or a load by default.
    """
    rows: list[dict[str, Any]] = []
    for name, cell in module.get("cells", {}).items():
        cell_type = cell.get("type", "")
        directions = cell_directions(cell, cell_type)
        for port, conn_bits in cell_connections(cell):
            hit = [b for b in conn_bits if b in bits]
            if not hit:
                continue
            direction = None if directions is None else directions.get(port)
            if direction == "inout":
                direction = None
            rows.append(
                {
                    "cell": name,
                    "type": cell_type,
                    "port": port,
                    "dir": direction or "unknown",
                    "bits": hit,
                    "src": cell.get("attributes", {}).get("src"),
                }
            )
    return rows


def drivers_of(module: dict, bits: set[int]) -> list[dict[str, Any]]:
    """Driver rows: the cell ports that compute these bits.

    A row whose direction is unknown is listed too: it may be the driver,
    and dropping it is worse than marking it.
    """
    return [r for r in rows_hitting_bits(module, bits) if r["dir"] in ("out", "unknown")]


def loads_of(module: dict, bits: set[int]) -> list[dict[str, Any]]:
    """Load rows: the cell ports that read these bits (unknown included)."""
    return [r for r in rows_hitting_bits(module, bits) if r["dir"] in ("in", "unknown")]


# --------------------------------------------------------------------------
# Hierarchy crossing
# --------------------------------------------------------------------------

def cross_into(
    design: Netlist, driver: dict[str, Any], parent_module_key: str
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """If ``driver`` is a submodule cell, return (child_module, child_net).

    child_module is the referenced module's dict (found by the cell's type,
    e.g. ``$paramod\\core\\W=...``) and child_net its ``netnames[port]`` entry.
    The positional correspondence is implicit: driver bits `k..n` map to the
    child net's bits at the same positions. Returns None for plain cells.
    """
    cell_type = driver["type"]
    if cell_type not in design["modules"]:
        return None
    child_module = design["modules"][cell_type]
    netname = driver["port"]
    child_net = child_module.get("netnames", {}).get(netname)
    if child_net is None:
        return None
    return child_module, child_net


def trace_drivers(
    design: Netlist, module_key: str, net_name: str, *, _seen: set[tuple[str, str]] | None = None
) -> list[dict[str, Any]]:
    """Driver chain for a net, descending through every hierarchy boundary.

    Returning one entry per step: the in-module driver plus, when it is a
    submodule cell, a nested ``into`` step with the child module's drivers.
    """
    seen = set() if _seen is None else _seen
    if (module_key, net_name) in seen:
        return [{"note": "cycle guard"}]
    seen.add((module_key, net_name))

    module = design["modules"][module_key]
    net = module.get("netnames", {}).get(net_name)
    if net is None:
        return [{"note": f"no net {net_name!r} in {module_key}"}]
    bits = set(net["bits"])

    chain: list[dict[str, Any]] = []
    for driver in drivers_of(module, bits):
        entry: dict[str, Any] = dict(driver)
        entry["net_src"] = net.get("attributes", {}).get("src")
        boundary = cross_into(design, driver, module_key)
        if boundary is not None:
            child_module_def, child_net = boundary
            child_key = driver["type"]  # the referenced module key, e.g. $paramod\core\W=...
            entry["into"] = {
                "module": child_key,
                "port": driver["port"],
                "child_bits": child_net.get("bits"),
                "drivers": trace_drivers(design, child_key, driver["port"], _seen=seen),
            }
        chain.append(entry)
    return chain