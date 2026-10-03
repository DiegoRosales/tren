"""Unit tests for the pure search module (deck card 244).

The fuzzy ranker and the index builder live outside the TUI so a ratatui
rewrite keeps them. They are the layer-1 net: the drives assert the modal
behaviour on top, but the ranking properties are pinned here where a
failure names the function and the string, not a key press queue.

Run:  nix develop . -c bash -c "PYTHONPATH=src python -m unittest discover -s tests"
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tren.ir import design_from_fixture  # noqa: E402
from tren.search import (  # noqa: E402
    SearchIndex,
    build_search_index,
    filter_and_rank,
    fuzzy_score,
)

FIXTURE = Path(__file__).resolve().parents[1] / "tui" / "fixtures" / "demo-tree.json"


def fixture_index():
    return build_search_index(design_from_fixture(FIXTURE))


class FuzzyScoreTests(unittest.TestCase):
    """Subsequence matching with a contiguous-run preference (fzf-like)."""

    def test_full_substring_matches(self) -> None:
        self.assertIsNotNone(fuzzy_score("alu_result", "top.u_core.alu_result"))

    def test_non_consecutive_characters_match(self) -> None:
        self.assertIsNotNone(fuzzy_score("alr", "alu_result"))

    def test_case_insensitive(self) -> None:
        self.assertEqual(
            fuzzy_score("ALU", "alu_result"), fuzzy_score("alu", "alu_result")
        )

    def test_non_match_is_none(self) -> None:
        self.assertIsNone(fuzzy_score("xyz", "alu"))

    def test_empty_query_matches_everything(self) -> None:
        self.assertEqual(fuzzy_score("", "anything"), 0)

    def test_contiguous_run_scores_higher_than_a_spread(self) -> None:
        self.assertGreater(
            fuzzy_score("core", "u_core"), fuzzy_score("core", "u c o r e")
        )

    def test_boundary_match_scores_higher(self) -> None:
        # with contiguity equal, a boundary match ('d' after '_') wins
        self.assertGreater(
            fuzzy_score("d0", "u_d0"), fuzzy_score("d0", "ud0")
        )

    def test_left_to_right_order_kept(self) -> None:
        # every letter must appear in order, whatever the gaps
        self.assertIsNone(fuzzy_score("ecor", "u_core"))

    def test_repeated_query_chars_need_distinct_positions(self) -> None:
        # the scorer must not let the same text character satisfy two equal
        # query characters (regression: the grouped-position walk once did)
        self.assertIsNone(fuzzy_score("..", "n.7z"))
        self.assertIsNotNone(fuzzy_score("cc", "c.c"))
        self.assertIsNotNone(fuzzy_score("cc", "cc."))

    def test_first_character_keeps_the_original_bonus(self) -> None:
        # the very first matched character scores 5, not 3 (the original
        # DP's ended[0]=0 initialisation leaks into j == 0)
        self.assertEqual(fuzzy_score("x", "x"), 5)
        self.assertEqual(fuzzy_score("x", "xx"), 5)


class BuildIndexTests(unittest.TestCase):
    """Every searchable item of the demo fixture ends up in the index."""

    def test_kinds_and_counts(self) -> None:
        entries = fixture_index()
        by_kind = {
            kind: sorted(e.path for e in entries if e.kind == kind)
            for kind in ("module", "instance", "port", "net", "file")
        }
        self.assertIsInstance(entries, SearchIndex)
        self.assertEqual(len(entries.folded), len(entries))
        self.assertEqual(by_kind["module"], ["alu", "core", "sram_stub", "top"])
        self.assertEqual(
            by_kind["instance"],
            [
                "core.g[0].u_alu_g",
                "core.g[1].u_alu_g",
                "core.u_alu0",
                "core.u_sram",
                "top.u_core",
            ],
        )
        self.assertIn("core.alu_result", by_kind["port"])
        self.assertIn("core.alu_result", by_kind["net"])
        self.assertEqual(by_kind["file"], ["rtl/demo.sv"])

    def test_entries_carry_their_scope_and_src(self) -> None:
        entries = fixture_index()
        net = next(e for e in entries if e.kind == "net" and e.path == "core.alu_result")
        self.assertEqual(net.scope.name, "core")
        self.assertEqual(net.src, "rtl/demo.sv:48")

    def test_file_entry_keeps_a_representative_src(self) -> None:
        entries = fixture_index()
        file = next(e for e in entries if e.kind == "file")
        # the first src naming the file in build order is the u_core
        # instance (top's own scope carries no src in the fixture)
        self.assertEqual(file.src, "rtl/demo.sv:77")


class RankTests(unittest.TestCase):
    """filter_and_rank returns answers for the finder's promises."""

    def test_substring_of_a_net_name_finds_it_across_scopes(self) -> None:
        rows = filter_and_rank(fixture_index(), "alu_result")
        self.assertEqual(
            [e.kind for e in rows if e.path == "core.alu_result"], ["port", "net"]
        )

    def test_one_result_per_matching_entity(self) -> None:
        rows = filter_and_rank(fixture_index(), "u_alu_g")
        paths = [e.path for e in rows]
        self.assertEqual(
            [p for p in paths if p.endswith("u_alu_g")],
            ["core.g[0].u_alu_g", "core.g[1].u_alu_g"],
        )

    def test_empty_query_lists_everything_grouped_by_kind(self) -> None:
        rows = filter_and_rank(fixture_index(), "")
        self.assertEqual(len(rows), 28)
        self.assertEqual(
            [r.kind for r in rows],
            ["module"] * 4
            + ["instance"] * 5
            + ["port"] * 14
            + ["net"] * 4
            + ["file"] * 1,
        )

    def test_query_narrows_and_keeps_the_finder_answer(self) -> None:
        rows = filter_and_rank(fixture_index(), "result")
        paths = [e.path for e in rows]
        self.assertIn("alu.result", paths)
        self.assertIn("core.alu_result", paths)
        self.assertNotIn("top", paths)

    def test_plain_list_ranks_identically(self) -> None:
        # a caller that drops the SearchIndex wrapper (fresh casefold) must
        # get the same rows in the same order
        index = fixture_index()
        for q in ("alu", "result", "."):
            self.assertEqual(
                [e.path for e in filter_and_rank(index, q)],
                [e.path for e in filter_and_rank(list(index), q)],
            )


if __name__ == "__main__":
    unittest.main()