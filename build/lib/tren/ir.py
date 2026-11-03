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
import re
from dataclasses import asdict, dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints

# --------------------------------------------------------------------------
# Source-span parsing
# --------------------------------------------------------------------------


def parse_src(src: str | None) -> tuple[str, int, int] | None:
    """'<file>:<span>' -> (file, first_line, last_line).

    The two backends spell spans differently and both reach the IR:
      yosys attributes  'tests/rtl/demo.sv:77.14-77.65'  line.col[-line.col]
      slang --ast-json  'tests/rtl/demo.sv:46:25'        line:col
    Read naively, the slang form yields the *column* as the line and leaves
    the line glued to the file name, so ports (and generate scopes, whose
    src also comes from slang) focused a bogus file at a bogus line.
    """
    if not src:
        return None
    file, _, rest = src.rpartition(":")
    if not file:
        return None
    if rest.isdigit():
        # trailing field is a bare integer: either slang's 'file:line:col'
        # (then what precedes it ends in ':line') or a plain 'file:line'
        head, _, line = file.rpartition(":")
        if head and line.isdigit():
            return head, int(line), int(line)
    m = re.match(r"(\d+)(?:\.\d+)?(?:-(\d+)(?:\.\d+)?)?$", rest)
    if m is None:
        return None
    lo = int(m.group(1))
    hi = int(m.group(2) or lo)
    return file, lo, hi


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
    range: str | None = None  # declared range as written, e.g. "[7:1]"


_LITERAL = re.compile(
    r"^(?P<size>\d+)?'(?P<sign>[sS])?(?P<base>[bBoOdDhH])(?P<digits>[0-9a-fA-F_]+)$"
)
_BASE = {"b": 2, "o": 8, "d": 10, "h": 16}


def literal_int(text: str | None) -> int | None:
    """Value of a Verilog integer literal, or None when it is not one.

    Reads both spellings the backends produce for one number: the plain
    source text of a declared default ("8", "-1") and slang's resolved form
    ("32'd8", "3'b000"). A literal that carries x or z bits has no single
    value and gives None, as does any expression ("(32 / 8)").
    """
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    negative = text.startswith("-")
    if negative:
        text = text[1:].lstrip()
    digits_only = text.replace("_", "")
    if digits_only.isdigit():
        return -int(digits_only) if negative else int(digits_only)
    m = _LITERAL.match(text)
    if m is None:
        return None
    try:
        value = int(m.group("digits").replace("_", ""), _BASE[m.group("base").lower()])
    except ValueError:
        return None  # a digit the base does not have ("2'b12")
    return -value if negative else value


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
        overridden). The two sides are spelled by different layers: the
        default is source text, the value is slang's resolved literal, and
        the same number reads "3'b000" in one and "3'b0" in the other.
        Compare them as numbers, so only a real override gets the marker.

        When exactly one side is a literal the two are not comparable - a
        default of "(32 / 8)" against a value of "4" says nothing about an
        override - and the parameter counts as not overridden. Two
        non-literals (a string parameter) fall back to a text compare.
        """
        if self.kind != "parameter" or self.default is None:
            return False
        declared = literal_int(self.default)
        resolved = literal_int(str(self.value))
        if declared is None and resolved is None:
            return str(self.value).strip() != str(self.default).strip()
        if declared is None or resolved is None:
            return False
        return declared != resolved


@dataclass(frozen=True)
class Net:
    name: str
    width: int
    bits: tuple[int, ...] = ()
    src: str | None = None
    range: str | None = None  # declared range as written, e.g. "[0:7]"
    type: str | None = None  # declared type as written, e.g. "wire [3:0]"


@dataclass(frozen=True)
class Instance:
    name: str
    module: str | None = None  # base module name (unmangled); None => generate block
    params: tuple[Param, ...] = ()
    blackbox: bool = False
    src: str | None = None


@dataclass(frozen=True)
class CellPin:
    """One port of a netlist cell, with the direction yosys reports."""

    name: str
    direction: str = "in"  # "in" | "out"
    width: int = 0  # bits in the cell connection, constant positions included


@dataclass(frozen=True)
class Cell:
    """An internal netlist cell of a scope (a yosys cell, not an instance).

    `pins` carries the connection directions, so a trace can step from a
    cell to the nets on its other side: the inputs of a driver, or the
    outputs of a load. Submodule instances are not cells: they are the
    scope's `instances` and the trace crosses them by boundary.
    """

    name: str
    type: str = ""
    src: str | None = None
    pins: tuple[CellPin, ...] = ()


@dataclass(frozen=True)
class Scope:
    name: str  # hierarchical scope path (e.g. top.u_core.g[0].u_alu_g); the
    #            hand-written fixture is module-keyed instead (name == module)
    module: str | None = None  # base module name; None => generate block scope
    blackbox: bool = False
    params: tuple[Param, ...] = ()
    ports: tuple[Port, ...] = ()
    nets: tuple[Net, ...] = ()
    cells: tuple[Cell, ...] = ()
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
    """Drivers/loads of one bit position of a net.

    `unknown` holds the endpoints whose direction the netlist cannot tell
    (an instance of a module with no definition in the source set). The
    trace shows them in both directions, marked, rather than guessing.
    """

    position: int
    drivers: tuple[Endpoint, ...] = ()
    loads: tuple[Endpoint, ...] = ()
    unknown: tuple[Endpoint, ...] = ()


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

SCHEMA_VERSION = 2
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
    overlay) landed exactly that way, and this repo's own
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
    nets, cells, instances, params, rtl, rtl_focus, blackbox} and no bit
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
                    Port(name=p["name"], direction=p.get("dir", "in"), width=int(p.get("width", 0)), src=p.get("src"), range=p.get("range"), type=p.get("type"))
                    for p in s.get("ports", [])
                ),
                nets=tuple(
                    Net(name=n["name"], width=int(n.get("width", 0)), src=n.get("src"), range=n.get("range"), type=n.get("type"))
                    for n in s.get("nets", [])
                ),
                cells=tuple(
                    Cell(
                        name=c["name"],
                        type=c.get("type", ""),
                        src=c.get("src"),
                        pins=tuple(
                            CellPin(
                                name=p["name"],
                                direction=p.get("direction", "in"),
                                width=int(p.get("width", 0)),
                            )
                            for p in c.get("pins", [])
                        ),
                    )
                    for c in s.get("cells", [])
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