"""Widget-agnostic intermediate representation for TREN.

These frozen dataclasses are the contract the TUI consumes and the backends
produce. They mirror the shapes that yosys `write_json` and slang give us
(see the design note, note 1503454, "The IR"), and carry source locations so
`e`-jumps and the focused-source pane work.

Design rules (from AGENTS.md):
- Bit IDs are module-local; cross-boundary links are positional only.
- Never hold the whole graph in memory — lazy scope population later.
- JSON (de)serialisation is lossless so the on-disk cache round-trips.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints

# --------------------------------------------------------------------------
# IR dataclasses
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Port:
    name: str
    direction: str  # "in" | "out" | "inout"
    width: int
    src: str | None = None
    type: str | None = None  # source-faithful type string from slang, e.g. "logic [31:0]"


@dataclass(frozen=True)
class Param:
    name: str
    value: str = ""
    default: str | None = None
    kind: str = "parameter"  # parameter | localparam

    @property
    def overridden(self) -> bool:
        """The instance resolves to something other than the declared default.

        Only meaningful for ``parameter`` (a ``localparam`` cannot be
        overridden); compares the source-text default with the resolved
        value as strings (e.g. "8" vs "32'd32").
        """
        return (
            self.kind == "parameter"
            and self.default is not None
            and str(self.value) != str(self.default)
        )


@dataclass(frozen=True)
class Net:
    name: str
    width: int
    bits: tuple[int, ...] = ()
    src: str | None = None


@dataclass(frozen=True)
class Instance:
    name: str
    module: str | None = None  # base module name (unmangled); None => generate block
    params: tuple[Param, ...] = ()
    blackbox: bool = False
    src: str | None = None


@dataclass(frozen=True)
class Scope:
    name: str  # hierarchical scope path (e.g. top.u_core.g[0].u_alu_g); the
    #            hand-written fixture is module-keyed instead (name == module)
    module: str | None = None  # base module name; None => generate block scope
    blackbox: bool = False
    params: tuple[Param, ...] = ()
    ports: tuple[Port, ...] = ()
    nets: tuple[Net, ...] = ()
    instances: tuple[Instance, ...] = ()
    src: str | None = None
    # Source pane helpers: the RTL snippet for this scope and the 1-based
    # line range to highlight (the instantiation in its parent).
    rtl: tuple[str, ...] = ()
    rtl_focus: tuple[int, int] = (1, 1)


@dataclass(frozen=True)
class Endpoint:
    """A (cell, port, bit position) touching a net bit."""

    cell: str
    port: str
    bit: int


@dataclass(frozen=True)
class BitLink:
    """Drivers/loads of one bit position of a net."""

    position: int
    drivers: tuple[Endpoint, ...] = ()
    loads: tuple[Endpoint, ...] = ()


@dataclass(frozen=True)
class NetConnectivity:
    net: str
    scope: str = ""  # the scope (module name) this connectivity describes
    bits: tuple[BitLink, ...] = ()


@dataclass(frozen=True)
class BoundaryLink:
    """A parent-side port connection to a child scope (positional only)."""

    instance: str  # the submodule cell name in the parent scope
    port: str
    parent_scope: str  # module key of the parent
    child_scope: str  # module key of the child (unmangle(parent cell type))


@dataclass(frozen=True)
class Design:
    top: str
    scopes: tuple[Scope, ...] = ()
    connectivity: tuple[NetConnectivity, ...] = ()
    boundaries: tuple[BoundaryLink, ...] = ()
    created_by: str | None = None

    def scope(self, name: str) -> Scope | None:
        for s in self.scopes:
            if s.name == name:
                return s
        return None

    # ---- JSON ----------------------------------------------------------

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(asdict(self), indent=indent)

    @classmethod
    def from_json(cls, text: str) -> Design:
        return _hydrate(cls, json.loads(text))

    def write(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json())

    @classmethod
    def read(cls, path: str | Path) -> Design:
        return cls.from_json(Path(path).read_text())


# --------------------------------------------------------------------------
# Schema identity (what makes a cached blob stale)
# --------------------------------------------------------------------------

SCHEMA_VERSION = 1
"""Bump by hand when the *meaning* of an unchanged field changes.

The structural fingerprint below catches added/renamed/retyped fields on its
own. It cannot see a change in what a field already spelled the same way now
holds — e.g. `Port.type` switching from 'logic [31:0]' to a canonical form.
Those need a bump here.
"""


def _dataclasses_in(annotation: Any) -> list[type]:
    """Dataclasses reachable from an annotation (`tuple[Scope, ...]` -> Scope)."""
    if is_dataclass(annotation):
        return [annotation]
    found: list[type] = []
    for arg in get_args(annotation):
        found.extend(_dataclasses_in(arg))
    return found


def schema_id() -> str:
    """Fingerprint of the IR's on-disk shape, for the cache key.

    A cached blob is only reusable by the code that wrote it. Keying the cache
    on the *inputs* alone (manifest + source mtimes) is not enough: grow the IR
    a field and every blob in the wild keeps hitting, answering the new field
    with None forever. That is not hypothetical — `Port.type` (the slang
    overlay, card 219) landed exactly that way, and this repo's own
    `.cache/tren` was still serving `type=None` for every port, so the I/O pane
    showed no types on any machine with a warm cache.

    Derived from the dataclass graph rooted at `Design` — every field name with
    its annotation as written — so the common case (a new field) invalidates
    itself with nothing to remember. `SCHEMA_VERSION` covers the rest.
    """
    return _fingerprint(Design)


def _fingerprint(root: type) -> str:
    """sha256 over a dataclass graph's field layout, depth-first from `root`."""
    parts = [f"v{SCHEMA_VERSION}"]
    seen: set[type] = set()

    def visit(cls: type) -> None:
        if cls in seen:
            return
        seen.add(cls)
        hints = get_type_hints(cls)
        for f in fields(cls):
            # `from __future__ import annotations` keeps f.type as the source
            # spelling; traversal uses the resolved hint.
            parts.append(f"{cls.__name__}.{f.name}:{f.type}")
            for nested in _dataclasses_in(hints[f.name]):
                visit(nested)

    visit(root)
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------
# (De)serialisation helpers
# --------------------------------------------------------------------------

def _convert(annotation: Any, value: Any) -> Any:
    if value is None:
        return None
    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is not None and (origin is Union or origin is types_union()):
        for a in args:
            if a is not type(None):
                return _convert(a, value)
        return None

    if is_dataclass(annotation):
        return _hydrate(annotation, value)

    if origin is tuple:
        inner = args[0] if args else None
        if inner is None:
            return tuple(value)
        return tuple(_convert(inner, v) for v in value)

    if origin is list:
        inner = args[0] if args else None
        if inner is None:
            return list(value)
        return [_convert(inner, v) for v in value]

    return value


def types_union() -> type:
    # types.UnionType exists from Python 3.10; `X | None` annotations resolve
    # to it. Fall back gracefully if get_type_hints already resolved them.
    import types

    return getattr(types, "UnionType", Union)


def _hydrate(cls: type, data: dict[str, Any]) -> Any:
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        kwargs[f.name] = _convert(hints[f.name], data.get(f.name))
    return cls(**kwargs)


# --------------------------------------------------------------------------
# Adapter for the hand-written demo fixture (TREN-IR-ish JSON)
# --------------------------------------------------------------------------


def design_from_fixture(path: str | Path) -> Design:
    """Load the demo-tree.json fixture (sibling/eval or vendored copy).

    The fixture is a hand-written TREN-IR-ish shape with scopes -> {ports,
    nets, instances, params, rtl, rtl_focus, blackbox} and no bit
    connectivity; connectivity/boundaries come back empty until the yosys
    adapter (elaborate.py) fills them.
    """
    data = json.loads(Path(path).read_text())
    scopes: list[Scope] = []
    for name, s in data["scopes"].items():
        scopes.append(
            Scope(
                name=name,
                module=name,  # fixture is module-keyed: name == module
                blackbox=bool(s.get("blackbox", False)),
                params=tuple(
                    Param(name=p["name"], value=p.get("value", ""), default=p.get("default"), kind=p.get("kind", "parameter"))
                    for p in s.get("params", [])
                ),
                ports=tuple(
                    Port(name=p["name"], direction=p.get("dir", "in"), width=int(p.get("width", 0)), src=p.get("src"))
                    for p in s.get("ports", [])
                ),
                nets=tuple(
                    Net(name=n["name"], width=int(n.get("width", 0)), src=n.get("src"))
                    for n in s.get("nets", [])
                ),
                instances=tuple(
                    Instance(
                        name=i["name"],
                        module=i["module"],
                        params=tuple(Param(name=p["name"], value=p.get("value", "")) for p in i.get("params", [])),
                        blackbox=bool(i.get("blackbox", False)),
                        src=i.get("src"),
                    )
                    for i in s.get("instances", [])
                ),
                src=s.get("src"),
                rtl=tuple(s.get("rtl", [])),
                rtl_focus=tuple(s.get("rtl_focus", [1, 1])),
            )
        )
    return Design(top="top", scopes=tuple(scopes), created_by="demo fixture")