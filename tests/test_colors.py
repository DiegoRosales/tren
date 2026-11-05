"""Unit tests for the theme-derived hierarchy row colours.

The TUI renders these styles, but Pilot asserts state, not colour. The pure
helpers are therefore the seam that keeps the mapping testable.

Run:  nix develop . -c bash -c "PYTHONPATH=src python -m unittest discover -s tests"
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tren.colors import (  # noqa: E402
    NONE,
    THEME,
    THEME_ROLES,
    port_kind,
    readable,
    resolve_row_colors,
    row_style,
    trace_kind,
)

THEME_VARIABLES = {
    "text-primary": "#57a5e2",
    "text-secondary": "#5684a5",
    "text-warning": "#ffc473",
    "text-error": "#d17e92",
    "text-success": "#8ad4a1",
    "foreground-lighten-1": "#f5f5f5",
    "foreground-darken-2": "#b6b6b6",
}


class PortKindTests(unittest.TestCase):
    def test_output_and_input(self) -> None:
        self.assertEqual(port_kind("out"), "port_out")
        self.assertEqual(port_kind("in"), "port_in")

    def test_inout_has_its_own_kind(self) -> None:
        self.assertEqual(port_kind("inout"), "port_inout")

    def test_an_unknown_direction_reads_as_an_input(self) -> None:
        self.assertEqual(port_kind(""), "port_in")


class TraceKindTests(unittest.TestCase):
    """Trace rows reuse the hierarchy kinds by meaning."""

    def test_a_boundary_crosses_into_a_scope(self) -> None:
        self.assertEqual(trace_kind({"kind": "boundary"}), "scope")

    def test_a_box_is_a_black_box(self) -> None:
        self.assertEqual(trace_kind({"kind": "box"}), "blackbox")

    def test_a_cell_is_module_logic(self) -> None:
        self.assertEqual(trace_kind({"kind": "cell"}), "module")

    def test_a_leaf_reads_as_a_net(self) -> None:
        self.assertEqual(trace_kind({"kind": "leaf"}), "net")

    def test_an_unknown_direction_takes_the_warning_kind(self) -> None:
        self.assertEqual(
            trace_kind({"kind": "leaf", "unknown": True}), "blackbox"
        )

    def test_the_traced_net_has_no_row(self) -> None:
        self.assertEqual(trace_kind(None), "net")


class ResolveRowColorTests(unittest.TestCase):
    def test_defaults_follow_the_theme(self) -> None:
        colors = resolve_row_colors(THEME_VARIABLES)
        for kind, role in THEME_ROLES.items():
            with self.subTest(kind=kind):
                self.assertEqual(colors[kind], THEME_VARIABLES[role])
    def test_the_default_theme_keeps_the_kinds_distinct(self) -> None:
        colors = resolve_row_colors(THEME_VARIABLES)
        self.assertEqual(colors["unresolved"], THEME_VARIABLES["text-error"])
        distinct = {
            kind: color for kind, color in colors.items() if kind != "unresolved"
        }
        self.assertEqual(len(set(distinct.values())), len(distinct), colors)

    def test_an_explicit_colour_replaces_the_theme_role(self) -> None:
        colors = resolve_row_colors(
            THEME_VARIABLES, scope="#123456", blackbox="red"
        )
        self.assertEqual(colors["scope"], "#123456")
        self.assertEqual(colors["blackbox"], "red")
        self.assertEqual(colors["net"], THEME_VARIABLES["foreground-darken-2"])

    def test_none_leaves_a_kind_uncoloured(self) -> None:
        colors = resolve_row_colors(THEME_VARIABLES, net=NONE)
        self.assertEqual(colors["net"], "")

    def test_the_port_setting_covers_input_and_output(self) -> None:
        colors = resolve_row_colors(THEME_VARIABLES, port="#abcdef")
        self.assertEqual(colors["port_in"], "#abcdef")
        self.assertEqual(colors["port_out"], "#abcdef")
        self.assertEqual(
            colors["port_inout"], THEME_VARIABLES["text-error"]
        )

    def test_the_inout_setting_is_separate(self) -> None:
        colors = resolve_row_colors(THEME_VARIABLES, inout="#abcdef")
        self.assertEqual(colors["port_inout"], "#abcdef")
        self.assertEqual(colors["port_in"], THEME_VARIABLES["text-secondary"])
        self.assertEqual(colors["port_out"], THEME_VARIABLES["text-success"])

    def test_port_theme_keeps_the_direction_roles(self) -> None:
        colors = resolve_row_colors(THEME_VARIABLES, port=THEME)
        self.assertEqual(colors["port_in"], THEME_VARIABLES["text-secondary"])
        self.assertEqual(
            colors["port_inout"], THEME_VARIABLES["text-error"]
        )
        self.assertEqual(colors["port_out"], THEME_VARIABLES["text-success"])

    def test_a_missing_theme_role_leaves_the_kind_uncoloured(self) -> None:
        colors = resolve_row_colors({})
        self.assertEqual(colors["scope"], "")


class ReadableTests(unittest.TestCase):
    """Theme roles must stay legible on the app's dark pane surfaces."""

    def test_a_bright_role_is_kept(self) -> None:
        self.assertEqual(
            readable("#ffc473", ("#262626", "#121212")), "#FFC473"
        )

    def test_a_dim_role_is_lightened(self) -> None:
        adjusted = readable("#5684a5", ("#262626", "#121212"))
        self.assertNotEqual(adjusted, "#5684A5")

    def test_a_light_theme_role_is_lightened_for_the_dark_surface(self) -> None:
        adjusted = readable("#002d4f", ("#262626", "#121212"))
        self.assertNotEqual(adjusted, "#002D4F")

    def test_no_backgrounds_leaves_the_colour_alone(self) -> None:
        self.assertEqual(readable("#5684a5", ()), "#5684a5")

    def test_an_unparseable_colour_is_unchanged(self) -> None:
        self.assertEqual(
            readable("theme", ("#262626", "#121212")), "theme"
        )

    def test_theme_values_are_adjusted_only_when_backgrounds_are_given(self) -> None:
        raw = resolve_row_colors(THEME_VARIABLES)
        adjusted = resolve_row_colors(
            THEME_VARIABLES, backgrounds=("#262626", "#121212")
        )
        self.assertEqual(raw["scope"], THEME_VARIABLES["text-primary"])
        self.assertNotEqual(adjusted["module"], raw["module"])


class RowStyleTests(unittest.TestCase):
    def test_a_plain_kind_style_is_its_colour(self) -> None:
        colors = {"scope": "#123456"}
        self.assertEqual(row_style("scope", colors), "#123456")

    def test_bold_goes_first(self) -> None:
        colors = {"scope": "#123456"}
        self.assertEqual(row_style("scope", colors, bold=True), "bold #123456")

    def test_an_uncoloured_kind_has_no_style(self) -> None:
        self.assertEqual(row_style("net", {"net": ""}), "")
        self.assertEqual(row_style("unknown", {}), "")

    def test_bold_without_a_colour_still_bolds(self) -> None:
        self.assertEqual(row_style("net", {"net": ""}, bold=True), "bold")
