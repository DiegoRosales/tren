"""Tests for the IR model, manifest and on-disk cache (card 207).

Run:  nix develop . -c bash -c "PYTHONPATH=src python -m unittest discover -s tests"
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tren.cache import IrCache, cache_key  # noqa: E402
from tren.ir import (  # noqa: E402
    BitLink,
    BoundaryLink,
    Design,
    Endpoint,
    NetConnectivity,
    design_from_fixture,
)
from tren.manifest import Manifest  # noqa: E402

REPO_FIXTURE = Path(__file__).resolve().parents[1] / "tui" / "fixtures" / "demo-tree.json"


class RoundTripTests(unittest.TestCase):
    def test_fixture_round_trips_losslessly(self) -> None:
        design = design_from_fixture(REPO_FIXTURE)
        again = Design.from_json(design.to_json())
        self.assertEqual(design, again)

    def test_fixture_shape(self) -> None:
        design = design_from_fixture(REPO_FIXTURE)
        self.assertEqual(design.top, "top")
        core = design.scope("core")
        self.assertIsNotNone(core)
        self.assertEqual(core.params[0].value, "32'd32")  # resolved params survive
        self.assertEqual(core.ports[0].direction, "in")
        self.assertTrue(design.scope("sram_stub").blackbox)
        self.assertEqual(len(design.scope("top").instances), 1)
        self.assertTrue(design.scope("core").rtl)  # source snippet carried

    def test_fixture_has_no_connectivity_yet(self) -> None:
        design = design_from_fixture(REPO_FIXTURE)
        self.assertEqual(design.connectivity, ())
        self.assertEqual(design.boundaries, ())

    def test_manual_connectivity_round_trips(self) -> None:
        design = design_from_fixture(REPO_FIXTURE)
        design = Design(
            top=design.top,
            scopes=design.scopes,
            connectivity=(
                NetConnectivity(
                    net="out",
                    bits=(
                        BitLink(
                            position=0,
                            drivers=(Endpoint(cell="u_core", port="alu_result", bit=66),),
                            loads=(),
                        ),
                    ),
                ),
            ),
            boundaries=(
                BoundaryLink(
                    instance="u_core",
                    port="alu_result",
                    parent_scope="top",
                    child_scope=r"$paramod\core\W=...",
                ),
            ),
        )
        self.assertEqual(Design.from_json(design.to_json()), design)


class ManifestTests(unittest.TestCase):
    def test_manifest_round_trips(self) -> None:
        m = Manifest(name="d", sources=("a.v",), defines=(("W", "32"),), top="top")
        self.assertEqual(Manifest.from_json(m.to_json()), m)

    def test_canonical_bytes_order_independent(self) -> None:
        a = Manifest(sources=("b.v", "a.v"))
        b = Manifest(sources=("a.v", "b.v"))
        self.assertEqual(a.canonical_bytes(), b.canonical_bytes())


class CacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tren-cache-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "rtl").mkdir()
        (self.tmp / "rtl" / "demo.sv").write_text("module top; endmodule\n")
        self.manifest = Manifest(name="demo", sources=("rtl/demo.sv",), top="top")
        self.cache = IrCache(self.tmp / "cache", root=self.tmp)

    @staticmethod
    def _design() -> Design:
        return Design(top="top", created_by="cache test")

    def test_hit_then_miss_on_mtime_change(self) -> None:
        path = self.cache.put(self.manifest, self._design())
        self.assertTrue(path.exists())
        self.assertEqual(self.cache.get(self.manifest), self._design())

        # Same content, newer mtime -> key changes -> miss.
        f = self.tmp / "rtl" / "demo.sv"
        st = f.stat()
        os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        self.assertIsNone(self.cache.get(self.manifest))

    def test_manifest_edit_changes_key(self) -> None:
        before = cache_key(self.manifest, self.tmp)
        after = cache_key(
            Manifest(name="demo", sources=("rtl/demo.sv",), top="other"), self.tmp
        )
        self.assertNotEqual(before, after)

    def test_missing_source_key_stable(self) -> None:
        m = Manifest(name="missing", sources=("rtl/nope.sv",))
        self.assertEqual(cache_key(m, self.tmp), cache_key(m, self.tmp))

    def test_corrupt_blob_is_a_miss(self) -> None:
        path = self.cache.path(self.manifest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        self.assertIsNone(self.cache.get(self.manifest))


if __name__ == "__main__":
    unittest.main()