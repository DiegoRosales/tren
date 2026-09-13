"""Headless driving tests for tren.tui using textual's Pilot.

Run:  nix develop . -c python test_drive.py                  # fixture path
      nix develop . -c python test_drive.py --manifest rtl/demo.manifest.json
                                                             # real IR + file source

Default (card 209): drives the fixture-loaded IR tree + source spans and the
verified keys. With --manifest (card 210): elaborates the real IR, the source
pane shows *real file lines* from the scope's src, the focus band moves with
selection and follows on descend, and `e` records the editor target at the
line. Both modes regenerate out/shot.svg.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from textual.widgets import Static  # noqa: E402

from tren.elaborate import elaborate  # noqa: E402
from tren.manifest import Manifest  # noqa: E402
from tren.tui import TrenApp, load_design  # noqa: E402


async def drive_fixture() -> None:
    """Card 209: fixture path, IR-loaded tree + source spans, verified keys."""
    app = TrenApp(load_design(), root=Path.cwd())
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

        # center must now show core's *instantiation* focused
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
        prev_offset = app.src_offset
        await pilot.press("j")
        assert app.src_offset == prev_offset + 1, "j in source pane should scroll the source"

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


async def drive_real(root: Path, manifest_path: Path) -> None:
    """Card 210: real IR, source pane loads the actual RTL file, auto-follow."""
    design = elaborate(Manifest.read(root / manifest_path), root)
    app = TrenApp(design, root=root)
    async with app.run_test(size=(110, 32)) as pilot:
        source: Static = app.query_one("#source")

        # top/ -> real file lines of the top module declaration
        await pilot.press("j")
        assert "top/" in app._current_node.label.plain, f"cursor: {app._current_node}"
        assert app._focus is not None, "top scope should resolve a file focus"
        assert app._focus[0] == "rtl/demo.sv", f"focus file: {app._focus}"
        assert "module top" in str(source.content), "top module not shown from real file"

        # descend into u_core -> its *instantiation in top* is focused
        await pilot.press("j")
        assert "u_core" in app._current_node.label.plain, f"cursor: {app._current_node}"
        assert app._focus == ("rtl/demo.sv", 77, 77), f"u_core focus: {app._focus}"
        src_text = str(source.content)
        assert "core #(32) u_core(" in src_text, f"no instantiation in source: {src_text[:120]!r}"
        # auto-follow keeps the band in view
        rows = app._source_widget.size.height or 30
        assert app.src_offset + 1 <= 77 <= app.src_offset + rows, f"band out of view: offset {app.src_offset}"

        # descend into u_alu0 (first child, source order) -> band follows to
        # its instantiation line
        await pilot.press("j")
        assert "u_alu0" in app._current_node.label.plain, f"cursor: {app._current_node}"
        assert app._focus == ("rtl/demo.sv", 56, 56), f"u_alu0 focus: {app._focus}"
        assert "alu #(W) u_alu0(" in str(source.content), "u_alu0 instantiation not focused"
        assert app.src_offset + 1 <= 56 <= app.src_offset + rows, f"band out of view: offset {app.src_offset}"

        # e opens the editor (stub) at the focused line
        await pilot.press("e")
        assert app.editor_target == "rtl/demo.sv:56", f"editor target: {app.editor_target}"

        # h walks back up to u_core
        await pilot.press("h")
        assert "u_core" in app._current_node.label.plain, f"after h: {app._current_node}"
        assert app._focus == ("rtl/demo.sv", 77, 77), "focus should return to u_core"

        out = Path(__file__).resolve().parent / "out"
        out.mkdir(exist_ok=True)
        (out / "shot-real.svg").write_text(app.export_screenshot())


async def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="elaborate this manifest (real-file source pane) instead of the fixture",
    )
    args = parser.parse_args(argv)
    root = Path.cwd()
    if args.manifest is not None:
        await drive_real(root, args.manifest)
    else:
        await drive_fixture()
    print("PASS")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
    print("ok")
