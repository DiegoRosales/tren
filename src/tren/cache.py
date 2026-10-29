"""On-disk IR cache, keyed by the manifest + source mtimes + the IR schema.

Elaboration is slow (minutes on a real SoC); TREN must start instantly on
cache hits. Key = sha256(canonical manifest bytes, plus each source's
relative path, mtime_ns and size), suffixed with `ir.schema_id()`. An
mtime/size change, a manifest edit, or an IR shape change invalidates the
entry; identical inputs on unchanged code hit.

The schema half is not decoration. Keyed on inputs alone, a blob written by
an older tren keeps hitting after the IR grows a field, and the new field
reads back as None for as long as the sources sit still — how `Port.type`
stayed invisible in the I/O pane.

mtime is the design's chosen proxy (see concept note, "Elaboration is slow").
A content-hash is the stricter alternative if identical-mtime edits ever
bite.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .ir import Design, schema_id
from .manifest import Manifest


def inputs_key(manifest: Manifest, root: str | Path) -> str:
    """Hash of the elaboration inputs: the manifest + the sources it names."""
    root = Path(root)
    h = hashlib.sha256()
    h.update(manifest.canonical_bytes())
    h.update(b"\0")
    for rel in sorted(manifest.sources):
        p = root / rel
        try:
            st = p.stat()
        except OSError:
            continue  # missing source: stable under "missing", flips when it appears
        h.update(f"{rel}\0{st.st_mtime_ns}\0{st.st_size}\0".encode())
    return h.hexdigest()


def cache_key(manifest: Manifest, root: str | Path) -> str:
    """Deterministic key for a manifest, its sources, and the IR shape.

    `<inputs>-<schema>`: same inputs read by a tren whose IR has changed is a
    miss, not a stale hit.
    """
    return f"{inputs_key(manifest, root)}-{schema_id()}"


class IrCache:
    """Cache directory of <key>.json IR blobs."""

    def __init__(self, directory: str | Path, root: str | Path) -> None:
        self.directory = Path(directory)
        self.root = Path(root)

    def path(self, manifest: Manifest) -> Path:
        return self.directory / f"{cache_key(manifest, self.root)}.json"

    def get(self, manifest: Manifest) -> Design | None:
        p = self.path(manifest)
        if not p.exists():
            return None
        try:
            return Design.read(p)
        except Exception:
            # corrupt/stale blob — treat as a miss and let it be overwritten
            return None

    def put(self, manifest: Manifest, design: Design) -> Path:
        p = self.path(manifest)
        p.parent.mkdir(parents=True, exist_ok=True)
        design.write(p)
        self._sweep(manifest, keep=p)
        return p

    def _sweep(self, manifest: Manifest, keep: Path) -> None:
        """Drop blobs for these same inputs written under another IR schema.

        Without this the schema suffix would just accumulate one dead blob per
        IR change (plus the legacy unsuffixed `<inputs>.json` names). Only the
        current manifest's own inputs are touched — other manifests' blobs are
        left to be swept when they are next elaborated.
        """
        for stale in self.directory.glob(f"{inputs_key(manifest, self.root)}*.json"):
            if stale != keep:
                stale.unlink(missing_ok=True)
