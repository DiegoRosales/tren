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
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from textual.color import Color  # noqa: E402
from textual.widgets import Static  # noqa: E402

from tren.elaborate import elaborate  # noqa: E402
from tren.manifest import Manifest  # noqa: E402
from tren.tui import PANE_BG_ACTIVE, PANE_BG_IDLE, TrenApp, load_design  # noqa: E402


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

        # j/k do not unfold: only l expands (file-manager model)
        assert not app._current_node.is_expanded, "top must stay collapsed after j"

        await pilot.press("l")
        assert app._current_node.is_expanded, "l should expand top"
        await pilot.press("j")  # -> u_core instance under top
        assert "u_core" in app._current_node.label.plain, f"cursor: {app._current_node}"

        # center must now show core's *instantiation* focused
        source_text = str(source.content)
        assert "core #(32) u_core(" in source_text, f"source did not focus core: {source_text[:80]!r}"
        assert ".alu_result(out)" in source_text, "core instantiation incomplete"

        # right pane must show core's I/O
        ports_text = str(ports.content)
        for expected in ("a", "alu_result"):
            assert expected in ports_text, f"ports pane missing {expected}: {ports_text!r}"

        # l/h: expand then collapse u_core (u_core is not expanded yet)
        assert not app._current_node.is_expanded, "u_core should start collapsed"
        await pilot.press("l")
        assert app._current_node.is_expanded, "l should expand u_core"
        await pilot.press("h")
        assert not app._current_node.is_expanded, "h should collapse u_core"
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

        # ctrl+u from a port promotes to the current hierarchy's module node
        await pilot.press("ctrl+u")
        assert "core/" in app._current_node.label.plain, \
            f"fixture ctrl+u should select the module: {app._current_node}"

        # enter shows the highlighted module in the centre (cursor: core scope)
        await pilot.press("enter")
        assert "core" in str(source.content).lower(), "enter did not show core RTL"

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

        # card 245: the active-pane cue must be symmetric — exactly one pane
        # lifted to PANE_BG_ACTIVE, the other two at PANE_BG_IDLE. Pilot can
        # only assert the style value; that the *tree* actually repaints is a
        # real-terminal check (tools/pane_cue_capture.sh), because textual's
        # Tree caches rendered lines and used to keep the old background.
        panes = {"1": "tree", "2": "source", "3": "ports"}
        widgets = {"tree": tree, "source": source, "ports": ports}
        for key, expected_active in panes.items():
            await pilot.press(key)
            for name, widget in widgets.items():
                want = PANE_BG_ACTIVE if name == expected_active else PANE_BG_IDLE
                got = widget.styles.background
                assert got == Color.parse(want), (
                    f"pane {key} active: {name} background is {got}, expected {want}"
                )
        # the tree must be blurred: focused, it picks up textual's
        # `Tree:focus { background-tint: ... }` and reads as highlighted even
        # when idle (only visible on a real terminal — headless never focuses it)
        assert not tree.has_focus, "the tree must not hold focus"
        await pilot.press("1")

        # cards 242/243, the no-src fallback: fixture ports carry no src, so a
        # port leaf resolves no declaration of its own and the enclosing
        # scope's inline RTL keeps the pane (no error, no jump).
        scrolled = app.src_offset  # the source pane was scrolled by `j` above
        select_leaf(app, "core", "port", "a")
        assert app._focus is None, f"a src-less port must not resolve a focus: {app._focus}"
        text = str(source.content)
        assert "cannot read" not in text, f"src-less port hit the error path: {text[:120]!r}"
        assert ".alu_result(out)" in text, f"inline snippet not shown: {text[:120]!r}"
        assert app.src_offset == scrolled, "a src-less leaf must not move the view"

        # ...but moving to a *different* src-less scope starts at the top: the
        # scroll belongs to the snippet, not to the pane.
        app._select_path("alu")
        assert app._focus is None, f"fixture scopes carry no file src: {app._focus}"
        assert app.src_offset == 0, f"new snippet must start at the top: {app.src_offset}"

        out = Path(__file__).resolve().parent / "out"
        out.mkdir(exist_ok=True)
        (out / "shot.svg").write_text(app.export_screenshot())


def select_leaf(app: TrenApp, scope_path: str, kind: str, name: str) -> None:
    """Select a port/net leaf under a scope.

    The app's own `_select_path` resolves nets only (it exists for the trace
    modal's boundary crossing), so the port half of card 242 needs this.
    """
    scope_node = app._find_scope_node(scope_path)
    assert scope_node is not None, f"no scope node for {scope_path}"
    app._expand_to(scope_node)
    app._ensure_children(scope_node)
    for child in scope_node.children:
        data = child.data
        if data is not None and data.get("kind") == kind and data[kind].name == name:
            app._expand_to(child)
            app._select(app._visible().index(child))
            return
    raise AssertionError(f"no {kind} leaf {name!r} under {scope_path}")


async def drive_unreadable_src() -> None:
    """Cards 242/243: a leaf src naming a file that is not on disk.

    Fixture nets carry `src: rtl/demo.sv:NN`, which only resolves when the
    repo is the working root. Rooted elsewhere the file is unreadable — the
    source pane must still show the scope's inline RTL, not '(cannot read)'.
    """
    with tempfile.TemporaryDirectory() as tmp:
        app = TrenApp(load_design(), root=Path(tmp))
        async with app.run_test(size=(110, 32)) as pilot:
            source: Static = app.query_one("#source")
            select_leaf(app, "core", "net", "x")
            await pilot.pause()
            assert app._focus == ("rtl/demo.sv", 50, 50), f"net x focus: {app._focus}"
            text = str(source.content)
            assert "cannot read" not in text, f"no inline fallback: {text[:120]!r}"
            assert "core #(32) u_core(" in text, f"inline snippet not shown: {text[:120]!r}"
            assert app.src_offset == 0, f"inline view must start at the top: {app.src_offset}"


async def drive_real(root: Path, manifest_path: Path) -> None:
    """Card 210: real IR, source pane loads the actual RTL file, auto-follow."""
    design = elaborate(Manifest.read(root / manifest_path), root)
    app = TrenApp(design, root=root)
    async with app.run_test(size=(110, 32)) as pilot:
        source: Static = app.query_one("#source")
        ports: Static = app.query_one("#ports")

        # top/ -> real file lines of the top module declaration
        await pilot.press("j")
        assert "top/" in app._current_node.label.plain, f"cursor: {app._current_node}"
        assert app._focus is not None, "top scope should resolve a file focus"
        assert app._focus[0] == "rtl/demo.sv", f"focus file: {app._focus}"
        assert "module top" in str(source.content), "top module not shown from real file"

        # j does not unfold; l does (file-manager model)
        await pilot.press("j")
        assert "top/" in app._current_node.label.plain, "j must not auto-expand"

        await pilot.press("l")
        assert app._current_node.is_expanded, "l should expand top"

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
        await pilot.press("l")
        assert app._current_node.is_expanded, "l should expand u_core"
        await pilot.press("j")
        assert "u_alu0" in app._current_node.label.plain, f"cursor: {app._current_node}"
        assert app._focus == ("rtl/demo.sv", 56, 56), f"u_alu0 focus: {app._focus}"
        assert "alu #(W) u_alu0(" in str(source.content), "u_alu0 instantiation not focused"
        assert app.src_offset + 1 <= 56 <= app.src_offset + rows, f"band out of view: offset {app.src_offset}"

        # e opens the editor (stub) at the focused line
        await pilot.press("e")
        assert app.editor_target == "rtl/demo.sv:56", f"editor target: {app.editor_target}"

        # ctrl+u walks back up to core (the enclosing scope of u_alu0)
        await pilot.press("ctrl+u")
        assert "u_core" in app._current_node.label.plain, f"after ctrl+u: {app._current_node}"
        assert app._focus == ("rtl/demo.sv", 77, 77), "focus should return to u_core"

        # enter shows the highlighted module in the centre with context
        await pilot.press("enter")
        rows = app._source_widget.size.height or 30
        assert app.src_offset <= 77 <= app.src_offset + rows, f"band out of view: offset {app.src_offset}"

        # ---- cards 242/243: leaves focus their own DECLARATION -------------
        # The two backends spell the span differently and both land here:
        # Port.src is slang's file:line:col, Net.src the yosys file:l.c-l.c.
        select_leaf(app, "top.u_core.u_alu0", "port", "op")
        assert app._focus == ("rtl/demo.sv", 24, 24), f"port op focus: {app._focus}"
        assert "input  logic [3:0]   op," in str(source.content), "op declaration not shown"
        rows = app._source_widget.size.height or 30
        assert app.src_offset + 1 <= 24 <= app.src_offset + rows, (
            f"port declaration out of view: offset {app.src_offset}"
        )

        select_leaf(app, "top.u_core", "net", "x")
        assert app._focus == ("rtl/demo.sv", 50, 50), f"net x focus: {app._focus}"
        assert "logic [W-1:0] x, y, rd, r0;" in str(source.content), "net declaration not shown"
        assert app.src_offset + 1 <= 50 <= app.src_offset + rows, (
            f"net declaration out of view: offset {app.src_offset}"
        )

        # generate scopes carry a slang src too — same spelling, same trap
        generate = app._find_scope_node("top.u_core.g[0]")
        assert generate is not None, "no generate scope node"
        app._expand_to(generate)
        app._select(app._visible().index(generate))
        assert app._focus == ("rtl/demo.sv", 58, 58), f"g[0] focus: {app._focus}"

        # an output port resolves the same way (the direction plays no part)
        select_leaf(app, "top.u_core.u_alu0", "port", "result")
        assert app._focus == ("rtl/demo.sv", 25, 25), f"port result focus: {app._focus}"
        # the no-src fallback is exercised in drive_fixture, where ports
        # genuinely carry no src (every port in the real IR has one)

        # ---- card 213: d/L trace with hierarchy crossing ------------------
        # walk from top.out to its $xor driver and back; dump shows the path
        app._select_path("top", "out")
        await pilot.press("d")
        modal = app._modal
        assert modal is not None, "d should open the trace modal"
        assert "drivers of top.out" in str(modal.query_one("#trace-title").content)
        assert app._trace == [("top", "out")], f"trace: {app._trace}"
        assert [r["label"] for r in modal.rows] == ["u_core.alu_result  → core"]

        await pilot.press("enter")  # cross the boundary into u_core.alu_result
        assert app._trace == [("top", "out"), ("top.u_core", "alu_result")], f"trace: {app._trace}"
        labels = [r["label"] for r in modal.rows]
        assert any("$xor" in l for l in labels), f"no $xor driver: {labels}"

        await pilot.press(";")  # dump the trace path
        assert app.trace_dump == "top.out ▶ top.u_core.alu_result", app.trace_dump

        await pilot.press("backspace")  # pop back to top.out
        assert app._trace == [("top", "out")], f"trace after pop: {app._trace}"
        await pilot.press("escape")
        assert app._modal is None, "escape should close the trace modal"

        # ---- card 212: black boxes + user cuts ----------------------------
        # sram_stub renders as an opaque scope: marker + its ports
        app._select_path("top.u_core.u_sram")
        ports_text = str(ports.content)
        assert "BLACK BOX" in ports_text, f"no black box marker: {ports_text!r}"
        assert "rdata" in ports_text, "black box ports missing"

        # X toggles a user cut on core (opaque and back)
        app._select_path("top.u_core")
        # card 219: I/O pane shows the slang overlay — resolved value,
        # declared default, override marker and the source-faithful type
        ports_text = str(ports.content)
        assert "W = 32'd32" in ports_text, f"params missing: {ports_text!r}"
        assert "[default 8]" in ports_text, f"default missing: {ports_text!r}"
        assert "✎" in ports_text, f"override marker missing: {ports_text!r}"
        assert "logic [31:0]" in ports_text, f"port type missing: {ports_text!r}"
        await pilot.press("X")
        assert "top.u_core" in app._user_cut, "X should cut core"
        assert "USER CUT" in str(ports.content), "cut marker missing"
        await pilot.press("X")
        assert "top.u_core" not in app._user_cut, "X should un-cut core"

        # trace stops at a black box with a reason: loads of core.x -/>
        # u_sram.addr is a box, enter must not descend
        app._select_path("top.u_core", "x")
        await pilot.press("L")
        modal = app._modal
        assert modal is not None and app._trace_dir == "loads"
        sram_idx = next(i for i, r in enumerate(modal.rows) if "u_sram" in r["label"])
        assert modal.rows[sram_idx]["kind"] == "box", modal.rows[sram_idx]
        assert "black box" in modal.rows[sram_idx]["label"]
        for _ in range(sram_idx):
            await pilot.press("down")  # arrow keys reach the modal list
        assert modal.listwidget.index == sram_idx, f"index {modal.listwidget.index}"
        n_before = len(app._trace)
        await pilot.press("enter")
        assert len(app._trace) == n_before, "trace must stop at a black box"
        await pilot.press("escape")

        # ---- card 214: yank formats + OSC 52 + editor jump ----------------
        # current selection after the box-stop section: core's `x` net
        parts = app._selection_parts()
        assert parts == (["top", "u_core"], "x"), f"parts: {parts}"

        # y yanks through the default (sv) format via OSC 52
        await pilot.press("y")
        assert app.yanked[-1] == "top.u_core.x", app.yanked[-1]
        assert app.osc_events[-1] == "top.u_core.x", app.osc_events[-1]

        # Y cycles the format registry (sv -> vivado); top excluded, Tcl-safe
        await pilot.press("Y")
        assert app.yank_format.name == "vivado", app.yank_format.name
        await pilot.press("y")
        assert app.yanked[-1] == "u_core/x", app.yanked[-1]

        # array segments re-render through the format (g[0] -> Tcl-escaped)
        app._select_path("top.u_core.g[0].u_alu_g", "result")
        await pilot.press("y")
        assert app.yanked[-1] == r"u_core/g\[0\]/u_alu_g/result", app.yanked[-1]

        # cycle back (vivado -> yosys -> vcd -> sv) and yank a port from the
        # ports pane: chain + port name
        for _ in range(3):
            await pilot.press("Y")
        assert app.yank_format.name == "sv"
        await pilot.press("3")
        await pilot.press("y")
        assert app.yanked[-1] == "top.u_core.g[0].u_alu_g.a", app.yanked[-1]
        await pilot.press("1")

        # ---- card 239: ctrl+u jumps to the next upper level ----------------
        # from a deep generate-block scope, one ctrl+u lands on its parent
        app._select_path("top.u_core.g[0].u_alu_g")
        await pilot.press("ctrl+u")
        assert "u_core" in app._current_node.label.plain, f"after ctrl+u: {app._current_node}"

        # from a port/net leaf, ctrl+u lands on ITS scope (current hierarchy)
        app._select_path("top.u_core", "x")
        await pilot.press("ctrl+u")
        assert "u_core" in app._current_node.label.plain, f"leaf ctrl+u: {app._current_node}"

        # pressing it again (now on the u_core module row) steps up to top
        await pilot.press("ctrl+u")
        assert "top/" in app._current_node.label.plain, f"second ctrl+u: {app._current_node}"

        # from a top-level scope it stays put (no wrap)
        await pilot.press("ctrl+u")
        assert app._current_node.data is not None
        assert app._current_node.data.get("scope", {}).name == "top", \
            f"top-level ctrl+u should not wrap: {app._current_node.data}"

        # ---- card 238: enter shows the module; the d/L modal keeps Enter ----
        app._select_path("top", "out")
        await pilot.press("d")
        modal = app._modal
        assert modal is not None
        await pilot.press("enter")  # must cross the boundary, not trigger focus_source
        assert app._trace == [("top", "out"), ("top.u_core", "alu_result")], f"trace: {app._trace}"
        await pilot.press("escape")

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
        await drive_unreadable_src()
    print("PASS")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
    print("ok")
