"""Tests for the IR model, manifest and on-disk cache.

Run:  nix develop . -c bash -c "PYTHONPATH=src python -m unittest discover -s tests"
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tren import ir  # noqa: E402
from tren.cache import IrCache, cache_key, inputs_key  # noqa: E402
from tren.ir import (  # noqa: E402
    BitLink,
    BoundaryLink,
    Design,
    Endpoint,
    NetConnectivity,
    Param,
    design_from_fixture,
    literal_int,
    schema_id,
)
from tren.manifest import Manifest  # noqa: E402

REPO_FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "demo-tree.json"


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


class LiteralIntTests(unittest.TestCase):
    """The two layers spell one number differently."""

    def test_plain_decimal(self) -> None:
        self.assertEqual(literal_int("8"), 8)
        self.assertEqual(literal_int(" 11 "), 11)
        self.assertEqual(literal_int("-1"), -1)

    def test_sized_literals(self) -> None:
        self.assertEqual(literal_int("32'd32"), 32)
        self.assertEqual(literal_int("3'b000"), 0)
        self.assertEqual(literal_int("4'b11"), 3)
        self.assertEqual(literal_int("32'hff"), 255)
        self.assertEqual(literal_int("8'o17"), 15)
        self.assertEqual(literal_int("16'sd7"), 7)
        self.assertEqual(literal_int("8'b1010_1010"), 170)

    def test_not_a_literal(self) -> None:
        self.assertIsNone(literal_int(None))
        self.assertIsNone(literal_int(""))
        self.assertIsNone(literal_int("(32 / 8)"))
        self.assertIsNone(literal_int("2*W"))
        self.assertIsNone(literal_int("4'bxx"))  # unknown bits have no value
        self.assertIsNone(literal_int("2'b12"))  # a digit the base has not
        self.assertIsNone(literal_int("hello"))


class ParamOverrideTests(unittest.TestCase):
    """The override marker states a fact, so it compares numbers.

    slang-hier resolves "3'b000" to "3'b0" and "2'd1" to "2'b1". A text
    compare reads those as an override of a parameter nobody overrides.
    """

    def test_same_number_spelled_differently_is_not_an_override(self) -> None:
        self.assertFalse(Param(name="ST", value="3'b0", default="3'b000").overridden)
        self.assertFalse(Param(name="S1", value="2'b1", default="2'd1").overridden)
        self.assertFalse(Param(name="N", value="32'd32", default="32").overridden)

    def test_a_real_override_still_marks(self) -> None:
        self.assertTrue(Param(name="W", value="32'd32", default="8").overridden)
        self.assertTrue(Param(name="M", value="4'b11", default="4'b10").overridden)

    def test_localparam_is_never_overridden(self) -> None:
        p = Param(name="OP", value="4'b11", default="3", kind="localparam")
        self.assertFalse(p.overridden)

    def test_no_declared_default_claims_nothing(self) -> None:
        self.assertFalse(Param(name="W", value="32'd32").overridden)

    def test_expression_default_claims_nothing(self) -> None:
        # '(32 / 8)' against a resolved '4' says nothing either way, and an
        # override is a positive claim.
        self.assertFalse(Param(name="B", value="4", default="(32 / 8)").overridden)
        self.assertFalse(Param(name="B", value="8", default="(32 / 8)").overridden)

    def test_two_non_literals_compare_as_text(self) -> None:
        self.assertTrue(Param(name="S", value='"b"', default='"a"').overridden)
        self.assertFalse(Param(name="S", value='"a"', default='"a"').overridden)


class ManifestTests(unittest.TestCase):
    def test_manifest_round_trips(self) -> None:
        m = Manifest(name="d", sources=("a.v",), defines=(("W", "32"),), top="top")
        self.assertEqual(Manifest.from_json(m.to_json()), m)

    def test_canonical_bytes_keeps_source_order(self) -> None:
        a = Manifest(sources=("b.v", "a.v"))
        b = Manifest(sources=("a.v", "b.v"))
        self.assertNotEqual(a.canonical_bytes(), b.canonical_bytes())


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

    def test_source_order_changes_key(self) -> None:
        (self.tmp / "rtl" / "a.sv").write_text("")
        (self.tmp / "rtl" / "b.sv").write_text("")
        a = Manifest(name="demo", sources=("rtl/b.sv", "rtl/a.sv"))
        b = Manifest(name="demo", sources=("rtl/a.sv", "rtl/b.sv"))
        self.assertNotEqual(cache_key(a, self.tmp), cache_key(b, self.tmp))

    def test_missing_source_key_stable(self) -> None:
        m = Manifest(name="missing", sources=("rtl/nope.sv",))
        self.assertEqual(cache_key(m, self.tmp), cache_key(m, self.tmp))

    def test_corrupt_blob_is_a_miss(self) -> None:
        path = self.cache.path(self.manifest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        self.assertIsNone(self.cache.get(self.manifest))


# Stand-ins for "the IR before and after a field lands", one nested level deep
# so the fingerprint has to walk the graph to tell them apart. Module-level
# because get_type_hints resolves annotations against module globals.
@dataclass(frozen=True)
class _Leaf:
    name: str


@dataclass(frozen=True)
class _LeafPlus:
    name: str
    type: str | None = None  # the shape of the Port.type change


@dataclass(frozen=True)
class _Before:
    leaves: tuple[_Leaf, ...] = ()


@dataclass(frozen=True)
class _After:
    leaves: tuple[_LeafPlus, ...] = ()


class SchemaInvalidationTests(unittest.TestCase):
    """A blob is only reusable by the code that wrote it.

    `Port.type` reached the IR while caches in the wild kept answering
    `type=None`, because the key knew the inputs but not the IR's own shape.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tren-schema-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "rtl").mkdir()
        (self.tmp / "rtl" / "demo.sv").write_text("module top; endmodule\n")
        self.manifest = Manifest(name="demo", sources=("rtl/demo.sv",), top="top")
        self.cache = IrCache(self.tmp / "cache", root=self.tmp)

    # ---- the fingerprint itself -----------------------------------------

    def test_a_new_field_changes_the_fingerprint(self) -> None:
        self.assertNotEqual(ir._fingerprint(_Before), ir._fingerprint(_After))

    def test_fingerprint_is_stable_and_reaches_nested_dataclasses(self) -> None:
        self.assertEqual(schema_id(), schema_id())
        # Port is only reachable through Design -> Scope -> Port; a change
        # that deep must still be seen.
        parts_seen = []
        real = ir.fields

        def spy(cls):  # noqa: ANN001 - test double
            parts_seen.append(cls.__name__)
            return real(cls)

        with mock.patch.object(ir, "fields", spy):
            schema_id()
        self.assertIn("Port", parts_seen)

    def test_manual_version_bump_invalidates(self) -> None:
        """The escape hatch for changes the field layout cannot see."""
        before = schema_id()
        with mock.patch.object(ir, "SCHEMA_VERSION", ir.SCHEMA_VERSION + 1):
            self.assertNotEqual(before, schema_id())

    # ---- what that buys the cache ---------------------------------------

    def test_blob_from_another_schema_is_a_miss(self) -> None:
        self.cache.put(self.manifest, Design(top="top", created_by="old tren"))
        self.assertIsNotNone(self.cache.get(self.manifest))
        with mock.patch.object(ir, "SCHEMA_VERSION", ir.SCHEMA_VERSION + 1):
            self.assertIsNone(
                self.cache.get(self.manifest),
                "a blob written by another IR must not be served",
            )

    def test_legacy_unsuffixed_blob_is_a_miss(self) -> None:
        """Blobs predating the schema suffix are named `<inputs>.json`."""
        legacy = self.cache.directory / f"{inputs_key(self.manifest, self.tmp)}.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        Design(top="top", created_by="pre-schema tren").write(legacy)
        self.assertIsNone(self.cache.get(self.manifest))

    def test_put_sweeps_superseded_blobs(self) -> None:
        legacy = self.cache.directory / f"{inputs_key(self.manifest, self.tmp)}.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        Design(top="top", created_by="pre-schema tren").write(legacy)

        current = self.cache.put(self.manifest, Design(top="top", created_by="now"))
        self.assertTrue(current.exists())
        self.assertFalse(legacy.exists(), "dead blobs must not accumulate per schema")

    def test_sweep_leaves_other_manifests_alone(self) -> None:
        (self.tmp / "rtl" / "other.sv").write_text("module other; endmodule\n")
        other = Manifest(name="other", sources=("rtl/other.sv",), top="other")
        kept = self.cache.put(other, Design(top="other"))

        self.cache.put(self.manifest, Design(top="top"))
        self.assertTrue(kept.exists(), "a sweep must not evict unrelated manifests")

    def test_key_splits_into_inputs_and_schema(self) -> None:
        key = cache_key(self.manifest, self.tmp)
        self.assertEqual(key, f"{inputs_key(self.manifest, self.tmp)}-{schema_id()}")


if __name__ == "__main__":
    unittest.main()