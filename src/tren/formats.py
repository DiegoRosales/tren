"""Declarative yank-path formats.

`y` pastes the current selection into some *other* tool, and every target
spells a hierarchical path differently. Formats are data, not code (design
note, "Yanking paths — the format problem"): a small spec + one renderer,
so users add a target without patching TREN. Each format is:

  separator      how path segments join ('.', '/', ':', ...)
  leading_sep    prefix the result with the separator (Questa /tb/dut/...)
  include_top    keep or drop the top-module segment (Vivado paths are
                 relative to the top cell — dropping it is the default there)
  array_notation how a generate/array index renders ('[{i}]', '.{i}', ...)
  bit_select     how a bit select renders ('[{i}]' for VCD, '{i}' in SV)
  escape         post-escape of the whole string
                 none | tcl (brackets/$/" are command substitution in Tcl —
                 unbraced paste silently does something insane) | shell

The four starting presets take their shapes from the design note's table
(SV / Vivado get_pins / yosys select / VCD dot-path). The note marks those
details unverified against the running tools ⚠; the vendor index spellings
in particular want a real design to check against. The *mechanism* is fully
exercised by the unit tests (including a custom format that diverges in
every dimension).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ARRAY_SEGMENT = re.compile(r"^(?P<base>.+?)(?:\[(?P<idx>\d+)\])?$")

_ESCAPERS = {
    "none": lambda s: s,
    "tcl": lambda s: re.sub(r'([\[\]";$`\\])', r"\\\1", s),
    "shell": lambda s: "'" + s.replace("'", "'\\''") + "'",
}


@dataclass(frozen=True)
class PathFormat:
    name: str
    separator: str = "."
    leading_sep: bool = False
    include_top: bool = True
    array_notation: str = "[{i}]"
    bit_select: str = "[{i}]"
    escape: str = "none"

    def __post_init__(self) -> None:
        if self.escape not in _ESCAPERS:
            raise ValueError(f"unknown escape mode: {self.escape!r}")


PRESETS: tuple[PathFormat, ...] = (
    PathFormat(
        name="sv",
        separator=".",
        include_top=True,
        array_notation="[{i}]",
        bit_select="[{i}]",
        escape="none",
    ),
    PathFormat(
        name="vivado",
        separator="/",
        include_top=False,
        array_notation="[{i}]",
        bit_select="[{i}]",
        escape="tcl",
    ),
    PathFormat(
        name="yosys",
        separator="/",
        include_top=True,
        array_notation="[{i}]",
        bit_select="[{i}]",
        escape="none",
    ),
    PathFormat(
        name="vcd",
        separator=".",
        include_top=True,
        array_notation="[{i}]",
        bit_select="[{i}]",
        escape="none",
    ),
)

BY_NAME = {p.name: p for p in PRESETS}
DEFAULT = PRESETS[0]


def render(
    fmt: PathFormat,
    segments: list[str],
    element: str | None = None,
    bit: int | None = None,
) -> str:
    """Render a hierarchical path through ``fmt``.

    ``segments`` are the scope chain, root first (array segments may already
    carry brackets, e.g. ``g[0]`` — they are split and re-rendered through
    ``array_notation``). ``element`` is the net/port name appended last;
    ``bit`` adds a bit select to the element.
    """
    parts: list[str] = []
    chain = list(segments)
    if not fmt.include_top and chain:
        chain = chain[1:]
    for seg in chain:
        m = _ARRAY_SEGMENT.match(seg)
        base = m.group("base") if m else seg
        idx = m.group("idx") if m else None
        if idx is not None:
            base = base + fmt.array_notation.format(i=idx)
        parts.append(base)
    tail = element
    if element is not None and bit is not None:
        tail = element + fmt.bit_select.format(i=bit)
    if tail is not None:
        parts.append(tail)
    out = fmt.separator.join(parts)
    if fmt.leading_sep and out:
        out = fmt.separator + out
    return _ESCAPERS[fmt.escape](out)