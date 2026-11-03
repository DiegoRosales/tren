"""The design manifest - what TREN elaborates.

A hand-written, checkable-in file (sources/incdirs/defines/params/top/
blackboxes). Importers write one; the elaborator consumes one; the on-disk
cache is keyed by it. Parsed from JSON, losslessly round-trippable.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Manifest:
    name: str = "design"
    sources: tuple[str, ...] = ()
    incdirs: tuple[str, ...] = ()
    defines: tuple[tuple[str, str], ...] = ()  # (name, value)
    params: tuple[tuple[str, str], ...] = ()  # (name, value) top-level overrides
    top: str = "top"
    blackboxes: tuple[str, ...] = ()  # globs/user cuts
    slang_flags: tuple[str, ...] = ()  # extra slang-hier/slang args
    yosys_flags: tuple[str, ...] = ()  # extra read_verilog args

    def defines_map(self) -> dict[str, str]:
        return dict(self.defines)

    def params_map(self) -> dict[str, str]:
        return dict(self.params)

    # ---- JSON ----------------------------------------------------------

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(asdict(self), indent=indent)

    @classmethod
    def from_json(cls, text: str) -> Manifest:
        data = json.loads(text)
        return cls(
            name=data.get("name", "design"),
            sources=tuple(data.get("sources", ())),
            incdirs=tuple(data.get("incdirs", ())),
            defines=tuple(tuple(x) for x in data.get("defines", ())),
            params=tuple(tuple(x) for x in data.get("params", ())),
            top=data.get("top", "top"),
            blackboxes=tuple(data.get("blackboxes", ())),
            slang_flags=tuple(data.get("slang_flags", ())),
            yosys_flags=tuple(data.get("yosys_flags", ())),
        )

    def write(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json())

    @classmethod
    def read(cls, path: str | Path) -> Manifest:
        return cls.from_json(Path(path).read_text())

    # ---- stable bytes for cache keys -----------------------------------

    def canonical_bytes(self) -> bytes:
        """Deterministic, order-independent representation for hashing."""
        return json.dumps(
            {
                "name": self.name,
                "sources": sorted(self.sources),
                "incdirs": sorted(self.incdirs),
                "defines": sorted(self.defines),
                "params": sorted(self.params),
                "top": self.top,
                "blackboxes": sorted(self.blackboxes),
                "slang_flags": sorted(self.slang_flags),
                "yosys_flags": sorted(self.yosys_flags),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()