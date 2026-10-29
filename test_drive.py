"""Headless driving tests for tren.tui using textual's Pilot.

Run:  nix develop . -c python test_drive.py                  # fixture path
      nix develop . -c python test_drive.py --manifest rtl/demo.manifest.json
                                                             # real IR + file source

Default: drives the fixture-loaded IR tree + source spans and the
verified keys. With --manifest: elaborates the real IR, the source
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
from textual.widgets import Input, Static  # noqa: E402

from tren.elaborate import elaborate  # noqa: E402
from tren.manifest import Manifest  # noqa: E402
from tren.tui import (  # noqa: E402
    PANE_BG_ACTIVE,
    PANE_BG_IDLE,
    PANE_WIDTHS,
    SearchScreen,
    TrenApp,
    load_design,
)


async def drive_fixture() -> None:
    """Fixture path, IR-loaded tree + source spans, verified keys."""
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
        assert status_path(app) == "top", f"status after j: {status_path(app)!r}"

        # j/k do not unfold: only l expands (file-manager model)
        assert not app._current_node.is_expanded, "top must stay collapsed after j"

        await pilot.press("l")
        assert app._current_node.is_expanded, "l should expand top"
        await pilot.press("j")  # -> u_core instance under top
        assert "u_core" in app._current_node.label.plain, f"cursor: {app._current_node}"
        assert status_path(app) == "top.u_core", f"status after l,j: {status_path(app)!r}"

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
        assert status_path(app) == "top.u_core", f"status after l: {status_path(app)!r}"
        await pilot.press("h")
        assert not app._current_node.is_expanded, "h should collapse u_core"
        assert status_path(app) == "top.u_core", f"status after h: {status_path(app)!r}"
        await pilot.press("l")
        assert app._current_node.is_expanded, "l should re-expand u_core"

        # the arrow keys are aliases of j/k/l/h in the tree
        await pilot.press("left")
        assert not app._current_node.is_expanded, "left should collapse u_core"
        await pilot.press("right")
        assert app._current_node.is_expanded, "right should expand u_core"
        await pilot.press("down")
        assert app._current_node.label.plain.startswith("port"), \
            f"down should step to the next visible row: {app._current_node}"
        await pilot.press("up")
        assert "u_core" in app._current_node.label.plain, \
            f"up should step back: {app._current_node}"

        # pageup/pagedown move a screen in the active pane.
        # in the tree, pagedown jumps `_page_rows` visible nodes (here the
        # whole tree fits one screen, so one page clamps at the last row)
        tree_rows = app._page_rows()
        assert tree_rows >= 1, f"page rows: {tree_rows}"
        before = app._index
        await pilot.press("pagedown")
        jumped = app._index
        assert jumped == min(before + tree_rows, len(app._visible()) - 1), \
            f"pagedown in tree: index {jumped}, page {tree_rows}"
        await pilot.press("pageup")
        assert app._index == max(0, jumped - tree_rows), \
            f"pageup should move back a screen: {app._index}"
        # restore the cursor to u_core (the page test moved it), so the
        # following j -> port-leaf step sees the tree it expects
        u_core_node = None
        for node in app._visible():
            if "u_core" in node.label.plain and not node.label.plain.startswith("port"):
                u_core_node = node
                break
        assert u_core_node is not None, "u_core row missing after paging"
        app._select(app._visible().index(u_core_node))

        # in source, pagedown scrolls a screen and pageup returns
        await pilot.press("2")
        assert app.active == "source", "2 should activate the source pane"
        prev_offset = app.src_offset
        await pilot.press("pagedown")
        page_down_offset = app.src_offset
        assert page_down_offset == min(prev_offset + tree_rows, app._source_max_offset(tree_rows)), \
            f"pagedown in source: {page_down_offset}"
        await pilot.press("pageup")
        assert app.src_offset == max(0, page_down_offset - tree_rows), \
            f"pageup in source should restore: {app.src_offset}"

        # in I/O, a page moves the port selection (wrapping, like j/k)
        await pilot.press("3")
        assert app.active == "ports", "3 should activate the ports pane"
        port_rows = app._page_rows()
        before = app.port_index
        await pilot.press("pagedown")
        assert app.port_index == (before + port_rows) % len(app.pane_ports), \
            f"pagedown in I/O: {app.port_index}"
        await pilot.press("pageup")
        assert app.port_index == before, \
            f"pageup in I/O should restore: {app.port_index}"

        # j -> first port leaf under u_core; panes stay on core
        await pilot.press("1")  # back to the tree pane after the page tests
        assert app.active == "tree", "1 should reactivate the tree pane"
        await pilot.press("j")
        assert app._current_node.label.plain.startswith("port"), f"cursor: {app._current_node}"
        # the leaf reads 'port <dir> <name>[msb:lsb]'
        assert app._current_node.label.plain == "port in    a[31:0]", \
            f"port leaf label: {app._current_node.label.plain!r}"
        assert "core #(32) u_core(" in str(source.content), "pane unlinked from selection"
        assert status_path(app) == "top.u_core.a", f"port leaf status: {status_path(app)!r}"

        # yank recorded the selection
        await pilot.press("y")
        assert app.yanked, "yank recorded nothing"
        print("yanked:", app.yanked[-1])

        # ctrl+u from a port promotes to the current hierarchy's module node
        await pilot.press("ctrl+u")
        assert "core/" in app._current_node.label.plain, \
            f"fixture ctrl+u should select the module: {app._current_node}"
        assert status_path(app) == "core", f"status after ctrl+u: {status_path(app)!r}"

        # enter shows the highlighted module in the centre (cursor: core scope)
        await pilot.press("enter")
        assert "core" in str(source.content).lower(), "enter did not show core RTL"
        assert status_path(app) == "core", f"status after enter: {status_path(app)!r}"

        # pane selection: 2 routes j to source scroll, 3 to ports selection
        await pilot.press("2")
        assert app.active == "source", "pressing 2 should activate the source pane"
        prev_offset = app.src_offset
        await pilot.press("j")
        assert app.src_offset == prev_offset + 1, "j in source pane should scroll the source"
        # the arrows scroll the source pane too
        await pilot.press("down")
        assert app.src_offset == prev_offset + 2, "down should scroll the source pane"
        await pilot.press("up")
        assert app.src_offset == prev_offset + 1, "up should scroll the source pane back"

        await pilot.press("3")
        assert app.active == "ports", "pressing 3 should activate the ports pane"
        await pilot.press("j")
        assert app.port_index == 1, "j in ports pane should move the I/O selection"
        await pilot.press("y")
        assert "b" in app.yanked[-1], f"yank in ports pane should name the selected port: {app.yanked[-1]}"
        # the arrows move the I/O selection too
        before = app.port_index
        await pilot.press("down")
        assert app.port_index == (before + 1) % len(app.pane_ports), \
            f"down should move the I/O selection: {app.port_index}"
        await pilot.press("up")
        assert app.port_index == before, f"up should move it back: {app.port_index}"

        # enter in the I/O pane acts on the pane's selected port.
        # Fixture ports carry no src, so the enclosing scope's inline RTL
        # keeps the pane - the fallback a tree port leaf gets.
        await pilot.press("enter")
        assert app.active == "ports", "enter must not switch the pane"
        assert app._focus is None, f"src-less port must not resolve a focus: {app._focus}"
        text = str(source.content)
        assert "cannot read" not in text, f"src-less port hit the error path: {text[:120]!r}"
        assert ".alu_result(out)" in text, f"inline snippet not shown: {text[:120]!r}"

        await pilot.press("1")
        assert app.active == "tree", "pressing 1 should reactivate the tree pane"

        # the active-pane cue must be symmetric — exactly one pane
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

        # z zooms the active pane to the full width and hides the
        # other two; z again restores the three columns
        assert not app.zoomed, "the app must start unzoomed"
        await pilot.press("z")
        assert app.zoomed, "z should zoom"
        for name, widget in widgets.items():
            if name == "tree":
                assert widget.display, "the zoomed pane must stay visible"
                assert width_percent(widget) == 100.0, (
                    f"zoomed pane width is {widget.styles.width}"
                )
            else:
                assert not widget.display, f"{name} must be hidden while zoomed"

        # 1/2/3 move the zoom with the active pane (one pane is ever visible)
        await pilot.press("2")
        assert app.active == "source", "2 should activate the source pane"
        assert source.display and not tree.display and not ports.display, (
            "the zoom should follow the active pane"
        )

        # keys still act on the zoomed pane
        prev_offset = app.src_offset
        await pilot.press("j")
        assert app.src_offset == prev_offset + 1, "j should scroll the zoomed source"

        await pilot.press("z")
        assert not app.zoomed, "z should unzoom"
        for name, widget in widgets.items():
            assert widget.display, f"{name} must return after the zoom"
            assert width_percent(widget) == float(PANE_WIDTHS[name].rstrip("%")), (
                f"{name} width after unzoom: {widget.styles.width}"
            )
        await pilot.press("1")

        # the no-src fallback: fixture ports carry no src, so a
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

        # ---- ? opens the navigation cheatsheet ------------------
        await pilot.press("?")
        assert app._cheat is not None, "? should open the cheatsheet"
        assert not app._cheat.include_trace, "no trace open: no trace keys listed"
        cheat_rows = "\n".join(str(s.content) for s in app._cheat.query("Static"))
        assert "Navigation cheatsheet" in cheat_rows, cheat_rows
        # acceptance: the cheatsheet lists the yank and editor keys
        assert "yank the selection" in cheat_rows, "cheatsheet lists yank"
        assert "$EDITOR" in cheat_rows, "cheatsheet lists the editor keys"
        assert "While the trace is open:" not in cheat_rows

        # escape dismisses the cheatsheet
        await pilot.press("escape")
        assert app._cheat is None, "escape should dismiss the cheatsheet"

        # any nav key dismisses it first, then acts (no second press needed)
        await pilot.press("?")
        assert app._cheat is not None, "? should open the cheatsheet again"
        before = app._index
        await pilot.press("j")
        assert app._cheat is None, "a nav key should dismiss the cheatsheet"
        assert app._index == (before + 1) % len(app._visible()), (
            f"the nav key should still act: index {app._index}"
        )

        # ---- / global fuzzy search -----------------------------
        await pilot.press("slash")
        assert app._search is not None, "/ should open the search modal"
        # the query Input must hold focus or the printable keys behind the
        # modal (j/k/d/L/...) would fire the app bindings
        assert app.focused is app._search.inputwidget, \
            f"query box focused? {app.focused}"
        # a substring of a signal name finds the net across scopes
        await pilot.press(*"alu_result")
        rows = app._search.rows
        assert [e.kind for e in rows] == ["port", "net"], (
            f"alu_result rows: {[(e.kind, e.path) for e in rows]}"
        )
        assert all(e.path == "core.alu_result" for e in rows)
        # down moves to the net row; enter commits; ctrl+enter selects it
        await pilot.press("down")
        await pilot.press("enter")
        assert app._search is not None, "enter commits, never dismisses"
        assert app._search._committed, "enter should commit the query"
        assert app.focused is None, f"commit must blur the query box: {app.focused}"
        await pilot.press("ctrl+enter")
        assert app._search is None, "ctrl+enter should dismiss the search"
        node = app._current_node
        assert node is not None and node.data is not None, "no selection"
        assert node.data["kind"] == "net", node.data
        # the fixture is module-keyed: core's own scope row owns the leaf
        assert status_path(app) == "core.alu_result", status_path(app)
        assert app._focus == ("rtl/demo.sv", 48, 48), f"focus: {app._focus}"

        # vim-style walking: n/N move through the matches, everything else is
        # swallowed while the finder is committed
        await pilot.press("slash")
        await pilot.press(*"alu")
        rows = app._search.rows
        assert rows[0].kind == "module" and rows[0].path == "alu", rows
        await pilot.press("enter")
        search = app._search
        assert search._committed, "enter should commit"
        assert search._cursor == 0, f"commit keeps the cursor: {search._cursor}"
        await pilot.press("n")
        assert search._cursor == 1, "n steps to the next match"
        await pilot.press("n")
        await pilot.press("N")
        assert search._cursor == 1, "N steps back one match"
        before_index = app._index
        await pilot.press("q")  # must not quit, yank, or move the tree
        assert app._search is not None, "q must not quit a committed search"
        assert app._index == before_index, "a blocked key must not reach the tree"
        assert search._cursor == 1, "a blocked key must not move the match"
        await pilot.press("pagedown")  # must not page the tree behind
        assert app._index == before_index, "pagedown must not reach the tree under the search"
        assert search._cursor == 1, "pagedown must not move the match"
        await pilot.press("slash")  # / returns to the query box
        assert search is app._search, "/ must keep the finder open"
        assert not search._committed, "/ un-commits the finder"
        assert app.focused is search.inputwidget, "/ refocuses the query box"
        assert search._cursor == 1, "/ keeps the match position"
        await pilot.press("ctrl+enter")
        assert app._search is None, "ctrl+enter should close the search"
        assert status_path(app) == "alu.a", status_path(app)

        # the committed list wraps at the end, and enter returns to the query
        await pilot.press("slash")
        await pilot.press(*"alu")
        nrow = len(app._search.rows)
        await pilot.press("enter")
        search = app._search
        for _ in range(nrow):
            await pilot.press("n")
        assert search._cursor == 0, "n wraps through the whole match set"
        await pilot.press("enter")
        assert not search._committed, "enter returns to the query box"
        assert app.focused is search.inputwidget, "query box refocused"
        await pilot.press("x")  # a letter types again
        assert search.rows == [], f"typing must re-filter: {search.rows}"
        await pilot.press("escape")
        assert app._search is None, "escape dismisses from editing too"

        # committed search shows a bottom pane of the matched lines
        await pilot.press("slash")
        await pilot.press(*"alu_result")
        rows = app._search.rows
        assert [e.kind for e in rows] == ["port", "net"], rows
        await pilot.press("down")  # onto the net row
        await pilot.press("enter")
        await pilot.pause()
        search = app._search
        assert search._committed, "enter should commit"
        assert search.matchlist.display, "the matched-lines pane appears"
        # one source line per shown result: the net row is line 48
        match_rows = "\n".join(str(c.content) for c in search.matchlist.query("ListItem > Static"))
        assert "alu_result" in match_rows, f"match pane rows: {match_rows!r}"
        assert search.matchlist.index == search._cursor - search._wstart, (
            "the pane cursor must track the shared match"
        )
        # tab switches the active pane; up/down move the shared cursor either
        # way, and the results list follows
        assert search._pane == "results", "results is active first"
        await pilot.press("tab")
        assert search._pane == "matches", "tab switches to the matches pane"
        await pilot.press("down")
        # the pane walks the shared match position (2 rows wrap: 1 -> 0)
        assert search._cursor == 0, f"down in the pane walks the results: {search._cursor}"
        assert search.listwidget.index == search._cursor - search._wstart, (
            "the results cursor must follow the pane"
        )
        assert search.preview.content, "the preview follows the moved cursor"
        await pilot.press("tab")
        assert search._pane == "results", "tab switches back"
        await pilot.press("slash")  # / un-commits: the pane collapses
        assert not search._committed, "/ un-commits the finder"
        assert not search.matchlist.display, "the pane hides on un-commit"
        await pilot.press("ctrl+enter")
        assert app._search is None
        assert status_path(app) == "core.alu_result", status_path(app)

        # instance search returns the expected item for the demo IR
        await pilot.press("slash")
        await pilot.press(*"u_sram")
        rows = app._search.rows
        assert [e.kind for e in rows] == ["instance"], (
            f"u_sram rows: {[(e.kind, e.path) for e in rows]}"
        )
        assert rows[0].path == "core.u_sram", rows[0].path
        await pilot.press("escape")
        assert app._search is None, "escape should dismiss the search"

        # module search returns the black-box module
        await pilot.press("slash")
        await pilot.press(*"sram_stub")
        rows = app._search.rows
        # the module row ranks first; its own port leaves match the prefix too
        assert rows[0].kind == "module" and rows[0].path == "sram_stub", (
            f"sram_stub rows: {[(e.kind, e.path) for e in rows]}"
        )
        await pilot.press("escape")

        # port search
        await pilot.press("slash")
        await pilot.press(*"op")
        rows = app._search.rows
        assert any(e.kind == "port" and e.path == "alu.op" for e in rows), rows
        await pilot.press("escape")

        # file search
        await pilot.press("slash")
        await pilot.press(*"demo.sv")
        rows = app._search.rows
        assert any(e.kind == "file" and e.path == "rtl/demo.sv" for e in rows), rows
        await pilot.press("escape")
        assert app._search is None

        # z must be refused while the search is open
        await pilot.press("slash")
        assert app._search is not None
        await pilot.press("z")
        assert not app.zoomed, "z must be refused while the search is open"
        # and d/L must be refused too (mirror of the trace-under-search guard)
        await pilot.press("d")
        assert app._modal is None, "d must not open a trace under the search"
        await pilot.press("escape")

        out = Path(__file__).resolve().parent / "out"
        out.mkdir(exist_ok=True)
        (out / "shot.svg").write_text(app.export_screenshot())


def select_leaf(app: TrenApp, scope_path: str, kind: str, name: str) -> None:
    """Select a port/net leaf under a scope.

    The app's own `_select_path` resolves nets only (it exists for the trace
    modal's boundary crossing), so the port half needs this.
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


def width_percent(widget) -> float:
    """Percentage a pane's width style carries.

    Textual stores a percentage width as `Unit.WIDTH` ('32w'), so a plain
    comparison with `Scalar.parse('32%')` (`Unit.PERCENT`) never matches.
    Every pane width is a percentage, so read the number.
    """
    return widget.styles.width.value


def status_path(app: TrenApp) -> str:
    """Plain text of the top status line."""
    return str(app.query_one("#status").content)


async def drive_unreadable_src() -> None:
    """A leaf src naming a file that is not on disk.

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
            assert status_path(app) == "core.x", f"status on a net leaf: {status_path(app)!r}"
            text = str(source.content)
            assert "cannot read" not in text, f"no inline fallback: {text[:120]!r}"
            assert "core #(32) u_core(" in text, f"inline snippet not shown: {text[:120]!r}"
            assert app.src_offset == 0, f"inline view must start at the top: {app.src_offset}"


async def drive_real(root: Path, manifest_path: Path) -> None:
    """Real IR, source pane loads the actual RTL file, auto-follow."""
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
        assert status_path(app) == "top", f"status: {status_path(app)!r}"

        # j does not unfold; l does (file-manager model)
        await pilot.press("j")
        assert "top/" in app._current_node.label.plain, "j must not auto-expand"

        await pilot.press("l")
        assert app._current_node.is_expanded, "l should expand top"

        # descend into u_core -> its *instantiation in top* is focused
        await pilot.press("j")
        assert "u_core" in app._current_node.label.plain, f"cursor: {app._current_node}"
        assert app._focus == ("rtl/demo.sv", 77, 77), f"u_core focus: {app._focus}"
        assert status_path(app) == "top.u_core", f"status: {status_path(app)!r}"
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
        assert status_path(app) == "top.u_core.u_alu0", f"status: {status_path(app)!r}"

        # k walks back up one visible row -> u_core path again
        await pilot.press("k")
        assert "u_core" in app._current_node.label.plain, f"after k: {app._current_node}"
        assert status_path(app) == "top.u_core", f"status after k: {status_path(app)!r}"
        await pilot.press("j")  # back onto u_alu0
        assert app.src_offset + 1 <= 56 <= app.src_offset + rows, f"band out of view: offset {app.src_offset}"

        # pagedown scrolls the real source a full screen and the
        # auto-follow band is not forced (the view moves, not the selection)
        await pilot.press("2")  # page the source pane itself
        assert app.active == "source", "2 should activate the source pane"
        page_rows = app._page_rows()
        assert page_rows >= 1, f"page rows: {page_rows}"
        before = app.src_offset
        await pilot.press("pagedown")
        expected = min(before + page_rows, app._source_max_offset(page_rows))
        assert app.src_offset == expected, \
            f"pagedown in real source: {app.src_offset} != {expected}"
        before = app.src_offset
        await pilot.press("pagedown")
        second = app.src_offset
        assert second == min(before + page_rows, app._source_max_offset(page_rows)), \
            f"pagedown in source pane: {second}"
        await pilot.press("pageup")
        up_again = max(0, second - page_rows)
        assert app.src_offset == up_again, \
            f"pageup in source should restore: {app.src_offset}"
        # and pagedown restores exactly the pre-pageup position (round-trip)
        await pilot.press("pagedown")
        expected = min(up_again + page_rows, app._source_max_offset(page_rows))
        assert app.src_offset == expected, \
            f"pagedown after pageup: {app.src_offset}"
        # pagedown past the end clamps at the last page (editor-style)
        await pilot.press("pagedown")
        await pilot.press("pagedown")
        assert app.src_offset == app._source_max_offset(page_rows), \
            f"pagedown must clamp at the end: {app.src_offset}"
        await pilot.press("1")
        assert app.active == "tree", "1 should reactivate the tree pane"

        # e/E: on u_alu0, e = the instantiation line, E = the
        # module's own definition (alu begins at rtl/demo.sv:21)
        await pilot.press("e")
        assert app.editor_target == "rtl/demo.sv:56", f"editor target: {app.editor_target}"
        await pilot.press("E")
        assert app.editor_target == "rtl/demo.sv:21", f"editor def target: {app.editor_target}"

        # ctrl+u walks back up to core (the enclosing scope of u_alu0)
        await pilot.press("ctrl+u")
        assert "u_core" in app._current_node.label.plain, f"after ctrl+u: {app._current_node}"
        assert app._focus == ("rtl/demo.sv", 77, 77), "focus should return to u_core"
        assert status_path(app) == "top.u_core", f"status after ctrl+u: {status_path(app)!r}"

        # on a nested scope, e = the instantiation in its parent (+77),
        # E = the module's own declaration (+45)
        await pilot.press("e")
        assert app.editor_target == "rtl/demo.sv:77", f"e on u_core: {app.editor_target}"
        await pilot.press("E")
        assert app.editor_target == "rtl/demo.sv:45", f"E on u_core: {app.editor_target}"

        # enter shows the highlighted module in the centre with context
        await pilot.press("enter")
        rows = app._source_widget.size.height or 30
        assert app.src_offset <= 77 <= app.src_offset + rows, f"band out of view: offset {app.src_offset}"
        assert status_path(app) == "top.u_core", f"status after enter: {status_path(app)!r}"

        # ---- leaves focus their own DECLARATION -------------
        # The two backends spell the span differently and both land here:
        # Port.src is slang's file:line:col, Net.src the yosys file:l.c-l.c.
        select_leaf(app, "top.u_core.u_alu0", "port", "op")
        # a real port leaf reads 'port <dir> <name>[msb:lsb]'
        assert app._current_node.label.plain == "port in    op[3:0]", \
            f"port leaf label: {app._current_node.label.plain!r}"
        assert app._focus == ("rtl/demo.sv", 24, 24), f"port op focus: {app._focus}"
        assert "input  logic [3:0]   op," in str(source.content), "op declaration not shown"
        assert status_path(app) == "top.u_core.u_alu0.op", f"port leaf status: {status_path(app)!r}"
        rows = app._source_widget.size.height or 30
        assert app.src_offset + 1 <= 24 <= app.src_offset + rows, (
            f"port declaration out of view: offset {app.src_offset}"
        )

        select_leaf(app, "top.u_core", "net", "x")
        # a net leaf carries the port's range spelling
        assert app._current_node.label.plain == "net x[31:0]", \
            f"net leaf label: {app._current_node.label.plain!r}"
        assert app._focus == ("rtl/demo.sv", 50, 50), f"net x focus: {app._focus}"
        assert "logic [W-1:0] x, y, rd, r0;" in str(source.content), "net declaration not shown"
        assert status_path(app) == "top.u_core.x", f"net leaf status: {status_path(app)!r}"
        assert app.src_offset + 1 <= 50 <= app.src_offset + rows, (
            f"net declaration out of view: offset {app.src_offset}"
        )

        # generate scopes carry a slang src too — same spelling, same trap
        generate = app._find_scope_node("top.u_core.g[0]")
        assert generate is not None, "no generate scope node"
        app._expand_to(generate)
        app._select(app._visible().index(generate))
        assert app._focus == ("rtl/demo.sv", 58, 58), f"g[0] focus: {app._focus}"
        assert status_path(app) == "top.u_core.g[0]", f"generate scope status: {status_path(app)!r}"

        # an output port resolves the same way (the direction plays no part)
        select_leaf(app, "top.u_core.u_alu0", "port", "result")
        assert app._focus == ("rtl/demo.sv", 25, 25), f"port result focus: {app._focus}"
        assert status_path(app) == "top.u_core.u_alu0.result", f"out port status: {status_path(app)!r}"
        # the no-src fallback is exercised in drive_fixture, where ports
        # genuinely carry no src (every port in the real IR has one)

        # ---- the I/O pane selection drives the centre pane -------
        await pilot.press("3")
        assert app.active == "ports", "pressing 3 should activate the ports pane"
        assert app.port_index == 0, f"untouched pane-3 selection: {app.port_index}"
        port = app.pane_ports[app.port_index]
        assert port.name == "a", f"first I/O row of u_alu0: {port.name}"
        assert app._focus == ("rtl/demo.sv", 22, 22), f"port {port.name} focus: {app._focus}"
        assert "input  logic [W-1:0] a," in str(source.content), "a declaration not shown"
        assert app.src_offset + 1 <= 22 <= app.src_offset + rows, (
            f"port declaration out of view: offset {app.src_offset}"
        )
        # moving the I/O selection moves the highlight with it
        await pilot.press("j")
        port = app.pane_ports[app.port_index]
        assert port.name == "b", f"second I/O row of u_alu0: {port.name}"
        assert app._focus == ("rtl/demo.sv", 23, 23), f"port {port.name} focus: {app._focus}"
        assert "input  logic [W-1:0] b," in str(source.content), "b declaration not shown"
        await pilot.press("k")  # leave the I/O selection where it started
        assert app.port_index == 0, f"restored pane-3 selection: {app.port_index}"
        # back to the tree pane: the centre follows the tree selection again
        # (the cursor still sits on the result leaf selected above)
        await pilot.press("1")
        assert app.active == "tree", "pressing 1 should reactivate the tree pane"
        assert app._focus == ("rtl/demo.sv", 25, 25), (
            f"tree selection focus after the pane switch: {app._focus}"
        )

        # ---- d/L trace with hierarchy crossing ------------------
        # walk from top.out to its $xor driver and back; dump shows the path
        app._select_path("top", "out")
        assert status_path(app) == "top.out", f"net under top status: {status_path(app)!r}"
        await pilot.press("d")
        modal = app._modal
        assert modal is not None, "d should open the trace modal"
        # the modal covers the panes, so z must not zoom behind it
        await pilot.press("z")
        assert not app.zoomed, "z must be refused while the trace modal is open"
        # page keys must not move the tree behind the modal
        tree_before = app._index
        await pilot.press("pagedown")
        await pilot.press("pageup")
        assert app._index == tree_before, \
            "page keys must not reach the tree under the trace modal"
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

        # in the modal right descends and left goes back, so an
        # arrow never falls through to the tree behind it
        await pilot.press("right")
        assert app._trace == [("top", "out"), ("top.u_core", "alu_result")], \
            f"right should cross the boundary: {app._trace}"
        await pilot.press("left")
        assert app._trace == [("top", "out")], f"left should pop the trace: {app._trace}"

        await pilot.press("escape")
        assert app._modal is None, "escape should close the trace modal"

        # ---- ? cheatsheet, incl. opened from inside a trace -----
        await pilot.press("?")
        assert app._cheat is not None, "? should open the cheatsheet"
        assert not app._cheat.include_trace, "no trace open: no trace keys"
        cheat_rows = "\n".join(str(s.content) for s in app._cheat.query("Static"))
        assert "yank the selection" in cheat_rows, "cheatsheet lists yank"
        assert "$EDITOR" in cheat_rows, "cheatsheet lists the editor keys"
        await pilot.press("escape")
        assert app._cheat is None, "escape should dismiss the cheatsheet"

        # from inside the d/L modal the cheatsheet also lists the trace keys
        app._select_path("top", "out")
        await pilot.press("d")
        modal = app._modal
        assert modal is not None, "d should open the trace modal"
        await pilot.press("?")
        assert app._cheat is not None, "? inside the trace modal"
        assert app._cheat.include_trace, "a trace is open: list the trace keys"
        cheat_rows = "\n".join(str(s.content) for s in app._cheat.query("Static"))
        assert "While the trace is open:" in cheat_rows, cheat_rows
        assert "crosses a boundary" in cheat_rows, "trace keys listed"
        assert "yank the selection" in cheat_rows, "screen keys still there"
        await pilot.press("escape")
        assert app._cheat is None, "escape dismisses the cheatsheet"
        assert app._modal is not None, "the trace modal survives the cheatsheet"
        await pilot.press("escape")
        assert app._modal is None, "escape then closes the trace modal"

        # ---- black boxes + user cuts ----------------------------
        # sram_stub renders as an opaque scope: marker + its ports
        app._select_path("top.u_core.u_sram")
        ports_text = str(ports.content)
        assert "BLACK BOX" in ports_text, f"no black box marker: {ports_text!r}"
        assert "rdata" in ports_text, "black box ports missing"
        assert status_path(app) == "top.u_core.u_sram", f"black box status: {status_path(app)!r}"
        # a scalar port carries no range, and out lines up with in
        select_leaf(app, "top.u_core.u_sram", "port", "we")
        assert app._current_node.label.plain == "port in    we", \
            f"scalar port leaf label: {app._current_node.label.plain!r}"
        select_leaf(app, "top.u_core.u_sram", "port", "rdata")
        assert app._current_node.label.plain == "port out   rdata[31:0]", \
            f"out port leaf label: {app._current_node.label.plain!r}"
        select_leaf(app, "top.u_core.u_sram", "net", "we")
        assert app._current_node.label.plain == "net we", \
            f"scalar net leaf label: {app._current_node.label.plain!r}"

        # X toggles a user cut on core (opaque and back)
        app._select_path("top.u_core")
        assert status_path(app) == "top.u_core", f"status: {status_path(app)!r}"
        # I/O pane shows the slang overlay — resolved value,
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

        # ---- yank formats + OSC 52 + editor jump ----------------
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
        assert status_path(app) == "top.u_core.g[0].u_alu_g.result", f"deep net status: {status_path(app)!r}"
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

        # ---- ctrl+u jumps to the next upper level ----------------
        # from a deep generate-block scope, one ctrl+u lands on its parent
        app._select_path("top.u_core.g[0].u_alu_g")
        assert status_path(app) == "top.u_core.g[0].u_alu_g", f"deep scope status: {status_path(app)!r}"
        await pilot.press("ctrl+u")
        assert "u_core" in app._current_node.label.plain, f"after ctrl+u: {app._current_node}"
        assert status_path(app) == "top.u_core", f"status after deep ctrl+u: {status_path(app)!r}"

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

        # ---- on a top-level module e and E both land on the
        #      definition (no parent instantiation to jump to)
        await pilot.press("e")
        assert app.editor_target == "rtl/demo.sv:70", f"e on top: {app.editor_target}"
        await pilot.press("E")
        assert app.editor_target == "rtl/demo.sv:70", f"E on top: {app.editor_target}"

        # ---- enter shows the module; the d/L modal keeps Enter ----
        app._select_path("top", "out")
        await pilot.press("d")
        modal = app._modal
        assert modal is not None
        await pilot.press("enter")  # must cross the boundary, not trigger focus_source
        assert app._trace == [("top", "out"), ("top.u_core", "alu_result")], f"trace: {app._trace}"
        await pilot.press("escape")

        # ---- / global search on the real IR ---------------------
        await pilot.press("slash")
        assert app._search is not None, "/ should open the search modal"
        assert app.focused is app._search.inputwidget, \
            f"query box focused? {app.focused}"
        # typing a substring of a signal name finds the net across scopes
        await pilot.press(*"alu_result")
        rows = app._search.rows
        assert [e.kind for e in rows if e.path == "top.u_core.alu_result"] == [
            "port", "net"], rows
        await pilot.press("down")  # -> the net row
        await pilot.press("enter")  # commit; the finder stays open
        assert app._search is not None and app._search._committed, \
            "enter commits the query"
        assert app.focused is None, f"commit blurs the query box: {app.focused}"
        await pilot.pause()
        search = app._search
        assert search.matchlist.display, "the matched-lines pane appears"
        match_rows = "\n".join(str(c.content) for c in search.matchlist.query("ListItem > Static"))
        assert "alu_result" in match_rows, f"match pane rows: {match_rows!r}"
        await pilot.press("tab")
        assert search._pane == "matches", "tab switches to the matches pane"
        await pilot.press("down")
        assert search.listwidget.index == search._cursor - search._wstart, (
            "the results cursor must follow the pane"
        )
        await pilot.press("tab")
        assert search._pane == "results", "tab switches back"
        await pilot.press("N")  # back onto the net row before the jump below
        assert search._cursor == 1, f"N returns to the net row: {search._cursor}"
        await pilot.press("slash")  # / returns to the query box
        assert not app._search._committed, "/ un-commits the finder"
        assert app.focused is app._search.inputwidget, "/ refocuses the query box"
        await pilot.press("enter")  # commit again before opening
        assert app._search._committed, "enter commits again"
        await pilot.press("ctrl+enter")  # select the highlighted result
        assert app._search is None, "ctrl+enter should dismiss the search"
        assert status_path(app) == "top.u_core.alu_result", status_path(app)
        # yosys attributes the net to the declaration line (the output port)
        assert app._focus == ("rtl/demo.sv", 48, 48), f"focus: {app._focus}"

        # file search jumps to the first scope carrying a src in the file
        await pilot.press("slash")
        await pilot.press(*"demo.sv")
        rows = app._search.rows
        assert any(e.kind == "file" and e.path == "rtl/demo.sv" for e in rows), rows
        await pilot.press("ctrl+enter")
        assert app._search is None
        assert status_path(app) == "top", status_path(app)

        # module and instance searches both find the black-box scope
        await pilot.press("slash")
        await pilot.press(*"u_sram")
        rows = app._search.rows
        assert any(e.kind == "module" and e.path == "top.u_core.u_sram" for e in rows), rows
        assert any(e.kind == "instance" and e.path == "top.u_core.u_sram" for e in rows), rows
        await pilot.press("ctrl+enter")  # first row is the module
        assert app._search is None
        assert status_path(app) == "top.u_core.u_sram", status_path(app)
        assert "BLACK BOX" in str(ports.content), "black box marker after jump"

        out = Path(__file__).resolve().parent / "out"
        out.mkdir(exist_ok=True)
        (out / "shot-real.svg").write_text(app.export_screenshot())


async def drive_search_window() -> None:
    """Committed search over more matches than the window: n/N slide it.

    The fixture design matches "alu" ten times while the finder materialises
    at most three rows here, so walking the whole match set must slide the
    window and keep the ListView cursor tracking the walk.
    """
    SearchScreen.MAX_ROWS = 3
    app = TrenApp(load_design(), root=Path.cwd())
    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.press("slash")
        await pilot.press(*"alu")
        search = app._search
        assert len(search.rows) > 3, "the window must actually bind"
        await pilot.press("enter")
        assert search._committed, "enter should commit"
        assert search.matchlist.display, "the matched-lines pane appears"
        seen_wstarts: set[int] = set()
        for _ in range(len(search.rows) * 2):
            seen_wstarts.add(search._wstart)
            assert search._shown[0] is search.rows[search._wstart], (
                "the shown list must open at the window start"
            )
            assert search.listwidget.index == search._cursor - search._wstart, (
                "the ListView cursor must track the walk"
            )
            assert search.matchlist.index == search._cursor - search._wstart, (
                "the matches-pane cursor must track the walk"
            )
            await pilot.press("n")
        assert search._cursor == 0, "n wraps the whole match set"
        assert len(seen_wstarts) > 1, "the window must slide over a long match set"


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
        await drive_search_window()
    print("PASS")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
    print("ok")
