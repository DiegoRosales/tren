"""tren — 3-pane textual spike, driven by the IR (tren.ir.Design).

Top:     yazi-style status line with the selection's full hierarchical path
         (appends the item name for a port/net leaf), updating live on nav.
Left:    scope tree (filesystem-like hierarchy; generate scopes nested)
Center:  source of the module you're sitting on — the *actual RTL file*
         loaded at the scope's src (file:line already in IR), with the
         focused range highlighted. Semantics: the module's *instantiation
         in its parent* (its src), else the module declaration; falls back
         to the inline rtl snippet when no file src exists (fixture).
Right:   the module's I/O (ports)

Auto-follow: on scope change the focus band is kept in view
(src_offset = focus_start - rows/2, clamped). `e` opens the editor (stub)
at the selection's declaration line, `E` at the module definition.

Keys: j/k move, pgup/pgdn page a screenful in the active pane (card 283),
l expand, h collapse, enter shows the selection in the centre (the module,
or the I/O pane's selected port declaration, card 281), ctrl+u jumps up a
level, 1/2/3 pane select, z zoom the active pane, y yank,
/ global search (modules, instances, ports, nets, files; card 244; enter
commits, n/N walk the matches, ctrl+enter selects and closes),
? navigation cheatsheet, e editor at the selection's declaration, E editor at
the module definition, q quit. The arrow keys are aliases: down/up = j/k,
right/left = l/h (card 267); in the trace modal right descends and left goes
back.
Headless Pilot tests:  python test_drive.py [--manifest …]
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
from pathlib import Path

from rich.text import Text as RichText
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Footer,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
    Tree,
)

from .cache import IrCache
from .elaborate import elaborate
from .formats import BY_NAME, DEFAULT, PathFormat, render as render_format
from .ir import Design, Net, Port, Scope, design_from_fixture, parse_src as _parse_src
from .keys import SCREEN_KEYS, TRACE_KEYS, KeyDoc
from .manifest import Manifest
from .search import SearchEntry, build_search_index, filter_and_rank

FIXTURE = Path(__file__).resolve().parents[2] / "tui" / "fixtures" / "demo-tree.json"

# active-pane cue: the pane 1/2/3 acts on is lifted out of the background
PANE_BG_ACTIVE = "#262626"
PANE_BG_IDLE = "#121212"
# status line background (card 246): distinctly a chrome bar, not a pane
STATUS_BG = "#1e1e1e"
# three-column layout (card 268 restores these widths when the zoom ends)
PANE_WIDTHS = {"tree": "32%", "source": "48%", "ports": "20%"}
ZOOM_WIDTH = "100%"
# port leaf column (card 253): "inout" is the longest direction
PORT_DIR_WIDTH = 5
# keys the *committed* finder handles itself (event.key form). Everything
# else is stopped while it is up: the query box no longer holds focus there,
# so a printable key would otherwise fire the app's priority bindings behind
# the modal (q quit, y yank, X cut, ...). '/' is NOT here: the app's own
# priority open_search would steal it, so TrenApp.on_event intercepts it
# before dispatch and returns to the query box instead. up/down move the
# highlighted match whatever pane is active (results or source-line bottom
# pane); tab flips between those two panes (card 280). Compare event.key,
# not event.name: textual spells 'N' as name 'upper_n' and '/' as 'slash'
# while the binding and event.key keep their original form.
SEARCH_COMMITTED_KEYS = frozenset(
    {"n", "N", "up", "down", "enter", "escape", "ctrl+enter", "tab"}
)

# First row a window of `window` rows shows with `cursor` centred in it.
# The finder materialises at most MAX_ROWS rows; committed, n/N walk the
# whole result list and the window slides to keep the cursor visible.
def _window_start(cursor: int, total: int, window: int) -> int:
    if total <= window:
        return 0
    return max(0, min(cursor - window // 2, total - window))


def load_design(fixture: Path | None = None) -> Design:
    return design_from_fixture(fixture or FIXTURE)


def _scope_parent(path: str) -> str | None:
    parts = path.rsplit(".", 1)
    return parts[0] if len(parts) == 2 else None


def _bit_range(width: int, declared: str | None = None) -> str:
    """Bit range suffix for a leaf: '[msb:lsb]', empty for a scalar.

    The declared range comes from the slang overlay and reads as the source
    writes it, ``[7:1]`` and ``[0:7]`` included (card 219). A signal the
    overlay never reached carries none, and the range falls back to the
    width as ``[width-1:0]``.
    """
    if declared:
        return declared
    return f"[{width - 1}:0]" if width > 1 else ""


def port_leaf_label(port: Port) -> str:
    """Hierarchy leaf text for a port: 'port <dir> <name>[msb:lsb]' (card 253).

    The direction is left-aligned in a fixed column, so in / out / inout all
    start the name at the same offset.
    """
    suffix = _bit_range(port.width, port.range)
    return f"port {port.direction:<{PORT_DIR_WIDTH}} {port.name}{suffix}"


def net_leaf_label(net: Net) -> str:
    """Hierarchy leaf text for a net: 'net <name>[msb:lsb]' (card 253).

    The range reads the same as a port's. A net has no direction column.
    """
    return f"net {net.name}{_bit_range(net.width, net.range)}"


def cheat_line(doc: KeyDoc) -> str:
    """Cheatsheet row: the keys in a fixed column, then the effect (card 248)."""
    return f"{doc.keys:<16} {doc.effect}"


def search_row_label(entry: SearchEntry) -> str:
    """One result row of the global finder: kind, then the full path (card 244)."""
    if entry.kind == "module":
        return f"module {entry.path}"
    if entry.kind == "instance":
        target = entry.instance.module if entry.instance is not None else None
        return f"instance {entry.path} -> {target or 'generate'}"
    if entry.kind == "port":
        return f"port {entry.path}"
    if entry.kind == "net":
        return f"net {entry.path}"
    if entry.kind == "file":
        return f"file {entry.path}"
    return entry.path


class SourcePane(Static):
    """Center: RTL source with the focused range highlighted."""


class PortsPane(Static):
    """Right: I/O list of the current module."""


class StatusBar(Static):
    """Top line: the selection's full hierarchical path (yazi-style, card 246).

    A plain Static is never focusable, so it cannot steal input — it is only a
    display seam, updated by the app as the selection moves.
    """

    can_focus = False


class CheatSheet(ModalScreen[None]):
    """Navigation cheatsheet: the full key map on one screen (card 248).

    Escape dismisses. Every other key also dismisses first and then continues
    to the layer below - TrenApp.on_event pops the sheet before dispatching,
    so a nav key reads the map and keeps navigating in one press. Both the
    sheet and the README table come from tren.keys, so they cannot drift.

    Opening it while the d/L trace modal is open lists the trace keys too:
    the app passes include_trace from whether a trace is currently shown.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "close", show=False),
    ]

    def __init__(self, include_trace: bool = False) -> None:
        super().__init__()
        self.include_trace = include_trace

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Navigation cheatsheet", id="cheat-title")
            for doc in SCREEN_KEYS:
                yield Static(cheat_line(doc))
            if self.include_trace:
                yield Label("While the trace is open:", id="cheat-trace-title")
                for doc in TRACE_KEYS:
                    yield Static(cheat_line(doc))
            yield Label("Escape or any key closes this.", id="cheat-hint")
        yield Footer()

    def on_mount(self) -> None:
        self._app = self.app  # type: ignore[assignment]
        self._app._cheat = self  # type: ignore[attr-defined]

    def on_unmount(self) -> None:
        self._app._cheat = None  # type: ignore[attr-defined]


class TraceScreen(ModalScreen[None]):
    """Drivers/loads of the trace head: enter descends, backspace pops.

    Enter is handled via ListView.Selected (ListView owns the enter key).
    List navigation uses arrow keys: the App's j/k bindings are
    priority=True and would shadow same-letter modal bindings (verified).
    The focused ListView keeps up/down for the list, because the App's arrow
    aliases (card 267) are not priority. Left/right are free there, so the
    modal binds them to back/descend: an arrow in the trace never reaches the
    tree behind the modal.
    """

    BINDINGS = [
        Binding("right", "descend_trace", "descend", show=False),
        Binding("left", "pop_trace", "back", show=False),
        Binding("backspace", "pop_trace", "back", show=False),
        Binding("escape", "dismiss", "close", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("", id="trace-title")
            yield ListView()
        yield Footer()

    def on_mount(self) -> None:
        self._app = self.app  # type: ignore[assignment]
        self._app._modal = self  # type: ignore[attr-defined]
        self.listwidget = self.query_one(ListView)
        self.rows: list[dict] = []
        self._refresh()

    def on_unmount(self) -> None:
        self._app._modal = None  # type: ignore[attr-defined]

    def _refresh(self) -> None:
        app = self._app
        scope, net = app._trace[-1]
        self.query_one("#trace-title", Label).update(
            f"{app._trace_dir} of {scope}.{net}"
        )
        self.rows = app._trace_rows()
        self.listwidget.clear()
        self.listwidget.extend(ListItem(Static(r["label"])) for r in self.rows)
        if self.rows:
            self.listwidget.index = 0
        self.listwidget.focus()

    def _descend(self, idx: int | None) -> None:
        app = self._app
        if idx is not None and 0 <= idx < len(self.rows):
            if app._trace_descend(self.rows[idx]):
                self._refresh()
                return
        app.bell()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._descend(event.index if event.index is not None else self.listwidget.index)

    def action_descend_trace(self) -> None:
        """right: cross the highlighted boundary, as enter does."""
        self._descend(self.listwidget.index)

    def action_pop_trace(self) -> None:
        app = self._app
        if app._trace_pop():
            self._refresh()
        else:
            app.bell()


class SearchScreen(ModalScreen[None]):
    """Global fuzzy search: modules, instances, ports, nets, files (card 244).

    Two modes, vim-style. While the query Input holds focus, every printable
    key - j and k included - types text and never reaches the app bindings
    behind the modal (textual strips printable keys from the binding chain
    while an Input is focused; verified). up/down, the arrow aliases of j/k
    (card 267), move the cursor there; the modal binding chain truncates at
    this screen, so they beat the app's own arrow aliases. `enter` (Input
    Submitted) commits the query: the Input is blurred and the finder stays
    open, and `n` / `N` step to the next / previous match through the whole
    result list.
    `ctrl+enter` leaves the finder and selects the highlighted result in the
    tree; `escape` dismisses without jumping. `enter` or `/` returns to the
    query box to refine. Nothing holds focus while the finder is committed,
    so TrenApp.on_event swallows every key except the finder's own -
    otherwise the app's priority printable bindings (q quit, y yank, X cut,
    ...) would fire under the modal.

Committed, a bottom pane lists the matched source lines (vim's
    `:g//p`): one row per result, showing the line of RTL the result means.
    `tab` switches which pane is active; up/down and n/N always move a
    single shared cursor, so scrolling the bottom pane walks the result
    list and the highlight follows (card 280).

    The right column previews the result's source focus by reusing the
    centre pane's renderer with a private scroll offset, so the pane state
    behind the modal is untouched. The result list materialises at most
    `MAX_ROWS` rows at once; walking past the edge slides the window, and
    the count label always shows the real total.
    """

    BINDINGS = [
        Binding("up", "search_prev", "up", show=False),
        Binding("down", "search_next", "down", show=False),
        Binding("n", "search_next_match", "next match", show=False),
        Binding("N", "search_prev_match", "prev match", show=False),
        Binding("enter", "search_edit_query", "edit query", show=False),
        Binding("tab", "search_cycle_pane", "match pane", show=False),
        Binding("ctrl+enter", "search_jump", "open", show=False),
        Binding("escape", "dismiss", "close", show=False),
    ]

    PREVIEW_ROWS = 12

    # Rows the results ListView materialises. Creating a ListItem widget per
    # row dominates the finder's latency on a large design (measured ~0.5 ms
    # per row), so the list is a window over the ranked rows and the count
    # label carries the real total.
    MAX_ROWS = 200

    # Rows the match pane shows at once. The pane is the vim :g//p list of
    # the matched source lines, height-capped so the finder keeps acting as
    # a modal over the panes behind it (card 280).
    MATCH_ROWS = 7

    def compose(self) -> ComposeResult:
        # select_on_focus=False: returning to the query box after a commit
        # must keep the caret at the end, so a letter APPENDS to the query
        # instead of replacing the whole text (textual selects all on focus).
        yield Input(
            placeholder="search modules, instances, ports, nets, files",
            id="search-input",
            select_on_focus=False,
        )
        with Horizontal():
            yield ListView(id="search-results")
            yield Static("", id="search-preview")
        yield ListView(id="search-matches")
        yield Label("", id="search-count")
        yield Footer()

    def on_mount(self) -> None:
        self._app = self.app  # type: ignore[assignment]
        self._app._search = self  # type: ignore[attr-defined]
        self.inputwidget = self.query_one("#search-input", Input)
        self.listwidget = self.query_one("#search-results", ListView)
        self.matchlist = self.query_one("#search-matches", ListView)
        self.preview = self.query_one("#search-preview", Static)
        self.countlabel = self.query_one("#search-count", Label)
        self.rows = filter_and_rank(self._app._search_index, "")  # type: ignore[attr-defined]
        self._cursor = 0
        self._wstart = 0
        self._committed = False
        self._preview_entry: SearchEntry | None = None
        self._pane = "results"  # up/down act on the results list or the matches
        self.matchlist.styles.height = self.MATCH_ROWS
        self.matchlist.display = False
        # panes get a border so the active cue survives the items painting
        # over a plain background (the ListItems own their rows)
        self.listwidget.border_title = "results"
        self.matchlist.border_title = "matches"
        # the matches and results panes share one cursor: painting the active
        # pane lifts it like the app's own pane cue (card 280)
        self._paint_panes()
        self._show_window()
        self.inputwidget.focus()

    def on_unmount(self) -> None:
        self._app._search = None  # type: ignore[attr-defined]

    def on_input_changed(self, event: Input.Changed) -> None:
        self.rows = filter_and_rank(self._app._search_index, event.value)  # type: ignore[attr-defined]
        self._cursor = 0
        self._committed = False
        self._pane = "results"
        self._refresh_matches()
        self._show_window()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """enter: commit the query so n/N walk the matches, vim-style."""
        if not self.rows:
            self._app.bell()  # type: ignore[attr-defined]
            return
        self._committed = True
        self._refresh_matches(rebuild=True)
        self._render_preview()
        self._render_count()
        self.set_focus(None)

    def action_search_next(self) -> None:
        self._move(1)

    def action_search_prev(self) -> None:
        self._move(-1)

    def action_search_next_match(self) -> None:
        self._move(1)

    def action_search_prev_match(self) -> None:
        self._move(-1)

    def action_search_jump(self) -> None:
        """ctrl+enter: select the highlighted result in the tree and close."""
        if not self.rows:
            self._app.bell()  # type: ignore[attr-defined]
            return
        entry = self.rows[self._cursor]
        if self._app._select_entry(entry):  # type: ignore[attr-defined]
            self._app.pop_screen()
        else:
            self._app.bell()

    def action_search_cycle_pane(self) -> None:
        """tab: switch the active pane between results and matches (card 280)."""
        if not self._committed:
            return
        self._pane = "matches" if self._pane == "results" else "results"
        self._paint_panes()

    def action_search_edit_query(self) -> None:
        """enter (committed): give the query box focus back to refine it."""
        self._committed = False
        self._pane = "results"
        self._refresh_matches()
        self._render_count()
        self.inputwidget.focus()

    def _move(self, delta: int) -> None:
        if not self.rows:
            self._app.bell()  # type: ignore[attr-defined]
            return
        if self._committed and len(self.rows) > self.MAX_ROWS:
            # committed over a list longer than the window: walk the whole
            # result set, sliding the materialised window over the cursor
            self._cursor = (self._cursor + delta) % len(self.rows)
            self._advance_window()
        else:
            # editing (or a small committed list): walk the shown window
            total = len(self._shown)
            local = (self._cursor - self._wstart + delta) % total if total else 0
            self._cursor = self._wstart + local
        self._sync_cursor()
        self._render_preview()
        if self._committed:
            self._render_count()

    def _sync_cursor(self) -> None:
        """Point both panes' ListViews at the shared cursor (card 280)."""
        row = self._cursor - self._wstart
        self.listwidget.index = row
        if self.matchlist.display:
            self.matchlist.index = min(row, len(self._shown) - 1)

    def _advance_window(self) -> None:
        """Slide the materialised window over the cursor (committed mode)."""
        want = _window_start(self._cursor, len(self.rows), self.MAX_ROWS)
        if want == self._wstart:
            return
        self._wstart = want
        self._shown = self.rows[want : want + self.MAX_ROWS]
        self.listwidget.clear()
        self.listwidget.extend(ListItem(Static(search_row_label(e))) for e in self._shown)
        self._refresh_matches(rebuild=True)

    def _show_window(self) -> None:
        """(Re)materialise the top window after the query changed."""
        self._wstart = 0
        self._shown = self.rows[: self.MAX_ROWS]
        self.listwidget.clear()
        self.listwidget.extend(ListItem(Static(search_row_label(e))) for e in self._shown)
        if self._shown:
            self.listwidget.index = min(self._cursor, len(self._shown) - 1)
        self._refresh_matches(rebuild=True)
        self._render_count()
        self._render_preview()

    def _render_count(self) -> None:
        total = len(self.rows)
        if not total:
            self.countlabel.update("no results")
        elif self._committed:
            self.countlabel.update(
                f"match {self._cursor + 1} of {total} - n/N next/prev, "
                "tab pane, ctrl+enter open"
            )
        else:
            self.countlabel.update(f"{total} result{'s' if total != 1 else ''}")

    def _match_source(self, entry: SearchEntry) -> str:
        """The source line the result matches (vim :g//p, card 280).

        A real focus reads the file line at the span; a fixture scope with an
        inline snippet reads its focused rtl line. Neither exists (a black
        box's ports) -> fall back to the row label so the pane never blanks.
        """
        focus, scope = self._app._entry_preview(entry)  # type: ignore[attr-defined]
        if focus is not None:
            file, lo, _ = focus
            lines = self._app._file_lines(file)  # type: ignore[attr-defined]
            if lines and 1 <= lo <= len(lines):
                return lines[lo - 1]
        if scope is not None and scope.rtl:
            lo, _ = scope.rtl_focus
            if 1 <= lo <= len(scope.rtl):
                return scope.rtl[lo - 1]
        return search_row_label(entry)

    def _refresh_matches(self, rebuild: bool = False) -> None:
        """Show/hide and materialise the bottom matched-lines pane.

        `rebuild` re-creates the ListItems; the pane only exists once the
        query is committed (card 280), so cheap calls just keep the shared
        cursor in sync.
        """
        if not self._committed:
            self.matchlist.display = False
            return
        if rebuild:
            self.matchlist.clear()
            self.matchlist.extend(
                ListItem(Static(self._match_source(e))) for e in self._shown
            )
        self.matchlist.display = True
        if self._shown:
            self.matchlist.index = min(
                self._cursor - self._wstart, len(self._shown) - 1
            )

    def _paint_panes(self) -> None:
        """Lift the active pane (results or matches) above the other."""
        for widget, pid in (
            (self.listwidget, "results"),
            (self.matchlist, "matches"),
        ):
            active = pid == self._pane
            widget.styles.border = ("solid", "#a6c9ff" if active else "#3a3a3a")
            widget.styles.background = PANE_BG_ACTIVE if active else PANE_BG_IDLE

    def _render_preview(self) -> None:
        if not self._shown:
            self.preview.update("")
            self._preview_entry = None
            return
        entry = self._shown[self._cursor - self._wstart]
        if entry is self._preview_entry:
            return
        self._preview_entry = entry
        focus, scope = self._app._entry_preview(entry)  # type: ignore[attr-defined]
        offset = (
            self._app._preview_offset(focus, self.PREVIEW_ROWS)  # type: ignore[attr-defined]
            if focus is not None
            else 0
        )
        self.preview.update(self._app._render_source(focus, scope, offset=offset))


class TrenApp(App[None]):
    TITLE = "tren (textual)"
    BINDINGS = [
        Binding("1", "select_pane_tree", "tree", priority=True),
        Binding("2", "select_pane_source", "source", priority=True),
        Binding("3", "select_pane_ports", "ports", priority=True),
        Binding("z", "toggle_zoom", "zoom", priority=True),
        Binding("j", "next_item", "down", priority=True),
        Binding("k", "prev_item", "up", priority=True),
        Binding("l", "expand", "expand", priority=True),
        Binding("h", "collapse", "collapse", priority=True),
        # page up/down: editor-style, a screenful of the active pane
        # (card 283). priority, like j/k: they must win even though nothing
        # on the main screen holds focus. Under a trace/search modal the
        # app must NOT page the tree behind it, so on_event stops them.
        Binding("pageup", "prev_page", "page up", priority=True),
        Binding("pagedown", "next_page", "page down", priority=True),
        # arrow aliases of j/k/l/h (card 267). NOT priority, and that is the
        # point: the trace modal's ListView owns up/down for its own list, and
        # the modal binds left/right itself. A priority alias here would take
        # all four away from the modal, exactly as j/k/l/h are taken.
        Binding("down", "next_item", "down", show=False),
        Binding("up", "prev_item", "up", show=False),
        Binding("right", "expand", "expand", show=False),
        Binding("left", "collapse", "collapse", show=False),
        Binding("enter", "focus_source", "show rtl"),  # NOT priority: the d/L
        # modal's ListView owns Enter while the trace is open
        Binding("ctrl+u", "go_up_level", "up level", priority=True),
        Binding("/", "open_search", "search", priority=True),
        Binding("y", "yank", "yank", priority=True),
        Binding("Y", "cycle_format", "fmt", priority=True),
        Binding("d", "trace_drivers", "drivers", priority=True),
        Binding("L", "trace_loads", "loads", priority=True),
        Binding("X", "toggle_cut", "cut", priority=True),
        Binding(";", "dump_trace", "dump", priority=True),
        Binding("e", "open_editor", "editor inst", priority=True),
        Binding("E", "open_module_definition", "editor def", priority=True),
        Binding("?", "show_cheatsheet", "cheatsheet", priority=True),
        Binding("q", "quit", "quit", priority=True),
    ]

    def __init__(self, design: Design, root: Path | None = None) -> None:
        super().__init__()
        self.design = design
        self.root = Path.cwd() if root is None else Path(root)
        self.yanked: list[str] = []
        self.yank_format: PathFormat = DEFAULT
        self.osc_events: list[str] = []
        self.active = "tree"  # which pane j/k/l/h/y act on: tree | source | ports
        self._cue: str | None = None  # pane the background cue is painted for
        self.zoomed = False  # z: the active pane fills the screen (card 268)
        self.src_offset = 0
        self._source_rows = 30  # last known source-pane height (see _follow)
        self.port_index = 0
        self.pane_ports: list[Port] = []
        self._scopes = {s.name: s for s in design.scopes}
        self._focus: tuple[str, int, int] | None = None
        # (focus, scope name) the source pane is currently scrolled for
        self._render_target: tuple[tuple[str, int, int] | None, str | None] | None = None
        self._file_cache: dict[str, list[str]] = {}
        self.editor_target: str | None = None
        # trace stack (scope_path, net_name); d/L populates it; ; dumps
        self._trace: list[tuple[str, str]] = []
        self._trace_dir = "drivers"
        self._modal: "TraceScreen | None" = None
        self.trace_dump: str = ""
        # user cuts (X): scope paths made opaque at runtime
        self._user_cut: set[str] = set()
        # the ? cheatsheet when it is on screen (card 248)
        self._cheat: "CheatSheet | None" = None
        # the / global search when it is on screen (card 244)
        self._search: "SearchScreen | None" = None
        self._search_index = build_search_index(design)

    def compose(self) -> ComposeResult:
        yield StatusBar("", id="status")
        with Horizontal():
            # auto_expand=False: textual's expand-on-select is an *async
            # message handler that toggles* — a key burst (autorepeat /
            # scripted paste) re-orders it against our visible-walk. TREN
            # expands deterministically in _select instead.
            yield Tree("design", data=None, id="tree")
            yield SourcePane("", id="source")
            yield PortsPane("", id="ports")
        yield Footer()

    def on_mount(self) -> None:
        self._tree_widget = self.query_one("#tree")
        # The tree is not focusable: textual auto-focuses the first focusable
        # widget on mount, which would shadow the app-level (non-priority)
        # Enter binding with the Tree's own. TREN drives all tree input via
        # priority=True app bindings, so this loses nothing (falling: the
        # d/L modal focuses its ListView and keeps Enter for boundary
        # crossing).
        self._tree_widget.can_focus = False
        # ...but can_focus=False does NOT blur a widget that is already
        # focused, and on a real terminal textual has auto-focused the Tree by
        # the time on_mount runs — verified with tmux: has_focus=True,
        # can_focus=False, pseudo-classes carrying `focus`. Headless Pilot
        # reports focused=None, so the drives (layer 3) cannot see this. The
        # visible symptom was `Tree:focus { background-tint: $foreground 5% }`
        # lifting the whole tree column ~4% above the other panes, so an idle
        # tree still read as highlighted. Blur explicitly.
        self.set_focus(None)
        # textual's select-time auto-expand runs in an async handler that
        # *toggles*: under a key burst it re-orders against our visible-walk
        # (and pre-expanding here would make it collapse). Disable it and
        # expand deterministically in _select.
        self._tree_widget.auto_expand = False
        self._source_widget = self.query_one("#source")
        self._ports_widget = self.query_one("#ports")
        self._status_widget = self.query_one("#status")
        self._status_widget.styles.height = 1
        self._status_widget.styles.background = STATUS_BG
        self._index = -1
        self._current_node = None
        # three columns: tree / source / ports; panes fill the viewport so
        # size.height is stable (not content-driven) for auto-follow
        for _, widget in self._panes():
            widget.styles.height = "1fr"
        self._apply_layout()

        roots = [s for s in self.design.scopes if _scope_parent(s.name) not in self._scopes]
        for scope in roots:
            self._add_scope_node(self._tree_widget.root, scope)
        # textual only renders children of expanded nodes. The root is never
        # expanded by scripts and our _visible() walk reads root.children
        # directly (root.is_expanded ignored), so app state stayed right but
        # a real terminal showed nothing under the bare "design" line.
        self._tree_widget.root.expand()
        self._render_panes()

    # ---- tree construction ------------------------------------------------
        # Scopes are populated LAZILY: a node is a stub with allow_expand=True
        # until _ensure_children runs (on select/expand/visible-walk). Only the
        # handful of roots exist at mount — the graph is never materialized.

    def _add_scope_node(
        self, parent: Tree.Node, scope: Scope, label: str | None = None
    ) -> Tree.Node:
        base_label = label or f"{scope.name}/"
        return parent.add(
            base_label,
            data={"kind": "scope", "scope": scope, "label": base_label},
            allow_expand=True,
        )

    def _ensure_children(self, node: Tree.Node) -> None:
        """Build a node's children idempotently (lazy populate on demand)."""
        data = node.data
        if data is None or node.children:
            return
        kind = data.get("kind")
        if kind == "scope":
            scope = data["scope"]
            for inst in scope.instances:
                child = self._scopes.get(f"{scope.name}.{inst.name}")
                if child is not None:
                    # the instance is a real sub-scope (elaborated IR): nest it
                    marker = "!" if inst.blackbox else ""
                    self._add_scope_node(node, child, f"{inst.name}/{marker}")
                else:
                    # fixture shape: the instance's module scope supplies ports
                    inst_node = node.add(
                        f"{inst.name}/",
                        data={"kind": "instance", "instance": inst},
                        allow_expand=True,
                    )
                    inst_scope = self._scopes.get(inst.module) if inst.module else None
                    if inst_scope is not None:
                        self._add_ports_nets(inst_node, inst_scope)
            self._add_ports_nets(node, scope)
        elif kind == "instance":
            inst = data.get("instance")
            inst_scope = self._scopes.get(inst.module) if inst else None
            if inst_scope is not None:
                self._add_ports_nets(node, inst_scope)

    def _add_ports_nets(self, node: Tree.Node, scope: Scope) -> None:
        for port in scope.ports:
            node.add_leaf(
                port_leaf_label(port),
                data={"kind": "port", "port": port},
            )
        for net in scope.nets:
            node.add_leaf(
                net_leaf_label(net),
                data={"kind": "net", "net": net},
            )

    # ---- model accessors -------------------------------------------------

    def _tree(self) -> Tree:
        return self._tree_widget

    def _visible(self) -> list[Tree.Node]:
        def walk(node: Tree.Node):
            for child in node.children:
                yield child
                if child.is_expanded:
                    self._ensure_children(child)  # lazy: materialize on demand
                    yield from walk(child)

        return list(walk(self._tree().root))

    def _scope_data(self, node: Tree.Node | None) -> Scope | None:
        """Resolve the Scope a tree node *means* (its ports/rtl)."""
        node = node or self._current_node
        while node is not None and node.data is not None:
            data = node.data
            if data.get("kind") == "scope":
                return data["scope"]
            inst = data.get("instance")
            if inst is not None and inst.module is not None:
                return self._scopes.get(inst.module)
            node = node.parent
        return None

    def _focus_for(self, node: Tree.Node | None) -> tuple[str, int, int] | None:
        """Source focus of a node: the module's instantiation in its parent.

        A port or net leaf focuses its own *declaration* (cards 242/243):
        Port.src comes from slang --ast-json, Net.src from the yosys netname
        attributes. Either may be missing (black boxes, synthesised nets), in
        which case the walk continues up and the module-level focus stands.

        An instance node carries its own src (the instantiation site). A
        sub-scope node focuses its instantiation in the parent (the parent's
        instance of it); a root scope focuses its module declaration.
        """
        node = node or self._current_node
        while node is not None and node.data is not None:
            data = node.data
            kind = data.get("kind")
            if kind in ("port", "net"):
                declaration = _parse_src(data[kind].src)
                if declaration is not None:
                    return declaration
            if data.get("kind") == "instance":
                return _parse_src(data["instance"].src)
            if data.get("kind") == "scope":
                scope = data["scope"]
                parent = _scope_parent(scope.name)
                if parent in self._scopes:
                    for inst in self._scopes[parent].instances:
                        if f"{parent}.{inst.name}" == scope.name:
                            return _parse_src(inst.src)
                return _parse_src(scope.src)
            node = node.parent
        return None

    def _effective_focus(self) -> tuple[str, int, int] | None:
        """Source focus of the *effective* selection (card 281).

        With the I/O pane active the selection is the port its marker sits
        on, so the focus is that port's declaration - the same rule a port
        leaf in the tree follows (cards 242/243). A port the backends gave
        no src, an out-of-range index and every other pane fall back to the
        tree selection's focus. While a modal screen owns the screen (the
        d/L trace, the search finder) the tree selection stays the truth:
        those modals move the tree rows and the centre pane must follow
        them, whatever pane was active when they opened.
        """
        if (
            self.active == "ports"
            and self._modal is None
            and self._search is None
            and self.pane_ports
            and 0 <= self.port_index < len(self.pane_ports)
        ):
            declared = _parse_src(self.pane_ports[self.port_index].src)
            if declared is not None:
                return declared
        return self._focus_for(self._current_node)

    def _file_lines(self, file: str) -> list[str]:
        if file not in self._file_cache:
            p = Path(file)
            if not p.is_absolute():
                p = self.root / file
            try:
                self._file_cache[file] = p.read_text().splitlines()
            except OSError:
                self._file_cache[file] = []
        return self._file_cache[file]

    def _follow(self, focus: tuple[str, int, int]) -> None:
        # floor the viewport: early renders report a tiny/zero height, and a
        # pane hidden by the zoom (card 268) reports zero for as long as it
        # stays hidden — keep the last real height so the band is centred for
        # the viewport the pane gets back.
        height = self._source_widget.size.height
        if height:
            self._source_rows = height
        rows = max(10, self._source_rows)
        file, lo, _ = focus
        lines = self._file_lines(file)
        max_offset = max(0, len(lines) - rows)
        self.src_offset = max(0, min(lo - rows // 2, max_offset))

    def _select(self, index: int) -> None:
        nodes = self._visible()
        if not nodes:
            return
        index %= len(nodes)
        node = nodes[index]
        self._index = index
        self._current_node = node
        self._tree().select_node(node)
        # No auto-expand on move: j/k only walks the rows that are already
        # visible; l (action_expand) is the only thing that unfolds a scope
        # (file-manager model). Programmatic jumps (_select_path, ctrl+u)
        # expand/populate explicitly where they need to.
        self._tree().scroll_to_node(node)
        self._render_panes()

    def _render_source(
        self, focus: tuple[str, int, int] | None, scope: Scope | None, offset: int | None = None
    ) -> RichText:
        """Render the source view.

        offset overrides the pane scroll: the search preview (card 244) passes
        a per-entry offset so browsing results never disturbs the pane state.
        """
        off = self.src_offset if offset is None else offset
        src = RichText()
        if focus is not None:
            file, lo, hi = focus
            lines = self._file_lines(file)
            if lines:
                src.append(f"· {file}  lines {lo}-{hi}\n", style="dim")
                view = lines[off :]
                if off:
                    src.append("…… (↑ scroll to see earlier lines)\n", style="dim italic")
                for i, line in enumerate(view):
                    abs_i = off + i + 1
                    style = "bold #ffa62b" if lo <= abs_i <= hi else "dim white"
                    src.append(f"{line}\n", style=style)
                return src
            if scope is None or not scope.rtl:
                src.append(f"(cannot read {file})\n", style="dim italic")
                return src
            # the file is gone but the scope carries an inline snippet: prefer
            # showing RTL over an error. Reachable since port/net leaves began
            # resolving their own src (cards 242/243) — fixture nets carry a
            # src pointing at a file that need not be checked out.
        if scope is not None:
            # fixture fallback: inline rtl snippet with the rtl_focus band
            rtl = scope.rtl
            lo, hi = scope.rtl_focus
            view = rtl[off :]
            if off:
                src.append("…… (↑ scroll to see earlier lines)\n", style="dim italic")
            for i, line in enumerate(view):
                abs_i = i + 1 + off
                style = "bold #ffa62b" if lo <= abs_i <= hi else "dim white"
                src.append(f"{line}\n", style=style)
        return src

    def _render_panes(self) -> None:
        scope = self._scope_data(self._current_node)
        focus = self._effective_focus()
        # What the centre pane is showing: a file span, or (fixture IR) the
        # scope's inline snippet. Scroll position belongs to *that* target, so
        # compare both — focus alone stays None across a move between two
        # src-less scopes and used to carry the previous scope's scroll offset
        # into an unrelated snippet. Within one target (j in the source pane)
        # nothing is reset. The focus is the effective selection's (card 281):
        # the I/O pane's selected port while that pane is active, so moving
        # its marker re-centres the declaration like a tree leaf does.
        target = (focus, scope.name if scope is not None else None)
        if target != self._render_target:
            self._render_target = target
            self._focus = focus
            if focus is not None:
                self._follow(focus)
            else:
                self.src_offset = 0  # inline RTL view starts at the top
        self._source_widget.update(self._render_source(focus, scope))
        # right: I/O list with selection
        ports = RichText()
        self.pane_ports = list(scope.ports) if scope is not None else []
        if scope is not None:
            # params first: resolved value, declared default, override mark
            for p in scope.params:
                over = p.overridden
                ports.append(f"  {'✎' if over else '·'} {p.name} = {p.value}",
                             style=("bold #ffa62b" if over else "#a6c9ff"))
                if p.default is not None:
                    ports.append(f"  [default {p.default}]", style="dim white")
                ports.append("\n", style="")
            for n, p in enumerate(self.pane_ports):
                direction = p.direction
                base = "#7adf77" if direction == "out" else "#a6c9ff"
                sel = "▸ " if n == self.port_index else "  "
                color = f"bold {base}" if n == self.port_index else base
                ports.append(f"{sel}{p.name}", style=color)
                type_str = f"  {p.type}" if p.type else ""
                ports.append(f"{type_str}  {direction}[{p.width}]\n", style="dim white")
        # black-box / user-cut provenance header on the I/O pane
        if scope is not None:
            header = RichText()
            if scope.blackbox:
                header.append("▊ BLACK BOX\n", style="bold yellow")
            if scope.name in self._user_cut:
                header.append("✂ USER CUT\n", style="bold red")
            ports = header + ports
        self._ports_widget.update(ports)
        self._status_widget.update(self._selection_status())
        self._render_pane_cue()

    def _panes(self) -> tuple[tuple[str, Widget], ...]:
        """The three panes in screen order, keyed by the name `active` uses."""
        return (
            ("tree", self._tree_widget),
            ("source", self._source_widget),
            ("ports", self._ports_widget),
        )

    def _apply_layout(self) -> None:
        """Size the panes for the current zoom state (card 268).

        Zoomed, the active pane takes the full width and the other two are
        undisplayed: a hidden widget claims no space, so a plain width change
        is not enough. Unzoomed, every pane returns to its share of the row.
        """
        for pid, widget in self._panes():
            active = pid == self.active
            widget.display = active or not self.zoomed
            widget.styles.width = ZOOM_WIDTH if self.zoomed else PANE_WIDTHS[pid]

    def _render_pane_cue(self) -> None:
        """Repaint the active-pane background cue (no-op unless it changed)."""
        if self._cue == self.active:
            return
        self._cue = self.active
        for pid, widget in self._panes():
            widget.styles.background = PANE_BG_ACTIVE if pid == self.active else PANE_BG_IDLE
        # textual 8.2 Tree caches rendered line *strips* keyed on
        # (line, cursor, hover, width, ...) — the background is not part of the
        # key, so a cue change repainted only the blank region below the nodes
        # and every line carrying a node kept the old (active) background. Drop
        # the cache so the tree re-renders at the new background; the Static
        # panes need no equivalent. `_invalidate()` would also do this but
        # resets the node tree and re-layouts, which is not needed for a
        # repaint.
        self._tree_widget._clear_line_cache()
        self._tree_widget.refresh()

    # ---- trace (drivers/loads, hierarchy crossing) ------------------------

    def _selection_net(self) -> tuple[str, str] | None:
        """(scope_path, net_name) of the current tree selection, else None."""
        node = self._current_node
        while node is not None and node.data is not None:
            data = node.data
            if data.get("kind") == "net":
                scope = self._scope_data(node)
                if scope is not None:
                    return scope.name, data["net"].name
                return None
            node = node.parent
        return None

    def _trace_rows(self) -> list[dict]:
        """Driver/load rows of the trace head for the modal."""
        scope_path, net_name = self._trace[-1]
        scope = self._scopes.get(scope_path)
        if scope is None:
            return []
        conn = next(
            (c for c in self.design.connectivity if c.scope == scope.module and c.net == net_name),
            None,
        )
        if conn is None:
            return []
        pairs: list[tuple[str, str]] = []
        for bit in conn.bits:
            eps = bit.drivers if self._trace_dir == "drivers" else bit.loads
            pairs.extend((e.cell, e.port) for e in eps)
        seen: set[tuple[str, str]] = set()
        rows: list[dict] = []
        for cell, port in pairs:
            if (cell, port) in seen:
                continue
            seen.add((cell, port))
            child = f"{scope_path}.{cell}"
            row = {"cell": cell, "port": port, "kind": "leaf", "label": f"{cell}.{port}"}
            if child in self._scopes:
                child_scope = self._scopes[child]
                if child_scope.blackbox:
                    row["kind"] = "box"
                    row["label"] = f"{cell}.{port}  ⛔ black box ({child_scope.module})"
                elif child in self._user_cut:
                    row["kind"] = "box"
                    row["label"] = f"{cell}.{port}  ⛔ user cut"
                else:
                    row["kind"] = "boundary"
                    row["child"] = child
                    row["label"] = f"{cell}.{port}  → {child_scope.module}"
            rows.append(row)
        return rows

    def _trace_descend(self, row: dict) -> bool:
        """Cross a boundary driver/load into the child scope's port net."""
        if row.get("kind") != "boundary":
            return False
        child, net_name = row["child"], row["port"]
        self._trace.append((child, net_name))
        self._select_path(child, net_name)
        return True

    def _trace_pop(self) -> bool:
        if len(self._trace) <= 1:
            return False
        self._trace.pop()
        self._select_path(*self._trace[-1])
        return True

    def _find_scope_node(self, path: str) -> Tree.Node | None:
        """Descend by path segments so only the touched lineage materializes."""
        parts = path.split(".")
        node = self._tree().root
        for i in range(1, len(parts) + 1):
            prefix = ".".join(parts[:i])
            self._ensure_children(node)
            match = None
            for child in node.children:
                data = child.data
                if (
                    data is not None
                    and data.get("kind") == "scope"
                    and data["scope"].name == prefix
                ):
                    match = child
                    break
            if match is None:
                return None
            node = match
        return node

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        self._ensure_children(event.node)

    def _find_net_leaf(self, scope_node: Tree.Node, net_name: str) -> Tree.Node | None:
        self._ensure_children(scope_node)  # final node of the descend above is unbuilt
        stack = list(scope_node.children)
        while stack:
            n = stack.pop()
            d = n.data
            if d is not None and d.get("kind") == "net" and d["net"].name == net_name:
                return n
            stack.extend(n.children)
        return None

    def _expand_to(self, node: Tree.Node) -> None:
        chain: list[Tree.Node] = []
        n = node
        while n is not self._tree().root:
            chain.append(n)
            n = n.parent
        for n in reversed(chain):
            n.expand()

    def _select_path(self, scope_path: str, net_name: str | None = None) -> bool:
        """Select the tree row for a scope (or one of its nets); True on hit.

        The search finder (card 244) keys on the return value: False means
        the scope has no tree row and the caller tries a fixture fallback.
        """
        scope_node = self._find_scope_node(scope_path)
        if scope_node is None:
            return False
        self._expand_to(scope_node)
        if net_name is not None:
            leaf = self._find_net_leaf(scope_node, net_name)
            if leaf is not None:
                self._expand_to(leaf)
                self._select(self._visible().index(leaf))
                return True
        self._select(self._visible().index(scope_node))
        return True

    # ---- global search: jump + preview helpers (card 244) ---------------

    def _select_instance_row(self, parent_path: str, inst_name: str) -> bool:
        """Select an instance row under a scope row (the fixture shape: the
        instance has no sub-scope node of its own)."""
        node = self._find_scope_node(parent_path)
        if node is None:
            return False
        self._ensure_children(node)
        self._expand_to(node)
        for child in node.children:
            data = child.data
            if (
                data is not None
                and data.get("kind") == "instance"
                and data["instance"].name == inst_name
            ):
                self._expand_to(child)
                self._select(self._visible().index(child))
                return True
        return False

    def _select_any_instance_of(self, module: str) -> bool:
        """Fixture module with no tree row: select the first instance row of
        that module anywhere in the tree."""
        for scope in self.design.scopes:
            for inst in scope.instances:
                if (
                    inst.module == module
                    and self._select_instance_row(scope.name, inst.name)
                ):
                    return True
        return False

    def _select_leaf(self, scope_path: str, kind: str, name: str) -> bool:
        """Select a port/net leaf, in either tree shape.

        A real scope row carries its own leaves; a fixture module (no row)
        hangs them under each instance row that instantiates the module.
        Only direct children count, so a nested scope's same-named leaf is
        never picked instead of the scope's own.
        """
        scope_node = self._find_scope_node(scope_path)
        if scope_node is not None:
            self._ensure_children(scope_node)
            self._expand_to(scope_node)
            for child in scope_node.children:
                data = child.data
                if (
                    data is not None
                    and data.get("kind") == kind
                    and data[kind].name == name
                ):
                    self._expand_to(child)
                    self._select(self._visible().index(child))
                    return True
        for scope in self.design.scopes:
            node = self._find_scope_node(scope.name)
            if node is None:
                continue
            self._ensure_children(node)
            for child in node.children:
                data = child.data
                if (
                    data is not None
                    and data.get("kind") == "instance"
                    and data["instance"].module == scope_path
                ):
                    self._ensure_children(child)
                    for leaf in child.children:
                        leaf_data = leaf.data
                        if (
                            leaf_data is not None
                            and leaf_data.get("kind") == kind
                            and leaf_data[kind].name == name
                        ):
                            self._expand_to(leaf)
                            self._select(self._visible().index(leaf))
                            return True
        return False

    def _select_entry(self, entry: SearchEntry) -> bool:
        """Select the tree row a search result means; True on jump.

        A fixture module has no row of its own (the tree is instance-keyed),
        so module and port/net entries fall back to an instance row of the
        module.
        """
        kind = entry.kind
        if kind == "module":
            if self._select_path(entry.path):
                return True
            scope = entry.scope
            if scope is None:
                return False
            return self._select_any_instance_of(scope.module or scope.name)
        if kind == "instance":
            if self._select_path(entry.path):
                return True
            inst = entry.instance
            if inst is not None and entry.scope is not None:
                return self._select_instance_row(entry.scope.name, inst.name)
            return False
        if kind in ("port", "net"):
            scope = entry.scope
            return self._select_leaf(scope.name if scope is not None else entry.path, kind, entry.name)
        if kind == "file":
            scope = entry.scope
            if scope is None:
                return False
            if self._select_path(scope.name):
                return True
            return self._select_any_instance_of(scope.module or scope.name)
        return False

    def _entry_preview(
        self, entry: SearchEntry
    ) -> tuple[tuple[str, int, int] | None, Scope | None]:
        """(focus, scope) the source pane would show for a search result."""
        if entry.kind == "module":
            return _parse_src(entry.src), entry.scope
        if entry.kind == "instance":
            inst = entry.instance
            if inst is not None:
                return _parse_src(inst.src or entry.src), (
                    self._scopes.get(inst.module) if inst.module else entry.scope
                )
            return _parse_src(entry.src), entry.scope
        if entry.kind in ("port", "net", "file"):
            return _parse_src(entry.src), entry.scope
        return None, None

    def _preview_offset(self, focus: tuple[str, int, int], rows: int) -> int:
        """Scroll offset for the search preview, centring the focus line."""
        file, lo, _ = focus
        lines = self._file_lines(file)
        return max(0, min(lo - rows // 2, max(0, len(lines) - rows)))

    def _open_trace(self, direction: str) -> None:
        if self._modal is not None or self._search is not None:
            self.bell()
            return
        sel = self._selection_net()
        if sel is None:
            self.bell()
            return
        self._trace = [sel]
        self._trace_dir = direction
        self.push_screen(TraceScreen())

    def action_trace_drivers(self) -> None:
        self._open_trace("drivers")

    def action_trace_loads(self) -> None:
        self._open_trace("loads")

    def action_dump_trace(self) -> None:
        self.trace_dump = " ▶ ".join(f"{s}.{n}" for s, n in self._trace)
        self.bell()

    def action_toggle_cut(self) -> None:
        scope = self._scope_data(self._current_node)
        if scope is None:
            self.bell()
            return
        if scope.name in self._user_cut:
            self._user_cut.discard(scope.name)
        else:
            self._user_cut.add(scope.name)
        node = self._find_scope_node(scope.name)
        if node is not None:
            base = node.data["label"] if node.data is not None else f"{scope.name}/"
            node.label = base + ("✂" if scope.name in self._user_cut else "")
        self._render_panes()

    # ---- actions ----------------------------------------------------------

    def _select_pane(self, pane: str) -> None:
        """Make a pane active. Zoomed, the zoom moves with it (card 268)."""
        self.active = pane
        if self.zoomed:
            self._apply_layout()
        self._render_panes()

    def action_select_pane_tree(self) -> None:
        self._select_pane("tree")

    def action_select_pane_source(self) -> None:
        self._select_pane("source")

    def action_select_pane_ports(self) -> None:
        self._select_pane("ports")

    def action_toggle_zoom(self) -> None:
        """z: zoom the active pane to the full width, or restore the columns.

        The trace modal and the search modal cover the panes, so a zoom
        there changes nothing the user can see. Refuse it instead of leaving
        a surprise behind the modal.
        """
        if self._modal is not None or self._search is not None:
            self.bell()
            return
        self.zoomed = not self.zoomed
        self._apply_layout()
        self._render_panes()

    def action_show_cheatsheet(self) -> None:
        """?: open the navigation cheatsheet (full key map, card 248).

        The sheet lists the trace-modal keys too when a trace is open. It is
        also reachable from inside the trace modal, where the trace is still
        mounted underneath. A second open is refused: on_event pops the sheet
        on the `?` key instead of reopening it.
        """
        if self._cheat is not None:
            return
        self.push_screen(CheatSheet(include_trace=self._modal is not None))

    def action_open_search(self) -> None:
        """/: open the global search over modules/instances/ports/nets/files.

        Refused while the trace modal is open, the mirror of the trace modal
        refusing to open under it: the finder needs the full screen and the
        search modal's Input owns the printable keys.
        """
        if self._modal is not None or self._search is not None:
            self.bell()
            return
        self.push_screen(SearchScreen())

    async def on_event(self, event: events.Event) -> None:
        """The cheatsheet yields to any key: dismiss it, then dispatch on.

        Every nav key is an app-level binding, so textual would run it while
        the sheet stays up. Pop the sheet first, then let the same key reach
        the layer below (the panes, or the trace modal it was opened from).
        escape and `?` only dismiss: escape must not close the trace modal
        underneath too, and `?` must not reopen the sheet.

        A *committed* search blurs the query box, so no widget guards the
        app's priority printable bindings (q quit, y yank, X cut, ...).
        Stop every key except the finder's own there, or it would act on the
        tree behind the modal. '/' is the one exception, intercepted here
        before the app's priority open_search can steal it: the finder is
        already open, so '/' returns to the query box instead (card 279).
        """
        if isinstance(event, events.Key) and not event.is_forwarded:
            if self._cheat is not None and self.screen is self._cheat:
                # event.name is the normalized key ('question_mark' for '?')
                key = event.name
                self.pop_screen()
                self._cheat = None
                if key in ("escape", "question_mark"):
                    event.stop()
                    return
            search = self._search
            if search is not None and getattr(search, "_committed", False):
                if event.key == "slash":
                    search.action_search_edit_query()
                    event.stop()
                    return
                if event.key not in SEARCH_COMMITTED_KEYS:
                    event.stop()
                    return
            # page up/down are priority app bindings. Under a trace or search
            # modal the list owns up/down, but neither ListView binds page
            # keys, so the app's page actions would fire and move the tree
            # behind the modal. Stop them (card 283); the cheatsheet above
            # already popped itself and lets the pane take the page key.
            if (self._modal is not None or self._search is not None) and (
                event.key in ("pageup", "pagedown")
            ):
                event.stop()
                return
        await super().on_event(event)

    def action_next_item(self) -> None:
        if self.active == "ports":
            if self.pane_ports:
                self.port_index = (self.port_index + 1) % len(self.pane_ports)
            self._render_panes()
        elif self.active == "source":
            self.src_offset += 1
            self._render_panes()
        else:
            self._select(getattr(self, "_index", -1) + 1)

    def action_prev_item(self) -> None:
        if self.active == "ports":
            if self.pane_ports:
                self.port_index = (self.port_index - 1) % len(self.pane_ports)
            self._render_panes()
        elif self.active == "source":
            self.src_offset = max(0, self.src_offset - 1)
            self._render_panes()
        else:
            self._select(getattr(self, "_index", 1) - 1)

    def _page_rows(self) -> int:
        """Rows a page move covers: the active pane's visible height minus one.

        Editors keep one line of context across a page flip; so does this.
        A hidden pane (the zoom, card 268) reports zero, so floor the page
        at 1 rather than freezing the move.
        """
        if self.active == "tree":
            height = self._tree_widget.size.height
        elif self.active == "ports":
            height = self._ports_widget.size.height
        else:
            height = self._source_widget.size.height or self._source_rows
        return max(1, height - 1)

    def _source_max_offset(self, height: int) -> int:
        """Last scroll offset that still leaves a full page of lines visible."""
        focus = self._effective_focus()
        if focus is not None:
            lines = self._file_lines(focus[0])
            if lines:
                return max(0, len(lines) - height)
        scope = self._scope_data(self._current_node)
        if scope is not None and scope.rtl:
            return max(0, len(scope.rtl) - height)
        return 0

    def action_next_page(self) -> None:
        """pagedown: a screenful of the active pane, editor-style (card 283).

        In the tree the cursor jumps `_page_rows` visible nodes and clamps at
        the last one; in source the view scrolls by the pane height and
        clamps at the bottom of the file; in I/O the port marker moves by the
        same amount and wraps, as j/k do.
        """
        if self.active == "ports":
            if self.pane_ports:
                rows = self._page_rows()
                self.port_index = (self.port_index + rows) % len(self.pane_ports)
            self._render_panes()
        elif self.active == "source":
            rows = self._page_rows()
            self.src_offset = min(self.src_offset + rows, self._source_max_offset(rows))
            self._render_panes()
        else:
            nodes = self._visible()
            if not nodes:
                return
            self._select(min(getattr(self, "_index", -1) + self._page_rows(), len(nodes) - 1))

    def action_prev_page(self) -> None:
        """pageup: a screenful back of the active pane, editor-style (283)."""
        if self.active == "ports":
            if self.pane_ports:
                rows = self._page_rows()
                self.port_index = (self.port_index - rows) % len(self.pane_ports)
            self._render_panes()
        elif self.active == "source":
            self.src_offset = max(0, self.src_offset - self._page_rows())
            self._render_panes()
        else:
            self._select(max(0, getattr(self, "_index", 1) - self._page_rows()))

    def action_expand(self) -> None:
        """l: expand the highlighted scope (reveal its children)."""
        node = getattr(self, "_current_node", None)
        if node is None:
            return
        self._expand_to(node)
        self._ensure_children(node)  # lazy children must exist before render
        self._tree().scroll_to_node(node)
        self._render_panes()

    def action_collapse(self) -> None:
        """h: collapse the highlighted scope (hide its children)."""
        node = getattr(self, "_current_node", None)
        if node is None:
            return
        if node.is_expanded and node.children:
            node.collapse()
        self._render_panes()

    def action_go_up_level(self) -> None:
        """ctrl+u: step up one *granularity* level, current scope first.

        1. From a port/net leaf (or an instance's context), jumps to the
           scope it belongs to — the current hierarchy's own definition —
           NOT past it to the parent.
        2. From a scope row, selects the nearest strict *module* ancestor
           (a generate block is transparent): g[0].u_alu_g -> top.u_core.
        From a top-level scope it stays put (no wrap). Flattened fixture
        scopes (module-keyed) jump to the module's own node.
        """
        scope = self._scope_data(self._current_node)
        if scope is None:
            self.bell()
            return
        cur = self._current_node
        at_scope = (
            cur is not None
            and cur.data is not None
            and cur.data.get("kind") == "scope"
            and cur.data.get("scope") is scope
        )
        if not at_scope:
            # promoted from a leaf/instance: select the current hierarchy
            node = self._find_scope_node(scope.name)
            if node is not None:
                self._select(self._visible().index(node))
            return
        parts = scope.name.split(".")
        for i in range(len(parts) - 1, 0, -1):
            ancestor = ".".join(parts[:i])
            s = self._scopes.get(ancestor)
            if s is not None and s.module is not None:
                node = self._find_scope_node(ancestor)
                if node is not None:
                    self._select(self._visible().index(node))
                return
        # no strict module ancestor (top-level scope): stay put

    def action_focus_source(self) -> None:
        """enter: show the effective selection in the centre RTL pane, band
        centred with surrounding context - the tree's highlighted module, or
        the I/O pane's selected port's declaration (card 281). The d/L trace
        modal owns Enter while open (this binding is deliberately not
        priority=True)."""
        if self._modal is not None:
            return
        focus = self._effective_focus()
        scope = self._scope_data(self._current_node)
        if focus is None and scope is None:
            self.bell()
            return
        if focus is not None:
            self._focus = focus
            self._follow(focus)
        self._render_panes()  # fixture scopes have no src: re-render the inline RTL

    def _selection_parts(self) -> tuple[list[str], str | None]:
        """(scope chain segments, element) of the current selection.

        Scope nodes yank their own path; instance/port/net nodes yank the
        chain of the scope they sit under plus their own name.
        """
        node = getattr(self, "_current_node", None)
        if node is None:
            return [], None
        while node is not None and node.data is not None:
            data = node.data
            if data.get("kind") == "scope":
                return data["scope"].name.split("."), None
            if data.get("kind") == "instance":
                parent = self._scope_data(node)
                chain = parent.name.split(".") if parent is not None else []
                return chain + [data["instance"].name], None
            if data.get("kind") == "net":
                return self._selection_scope_chain(node), data["net"].name
            if data.get("kind") == "port":
                return self._selection_scope_chain(node), data["port"].name
            node = node.parent
        return [], None

    def _selection_scope_chain(self, node) -> list[str]:
        scope = self._scope_data(node)
        return scope.name.split(".") if scope is not None else []

    def _status_chain(self, node) -> list[str]:
        """Tree-lineage segments of a selection: [top, u_core] etc.

        Unlike `_selection_parts` (the yank path, which keys off the *module*
        scope a row stands for), the status line is the selection's full
        *hierarchical* path — so an instance row (fixture UIs, unresolved
        black boxes) reports its place in the tree (top.u_core), not the
        module name (core). Real scopes are path-keyed anyway, so both agree
        there. Ports/nets contribute nothing; their item is appended by
        `_selection_status`.
        """
        chain: list[str] = []
        n = node
        while n is not None and n is not self._tree().root:
            data = n.data
            if data is not None:
                kind = data.get("kind")
                if kind == "scope":
                    chain.append(data["scope"].name.split(".")[-1])
                elif kind == "instance":
                    chain.append(data["instance"].name)
            n = n.parent
        chain.reverse()
        return chain

    def _selection_status(self) -> RichText:
        """The top status line: yazi-style full path of the selection (246).

        Scope/instance rows show their hierarchical path (top.u_core.g[0].
        u_alu_g); a port/net leaf appends the item name (…u_alu_g.result).
        Without a selection the design top stands in.
        """
        node = getattr(self, "_current_node", None)
        element = None
        if node is not None and node.data is not None:
            kind = node.data.get("kind")
            if kind in ("port", "net"):
                element = node.data[kind].name
        chain = self._status_chain(node)
        if not chain:
            chain = [self.design.top]
        text = RichText()
        for i, seg in enumerate(chain):
            if i:
                text.append(".", style="dim")
            last = i == len(chain) - 1
            # the terminal segment is the thing being *looked at*; brighten it
            # only when it is the whole selection (no item appended below)
            text.append(seg, style="bold #ffffff" if last and element is None else "#c9c9c9")
        if element is not None:
            text.append(f".{element}", style="bold #ffa62b")
        return text

    def _write_osc(self, payload: str) -> None:
        self.osc_events.append(payload)
        # textual 8.2.8 ships App.copy_to_clipboard — the raw OSC 52 escape
        # written straight through the driver (self.console is a _NullFile
        # sink and must NOT be used; verified). The tren_testing finding that
        # "textual has no clipboard API" predates this method.
        self.copy_to_clipboard(payload)

    def action_cycle_format(self) -> None:
        names = list(BY_NAME)
        idx = (names.index(self.yank_format.name) + 1) % len(names)
        self.yank_format = BY_NAME[names[idx]]
        self.notify(f"yank format: {self.yank_format.name}")
        self.bell()

    def action_yank(self) -> None:
        """Yank the selection through the current format via OSC 52."""
        if self.active == "ports" and self.pane_ports:
            port = self.pane_ports[self.port_index]
            scope = self._scope_data(self._current_node)
            chain = scope.name.split(".") if scope is not None else []
            payload = render_format(self.yank_format, chain, port.name)
        else:
            chain, element = self._selection_parts()
            payload = render_format(self.yank_format, chain, element)
        self.yanked.append(payload)
        self._write_osc(payload)
        self.bell()

    def _open_editor(self, focus: tuple[str, int, int] | None) -> None:
        """Suspend-and-return $EDITOR jump (+line file), shared by e and E."""
        self.editor_target = f"{focus[0]}:{focus[1]}" if focus is not None else None
        if focus is None:
            return
        driver = getattr(self, "_driver", None)
        can_suspend = driver is not None and getattr(driver, "can_suspend", False)
        if not can_suspend:
            return  # Pilot run: only the target is test-relevant
        editor = (
            os.environ.get("TREN_EDITOR")
            or os.environ.get("EDITOR")
            or os.environ.get("VISUAL")
            or ""
        ).strip()
        if not editor:
            self.bell()
            return
        file, line, _ = focus
        try:
            with self.suspend():
                subprocess.call([*shlex.split(editor), f"+{line}", file])
        except Exception:
            self.bell()

    def action_open_editor(self) -> None:
        """e: open $EDITOR at the effective selection's own declaration - for
        a scope the *instantiation* in its parent (the line you'd edit to
        rewire the port map), for a port/net leaf its own declaration
        (cards 242/243), for the I/O pane's selected port its declaration
        (card 281). A top-level module has no parent: both e and E land on
        its definition.
        """
        self._open_editor(self._effective_focus())

    def action_open_module_definition(self) -> None:
        """E: open $EDITOR at the *module definition* — the scope's own src
        (Scope.src carries the declaration span), regardless of where the
        selection sits. Leaves defer to the scope they belong to."""
        scope = self._scope_data(self._current_node)
        self._open_editor(_parse_src(scope.src) if scope is not None else None)

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        self._current_node = event.node
        self._render_panes()


def main(argv: list[str] | None = None) -> int:
    """`python -m tren.tui` and the `tren-tui` console script.

    A function rather than a bare `__main__` block so the installed package
    can expose it as an entry point (pyproject.toml § project.scripts).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        help="elaborate this manifest instead of the demo fixture",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path(".cache/tren"),
        help="IR cache directory (reused across runs; second start is instant)",
    )
    args = parser.parse_args(argv)
    if args.manifest is not None:
        root = Path.cwd()
        design = elaborate(
            Manifest.read(args.manifest),
            root,
            cache=IrCache(args.cache, root=root),
        )
    else:
        design = load_design()
    TrenApp(design).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
