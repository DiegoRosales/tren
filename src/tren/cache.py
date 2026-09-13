"""On-disk IR cache, keyed by the manifest + source mtimes.

Elaboration is slow (minutes on a real SoC); TREN must start instantly on
cache hits. Key = sha256(canonical manifest bytes, plus each source's
relative path, mtime_ns and size). An mtime/size change (or a manifest edit)
invalidates the entry; identical inputs hit.

mtime is the design's chosen proxy (see concept note, "Elaboration is slow").
A content-hash is the stricter alternative if identical-mtime edits ever
bite.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .ir import Design
from .manifest import Manifest


def cache_key(manifest: Manifest, root: str | Path) -> str:
    """Deterministic key for a manifest + the sources it names under root."""
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
        return p