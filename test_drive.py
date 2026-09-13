"""Headless driving test for tren.tui using textual's Pilot (card 209).

Run:  nix develop . -c python test_drive.py

Drives the IR-loaded tree + source spans (fixture path first) and asserts on
widget state (Tree nodes, source pane highlight, ports pane, yank, pane
selection). Selecting a tree node auto-expands it (textual 8.2 behavior);
`l` toggles. Regenerates out/shot.svg as the visual artifact.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from textual.widgets import Static  # noqa: E402

from tren.tui import TrenApp, load_design  # noqa: E402


async def main() -> None:
    app = TrenApp(load_design())
    async with app.run_test(size=(110, 32)) as pilot:
        tree = app._tree()
        source: Static = app.query_one("#source")
        ports: Static = app.query_one("#ports")

        # j -> top/ scope; center shows top's own text
        await pilot.press("j")
        assert "top/" in app._current_node.label.plain, f"cursor: {app._current_node}"
        source_text = str(source.content)
        assert "module top" in source_text, "source pane did not focus top"

        # selecting top auto-expanded it -> j descends into u_core instance
        await pilot.press("j")
        assert "u_core" in app._current_node.label.plain, f"cursor: {app._current_node}"

        # center must now show core's *instantiation* focused (rtl_focus span)
        source_text = str(source.content)
        assert "core #(32) u_core(" in source_text, f"source did not focus core: {source_text[:80]!r}"
        assert ".alu_result(out)" in source_text, "core instantiation incomplete"

        # right pane must show core's I/O
        ports_text = str(ports.content)
        for expected in ("a", "alu_result"):
            assert expected in ports_text, f"ports pane missing {expected}: {ports_text!r}"

        # l toggles: collapse then re-expand u_core
        await pilot.press("l")
        assert not app._current_node.is_expanded, "l should collapse u_core"
        await pilot.press("l")
        assert app._current_node.is_expanded, "l should re-expand u_core"

        # j -> first port leaf under u_core; panes stay on core
        await pilot.press("j")
        assert app._current_node.label.plain.startswith("port"), f"cursor: {app._current_node}"
        assert "core #(32) u_core(" in str(source.content), "pane unlinked from selection"

        # yank recorded the selection
        await pilot.press("y")
        assert app.yanked, "yank recorded nothing"
        print("yanked:", app.yanked[-1])

        # 'h' walks up a level to u_core
        await pilot.press("h")
        assert "u_core" in app._current_node.label.plain, f"after h: {app._current_node}"

        # pane selection: 2 routes j to source scroll, 3 to ports selection
        await pilot.press("2")
        assert app.active == "source", "pressing 2 should activate the source pane"
        await pilot.press("j")
        assert app.src_offset == 1, "j in source pane should scroll the source"
        assert not str(source.content).startswith("core #(32) u_core("), "source did not scroll"

        await pilot.press("3")
        assert app.active == "ports", "pressing 3 should activate the ports pane"
        await pilot.press("j")
        assert app.port_index == 1, "j in ports pane should move the I/O selection"
        await pilot.press("y")
        assert "b" in app.yanked[-1], f"yank in ports pane should name the selected port: {app.yanked[-1]}"

        await pilot.press("1")
        assert app.active == "tree", "pressing 1 should reactivate the tree pane"

        out = Path(__file__).resolve().parent / "out"
        out.mkdir(exist_ok=True)
        (out / "shot.svg").write_text(app.export_screenshot())

    print("PASS")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
    print("ok")
