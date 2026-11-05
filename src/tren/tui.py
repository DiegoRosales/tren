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

Keys: j/k move, pgup/pgdn page a screenful in the active pane,
l expand, h collapse, enter shows the selection in the centre (the module,
or the I/O pane's selected port declaration), ctrl+u jumps up a
level, 1/2/3 pane select, z zoom the active pane, n toggle the source
line numbers, y yank,
/ global search (modules, instances, ports, nets, files; enter
commits, n/N walk the matches, ctrl+enter selects and closes),
? navigation cheatsheet, e editor at the selection's declaration, E editor at
the module definition, q quit. The arrow keys are aliases: down/up = j/k,
right/left = l/h; in the trace split right descends and left goes
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
from .clipboard import ClipboardError, resolve_provider, write_tmux
from .colors import port_kind, resolve_row_colors, row_style, trace_kind
from .config import Config, ConfigError, load_config
from .elaborate import elaborate
from .formats import BY_NAME, PathFormat, render as render_format, render_parts
from .ir import (
    Cell,
    CellPin,
    Design,
    Net,
    Port,
    Scope,
    design_from_fixture,
    parse_src as _parse_src,
)
from .keys import SCREEN_KEYS, TRACE_KEYS, KeyDoc
from .manifest import Manifest
from .search import SearchEntry, build_search_index, filter_and_rank

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "demo-tree.json"

# active-pane cue: the pane 1/2/3 acts on is lifted out of the background
PANE_BG_ACTIVE = "#262626"
PANE_BG_IDLE = "#121212"
# status line background: distinctly a chrome bar, not a pane
STATUS_BG = "#1e1e1e"
# a yank marks the status line for a moment, so the user sees that the
# clipboard write happened without a permanent change to the path display.
YANKED_MARK = "  [yanked]"
YANKED_STYLE = "bold #7adf77"
YANK_FLASH_SECONDS = 1.5
# trace pane background: the split's own surface, so the trace rows read as
# a pane below the source, never as more RTL lines
TRACE_PANE_BG = "#1e1e1e"
# three-column layout (the zoom restores these widths when it ends)
PANE_WIDTHS = {"tree": "32%", "source": "48%", "ports": "20%"}
ZOOM_WIDTH = "100%"
# port leaf column: "inout" is the longest direction
PORT_DIR_WIDTH = 5
# keys the *committed* finder handles itself (event.key form). Everything
# else is stopped while it is up: the query box no longer holds focus there,
# so a printable key would otherwise fire the app's priority bindings behind
# the modal (q quit, y yank, X cut, ...). '/' is NOT here: the app's own
# priority open_search would steal it, so TrenApp.on_event intercepts it
# before dispatch and returns to the query box instead. up/down move the
# highlighted match whatever pane is active (results or source-line bottom
# pane); tab flips between those two panes. Compare event.key,
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


def _gutter_width(last_line: int) -> int:
    """Width of the line-number gutter: the digits of the last line.

    The column is sized for the whole file, not the visible window, so the
    numbers do not shift left and right while the view scrolls.
    """
    return len(str(max(1, last_line)))


# The trace split renders at most this many rows per refresh; the window
# slides to keep the cursor visible. A bound on the render keeps a
# high-fanout net (hundreds of drivers) from rebuilding the whole list on
# every keypress.
TRACE_MAX_ROWS = 200


def load_design(fixture: Path | None = None) -> Design:
    return design_from_fixture(fixture or FIXTURE)


def _scope_parent(path: str) -> str | None:
    parts = path.rsplit(".", 1)
    return parts[0] if len(parts) == 2 else None


def _bit_range(width: int, declared: str | None = None) -> str:
    """Bit range suffix for a leaf: '[msb:lsb]', empty for a scalar.

    The declared range comes from the slang overlay and reads as the source
    writes it, ``[7:1]`` and ``[0:7]`` included. A signal the
    overlay never reached carries none, and the range falls back to the
    width as ``[width-1:0]``.
    """
    if declared:
        return declared
    return f"[{width - 1}:0]" if width > 1 else ""


def port_leaf_label(port: Port) -> str:
    """Hierarchy leaf text for a port: 'port <dir> <type> <name>'.

    The type carries the declared range, so the name needs no suffix. The
    direction stays left-aligned in a fixed column. A port the overlay never
    reached has no type: the row falls back to the range suffix
    ('port in    a[31:0]') and the same direction column.
    """
    if port.type:
        return f"port {port.direction:<{PORT_DIR_WIDTH}} {port.type} {port.name}"
    suffix = _bit_range(port.width, port.range)
    return f"port {port.direction:<{PORT_DIR_WIDTH}} {port.name}{suffix}"


def net_leaf_label(net: Net) -> str:
    """Hierarchy leaf text for a net: '<type> <name>'.

    The type is the declared keyword and range ('wire [3:0]', 'reg [3:0]'),
    so the row reads as the declaration. A net the overlay never reached
    falls back to the 'net' marker and the range derived from the bit count.
    """
    if net.type:
        return f"{net.type} {net.name}"
    return f"net {net.name}{_bit_range(net.width, net.range)}"


def _net_slice(net: Net, positions: list[int]) -> str:
    """Declared bit select of the net positions a connection touches.

    ``o[31:0]`` on a descending net and ``nu[0:3]`` on an ascending one, so
    the slice reads as the source writes the range. Two netnames that carry
    the same bits alias the same wire: their rows show the same select.
    """
    if not positions or net.width < 1:
        return ""
    declared = net.range or (f"[{net.width - 1}:0]" if net.width > 1 else "")
    if not (declared.startswith("[") and declared.endswith("]")):
        return ""
    bounds = declared[1:-1].split(":")
    if len(bounds) != 2:
        return ""
    try:
        left, right = int(bounds[0]), int(bounds[1])
    except ValueError:
        return ""
    ascending = left < right
    lo, hi = min(positions), max(positions)
    # yosys lists net bits LSB first, and the right bound is the LSB.
    def index(position: int) -> int:
        return right - position if ascending else right + position

    start, end = index(hi), index(lo)
    if start == end:
        return f"[{start}]"
    return f"[{start}:{end}]"


def _normalise_positions(
    scope: Scope | None,
    name: str,
    positions: tuple[int, ...],
) -> tuple[int, ...] | None:
    """Keep only a real slice; a full net or port carries no bit subset."""
    if not positions or scope is None:
        return positions or None
    for net in scope.nets:
        if net.name == name:
            return None if len(positions) == net.width else positions
    for port in scope.ports:
        if port.name == name:
            return None if len(positions) == port.width else positions
    return positions


def _pin_slice(pin: CellPin, positions: list[int]) -> str:
    """Bits of a cell pin one net touches, as the pin select to show."""
    if not positions:
        return ""
    lo, hi = min(positions), max(positions)
    if pin.width and len(positions) == pin.width and lo == 0 and hi == pin.width - 1:
        return ""
    if lo == hi:
        return f"[{lo}]"
    return f"[{hi}:{lo}]"


def _cell_row_label(
    cell: Cell | None, cell_name: str, port: str, net_name: str | None = None
) -> str:
    """Leaf text for a netlist cell row: '<net>  <type>.<pin>  <file>:<line>'.

    An internal yosys cell reads by its type (`$xor.Y`), so the net it sits
    on comes first: the source variable is the name the user knows, and the
    cell type is the detail after it. An unresolved instance has no module to
    name, so it reads by the cell name the source uses
    (`mix_fifo.m_axis_tvalid`) and keeps no net prefix. The yosys cell name
    embeds a mangled copy of the source span; the stored attribute spells it
    once. A cell built from several locations keeps the first (its own
    expression).
    """
    if cell is not None and cell.type.startswith("$"):
        head = f"{cell.type}.{port}"
        if net_name:
            head = f"{net_name}  {head}"
    else:
        head = f"{cell_name}.{port}"
    if cell is None:
        return head
    span = _src_focus(cell.src)
    if span is None:
        return head
    return f"{head}  {span[0].rsplit('/', 1)[-1]}:{span[1]}"


def _src_focus(src: str | None) -> tuple[str, int, int] | None:
    """Source span of an IR src, which yosys may join with '|'.

    Yosys writes one span per source location, e.g. the expression that
    became the cell and the always block around it. The first span is the
    cell's own expression, so it is the one the RTL view highlights. A cell,
    a net and an instance src can all arrive joined this way.
    """
    for part in (src or "").split("|"):
        span = _parse_src(part)
        if span is not None:
            return span
    return None


def cheat_line(doc: KeyDoc) -> str:
    """Cheatsheet row: the keys in a fixed column, then the effect."""
    return f"{doc.keys:<16} {doc.effect}"


def search_row_label(entry: SearchEntry) -> str:
    """One result row of the global finder: kind, then the full path."""
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


class TracePane(Static):
    """Centre, bottom half: the trace rows of the trace head.

    A bordered panel with its own chrome background, so the trace split
    reads as a second pane below the source view, never as more source
    lines. Its border title names the trace head; the rows render inside.
    """


class StatusBar(Static):
    """Top line: the selection's full hierarchical path (yazi-style).

    A plain Static is never focusable, so it cannot steal input — it is only a
    display seam, updated by the app as the selection moves.
    """

    can_focus = False


class CheatSheet(ModalScreen[None]):
    """Navigation cheatsheet: the full key map on one screen.

    Escape dismisses. Every other key also dismisses first and then continues
    to the layer below - TrenApp.on_event pops the sheet before dispatching,
    so a nav key reads the map and keeps navigating in one press. Both the
    sheet and the README table come from tren.keys, so they cannot drift.

    Opening it while the trace split is open lists the trace keys too:
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


class TraceNode:
    """One net on a trace, with the rows the user could descend into.

    The trace pane renders these nodes as a tree: the rows of the root sit
    at the left edge, and the rows of a crossed net nest one level under
    the row that crossed. A branch stays visible while another one grows.
    """

    def __init__(
        self,
        scope_path: str,
        net_name: str,
        parent: "TraceNode | None",
        *,
        via: tuple | None = None,
        pin_label: str | None = None,
        positions: tuple[int, ...] | None = None,
    ) -> None:
        self.scope_path = scope_path
        self.net_name = net_name
        self.parent = parent
        self.children: list["TraceNode"] = []
        # the row key that reached this net: an edge row or a cell pin row
        self.via = via
        # a net reached through a cell pin renders as "pin <-/-> net"
        self.pin_label = pin_label
        # the bits of this net that the trace carries. None means every bit;
        # a cross through a port or cell pin part-select keeps the touched bits.
        self.positions = positions
        # a crossed net shows its rows only while it is expanded
        self.expanded = False
        # cell rows whose pins are shown (the key lives on the owner node)
        self.expanded_rows: set[tuple] = set()
        # rows of this net, cached for the direction in force; None until
        # first built, cleared when the direction or a user cut changes
        self.rows: list[dict] | None = None


class SearchScreen(ModalScreen[None]):
    """Global fuzzy search: modules, instances, ports, nets, files.

    Two modes, vim-style. While the query Input holds focus, every printable
    key - j and k included - types text and never reaches the app bindings
    behind the modal (textual strips printable keys from the binding chain
    while an Input is focused; verified). up/down, the arrow aliases of j/k,
    move the cursor there; the modal binding chain truncates at
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
    list and the highlight follows.

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
    # a modal over the panes behind it.
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
        # pane lifts it like the app's own pane cue
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
        """tab: switch the active pane between results and matches."""
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
        """Point both panes' ListViews at the shared cursor."""
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
        """The source line the result matches (vim :g//p).

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
        query is committed, so cheap calls just keep the shared
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
        # line numbers: NOT priority. The finder's Input owns every printable
        # key while it is open, and the committed finder binds n for its next
        # match - the modal screen wins there, so the toggle never steals it.
        Binding("n", "toggle_line_numbers", "lines"),
        Binding("j", "next_item", "down", priority=True),
        Binding("k", "prev_item", "up", priority=True),
        Binding("l", "expand", "expand", priority=True),
        Binding("h", "collapse", "collapse", priority=True),
        # page up/down: editor-style, a screenful of the active pane.
        # priority, like j/k: they must win even though nothing
        # on the main screen holds focus. The search modal owns the screen
        # while it is open, so on_event stops them there rather than page
        # the tree behind it; in the trace split they page the trace rows.
        Binding("pageup", "prev_page", "page up", priority=True),
        Binding("pagedown", "next_page", "page down", priority=True),
        # arrow aliases of j/k/l/h. NOT priority, and that is the
        # point: while the trace split is open these keys act on the
        # trace rows (descend/back through the trace); a priority alias
        # here would take all four away from the pane.
        Binding("down", "next_item", "down", show=False),
        Binding("up", "prev_item", "up", show=False),
        Binding("right", "expand", "expand", show=False),
        Binding("left", "collapse", "collapse", show=False),
        Binding("enter", "focus_source", "show rtl"),  # NOT priority: the
        # trace split routes enter to descend while it is open
        Binding("ctrl+u", "go_up_level", "up level", priority=True),
        Binding("/", "open_search", "search", priority=True),
        Binding("y", "yank", "yank", priority=True),
        Binding("Y", "cycle_format", "fmt", priority=True),
        Binding("d", "trace_drivers", "drivers", priority=True),
        Binding("L", "trace_loads", "loads", priority=True),
        Binding("escape", "close_trace", "close trace", show=False),
        Binding("backspace", "trace_back", "pop trace", show=False),
        Binding("X", "toggle_cut", "cut", priority=True),
        Binding(";", "dump_trace", "dump", priority=True),
        Binding("e", "open_editor", "editor inst", priority=True),
        Binding("E", "open_module_definition", "editor def", priority=True),
        Binding("?", "show_cheatsheet", "cheatsheet", priority=True),
        Binding("q", "quit", "quit", priority=True),
    ]

    def __init__(
        self,
        design: Design,
        root: Path | None = None,
        settings: Config | None = None,
    ) -> None:
        super().__init__()
        settings = Config() if settings is None else settings
        self.design = design
        self.root = Path.cwd() if root is None else Path(root)
        self._settings = settings
        self._row_colors: dict[str, str] = {}
        self.yanked: list[str] = []
        self.yank_format: PathFormat = BY_NAME[settings.yank_format]
        self.clipboard_provider = resolve_provider(settings.yank_provider)
        self._yank_flash = False
        self._yank_flash_key: tuple[str, int, int | None] | None = None
        self._yank_timer = None
        self.osc_events: list[str] = []
        self.active = "tree"  # which pane j/k/l/h/y act on: tree | source | ports
        self._cue: str | None = None  # pane the background cue is painted for
        self.zoomed = False  # z: the active pane fills the screen
        self.line_numbers = settings.line_numbers  # n toggles the gutter
        self.editor_command = settings.editor_command
        self.theme = settings.theme
        self.src_offset = 0
        self._source_rows = 30  # last known source-pane height (see _follow)
        self._trace_pane_rows = 12  # last known trace-pane height (see _trace_view_rows)
        self.port_index = 0
        self.pane_ports: list[Port] = []
        self._scopes = {s.name: s for s in design.scopes}
        self._focus: tuple[str, int, int] | None = None
        # (focus, scope name) the source pane is currently scrolled for
        self._render_target: tuple[tuple[str, int, int] | None, str | None] | None = None
        self._file_cache: dict[str, list[str]] = {}
        self.editor_target: str | None = None
        # trace tree (scope_path, net_name); d/L populates it; ; dumps the path
        self._trace_root: TraceNode | None = None
        self._trace_cursor: TraceNode | None = None
        self._trace_dir = "drivers"
        # the trace split: True while the trace pane is open. `_trace_index`
        # selects the row of the trace head the descend targets;
        # `_trace_scroll` is the first row the bottom half shows.
        self._trace_open = False
        self._trace_index = 0
        self._trace_scroll = 0
        # per-module cell lookup and (cell, pin) -> nets, built on first use
        self._cells_by_module: dict[str, dict[str, Cell]] = {}
        self._cell_pins_by_module: dict[
            str,
            dict[
                tuple[str, str],
                list[tuple[str, str, tuple[int, ...], tuple[int, ...]]],
            ],
        ] = {}
        self.trace_dump: str = ""
        # user cuts (X): scope paths made opaque at runtime
        self._user_cut: set[str] = set()
        # the ? cheatsheet when it is on screen
        self._cheat: "CheatSheet | None" = None
        # the / global search when it is on screen
        self._search: "SearchScreen | None" = None
        self._search_index = build_search_index(design)

    def compose(self) -> ComposeResult:
        yield StatusBar("", id="status")
        with Horizontal():
            # auto_expand=False: textual's expand-on-select is an *async
            # message handler that toggles* — a key burst (autorepeat /
            # scripted paste) re-orders it against our visible-walk. TREN
            # expands deterministically in _select instead.
            # the root label is the top module (promoted in on_mount);
            # "design" only survives when no scope matches the declared top
            yield Tree("design", data=None, id="tree")
            # the centre column: the source pane, and below it the trace pane
            # while a trace is open (the split). Widths belong to the column.
            with Vertical(id="centre"):
                yield SourcePane("", id="source")
                yield TracePane("", id="trace")
            yield PortsPane("", id="ports")
        yield Footer()

    def on_mount(self) -> None:
        self._tree_widget = self.query_one("#tree")
        if self._settings.hierarchy_enabled:
            self._row_colors = resolve_row_colors(
                self.current_theme.to_color_system().generate(),
                scope=self._settings.hierarchy_scope,
                module=self._settings.hierarchy_module,
                blackbox=self._settings.hierarchy_blackbox,
                port=self._settings.hierarchy_port,
                inout=self._settings.hierarchy_inout,
                net=self._settings.hierarchy_net,
                backgrounds=(PANE_BG_ACTIVE, PANE_BG_IDLE),
            )
        # The tree is not focusable: textual auto-focuses the first focusable
        # widget on mount, which would shadow the app-level (non-priority)
        # Enter binding with the Tree's own. TREN drives all tree input via
        # priority=True app bindings, so this loses nothing (the trace
        # split is a pane, not a modal: it never steals focus).
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
        self._trace_widget = self.query_one("#trace")
        self._centre = self.query_one("#centre")
        self._ports_widget = self.query_one("#ports")
        self._status_widget = self.query_one("#status")
        self._status_widget.styles.height = 1
        self._status_widget.styles.background = STATUS_BG
        self._index = -1
        self._current_node = None
        # three columns: tree / centre / ports; panes fill the viewport so
        # size.height is stable (not content-driven) for auto-follow
        for _, widget in self._panes():
            widget.styles.height = "1fr"
        # the centre column splits between the source pane and the trace pane
        # while the trace is open; each half fills the column. The trace pane
        # is chrome, not a source surface: its own background and a border,
        # like the finder's panes, so the split reads as two panes.
        self._source_widget.styles.width = "1fr"
        self._source_widget.styles.height = "1fr"
        self._trace_widget.styles.width = "1fr"
        self._trace_widget.styles.height = "1fr"
        self._trace_widget.styles.border = ("solid", "#a6c9ff")
        self._trace_widget.styles.background = TRACE_PANE_BG
        self._trace_widget.display = False
        self._apply_layout()

        roots = [s for s in self.design.scopes if _scope_parent(s.name) not in self._scopes]
        top = self._scopes.get(self.design.top)
        root_node = self._tree_widget.root
        if top is not None:
            # the top module IS the tree root: no bare "design" line above it
            label = f"{top.name}/"
            root_node.label = self._styled_label(label, "module", bold=True)
            root_node.data = {"kind": "scope", "scope": top, "label": label}
            root_node.expand()
            # no expand event fires for the root, and the _visible() walk only
            # materializes below it: build the top scope's rows here
            self._ensure_children(root_node)
            # A source set can carry modules no single top reaches. Keep the
            # top as the root row anyway and park those roots under one
            # unselectable group row, so they stay reachable without
            # pretending they are part of the top's hierarchy.
            others = [s for s in roots if s is not top]
            if others:
                group = root_node.add(
                    self._styled_label("other roots/", "module"),
                    data=None,
                    allow_expand=True,
                )
                group.expand()
                for scope in others:
                    self._add_scope_node(group, scope, kind="module")
            # the cursor starts on the top-module row, so the source pane
            # opens on the design top instead of an empty pane. Without a
            # selection the first j "selects" row 0 - a move the eye cannot
            # see, since the cursor was implicitly there all along
            self._select(0)
        else:
            # no scope for the declared top: the neutral "design" line stays
            for scope in roots:
                self._add_scope_node(root_node, scope, kind="module")
            root_node.expand()
        self._render_panes()

    # ---- tree construction ------------------------------------------------
        # Scopes are populated LAZILY: a node is a stub with allow_expand=True
        # until _ensure_children runs (on select/expand/visible-walk). Only the
        # handful of roots exist at mount — the graph is never materialized.

    def _styled_label(
        self, text: str, kind: str, *, bold: bool = False
    ) -> RichText:
        """A tree label painted for one row kind."""
        label = RichText(text)
        style = row_style(kind, self._row_colors, bold=bold)
        if style:
            label.stylize(style)
        return label

    def _node_kind(self, node: Tree.Node) -> str:
        """The row kind a tree node paints as.

        A child scope is an instance. The top module, another root module
        and a generate block are module definitions. A black box or a user
        cut keeps its marker and takes the warning kind.
        """
        data = node.data or {}
        kind = data.get("kind")
        if kind in ("scope", "instance"):
            inst = data.get("instance")
            if inst is not None and inst.blackbox:
                return "blackbox"
            scope = self._scope_data(node)
            if scope is not None and scope.unresolved:
                return "unresolved"
            if scope is not None and (
                scope.blackbox or scope.name in self._user_cut
            ):
                return "blackbox"
            if kind == "instance":
                return "scope"
            if scope is not None and scope.module is None:
                return "module"
            parent = node.parent
            if (
                parent is None
                or node is self._tree().root
                or parent.data is None
            ):
                return "module"
            return "scope"
        if kind == "port":
            port = data.get("port")
            return port_kind(port.direction) if port is not None else "port_in"
        return "net"

    def _add_scope_node(
        self,
        parent: Tree.Node,
        scope: Scope,
        label: str | None = None,
        *,
        kind: str | None = None,
    ) -> Tree.Node:
        base_label = label or f"{scope.name}/"
        row_kind = kind
        if scope.unresolved:
            row_kind = "unresolved"
        elif scope.blackbox:
            row_kind = "blackbox"
        elif row_kind is None:
            row_kind = "module" if scope.module is None else "scope"
        return parent.add(
            self._styled_label(base_label, row_kind, bold=True),
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
            # Child scopes first, then ports, then nets: the hierarchy is
            # the quickest thing to walk into. The design top follows the
            # same order, so every scope reads the same way.
            for inst in scope.instances:
                child = self._scopes.get(f"{scope.name}.{inst.name}")
                if child is not None:
                    # the instance is a real sub-scope (elaborated IR): nest it
                    marker = "!" if inst.blackbox else ""
                    self._add_scope_node(
                        node,
                        child,
                        f"{inst.name}/{marker}",
                        kind="blackbox" if inst.blackbox else None,
                    )
                else:
                    # fixture shape: the instance's module scope supplies ports
                    inst_scope = (
                        self._scopes.get(inst.module) if inst.module else None
                    )
                    row_kind = "blackbox" if inst.blackbox else "scope"
                    if inst_scope is not None and inst_scope.blackbox:
                        row_kind = "blackbox"
                    inst_node = node.add(
                        self._styled_label(
                            f"{inst.name}/", row_kind, bold=True
                        ),
                        data={"kind": "instance", "instance": inst},
                        allow_expand=True,
                    )
                    if inst_scope is not None:
                        self._add_ports_nets(inst_node, inst_scope)
            self._add_ports_nets(node, scope)
        elif kind == "instance":
            inst = data.get("instance")
            inst_scope = self._scopes.get(inst.module) if inst else None
            if inst_scope is not None:
                self._add_ports_nets(node, inst_scope)

    def _add_ports_nets(self, node: Tree.Node, scope: Scope) -> None:
        """Leaves for a scope in {\"ports, then nets\"} order."""
        self._add_ports(node, scope)
        self._add_nets(node, scope)

    def _add_ports(self, node: Tree.Node, scope: Scope) -> None:
        """Port leaves for a scope, each keeping the net yosys named after it.

        The netlist lists a port a second time as a net, because yosys names
        every port net. Show such a signal once, as the port row, and keep
        its net on that row: the direction is the fact a net row lacks, and
        the trace and source jumps still need the net.
        """
        nets = {net.name: net for net in scope.nets}
        for port in scope.ports:
            node.add_leaf(
                self._styled_label(port_leaf_label(port), port_kind(port.direction)),
                data={"kind": "port", "port": port, "net": nets.get(port.name)},
            )

    def _add_nets(self, node: Tree.Node, scope: Scope) -> None:
        """Internal net leaves, skipping the nets a port row already carries."""
        port_names = {port.name for port in scope.ports}
        for net in scope.nets:
            if net.name in port_names:
                continue
            node.add_leaf(
                self._styled_label(net_leaf_label(net), "net"),
                data={"kind": "net", "net": net},
            )

    # ---- model accessors -------------------------------------------------

    def _tree(self) -> Tree:
        return self._tree_widget

    def _visible(self) -> list[Tree.Node]:
        def walk(node: Tree.Node):
            # a promoted root (the top module) is a row of its own; the bare
            # fallback root ("design") is not selectable
            if node.data is not None:
                yield node
            if node.is_expanded:
                self._ensure_children(node)  # lazy: materialize on demand
                for child in node.children:
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

        A port or net leaf focuses its own *declaration*:
        Port.src comes from slang --ast-json, Net.src from the yosys netname
        attributes. A merged port row falls back to its net's src when the
        slang pass gave the port none. Either may be missing (black boxes,
        synthesised nets), in which case the walk continues up and the
        module-level focus stands.

        An instance node carries its own src (the instantiation site). A
        sub-scope node focuses its instantiation in the parent (the parent's
        instance of it); a root scope focuses its module declaration.
        """
        node = node or self._current_node
        while node is not None and node.data is not None:
            data = node.data
            kind = data.get("kind")
            if kind in ("port", "net"):
                for signal in (data[kind], data.get("net")):
                    if signal is not None:
                        declaration = _src_focus(signal.src)
                        if declaration is not None:
                            return declaration
            if data.get("kind") == "instance":
                return _src_focus(data["instance"].src)
            if data.get("kind") == "scope":
                scope = data["scope"]
                parent = _scope_parent(scope.name)
                if parent in self._scopes:
                    for inst in self._scopes[parent].instances:
                        if f"{parent}.{inst.name}" == scope.name:
                            return _src_focus(inst.src)
                return _src_focus(scope.src)
            node = node.parent
        return None

    def _io_selection_port(self) -> Port | None:
        """The I/O pane's marked port, while that pane owns the selection.

        The marker is the selection for `enter`, `e` and `d`/`L` only while
        the I/O pane is active. A hidden marker (no ports, an out-of-range
        index), an open trace split and the search modal all give the
        selection back to the tree.
        """
        if (
            self.active != "ports"
            or self._trace_open
            or self._search is not None
            or not self.pane_ports
            or not (0 <= self.port_index < len(self.pane_ports))
        ):
            return None
        return self.pane_ports[self.port_index]

    def _effective_focus(self) -> tuple[str, int, int] | None:
        """Source focus of the *effective* selection.

        With the I/O pane active the selection is the port its marker sits
        on, so the focus is that port's declaration - the same rule a port
        leaf in the tree follows. A port the backends gave
        no src, an out-of-range index and every other pane fall back to the
        tree selection's focus. While the search finder (a modal screen)
        owns the screen the tree selection stays the truth: the search
        moves the tree rows and the centre pane must follow them,
        whatever pane was active when it opened. While the trace pane holds
        the cursor, the highlighted line owns the focus: a cell row shows
        the statement it implements and a pin line shows the net it names.
        """
        if (
            self.active == "source"
            and self._trace_open
            and self._search is None
        ):
            traced = self._trace_focus()
            if traced is not None:
                return traced
        port = self._io_selection_port()
        if port is not None:
            declared = _src_focus(port.src)
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
        # pane hidden by the zoom reports zero for as long as it
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
        self,
        focus: tuple[str, int, int] | None,
        scope: Scope | None,
        offset: int | None = None,
    ) -> RichText:
        """Render the source view.

        offset overrides the pane scroll: the search preview passes
        a per-entry offset so browsing results never disturbs the pane state.
        The trace split no longer caps the view here: the trace pane is its
        own widget below, and the layout divides the centre column.
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
                width = _gutter_width(len(lines)) if self.line_numbers else 0
                for i, line in enumerate(view):
                    abs_i = off + i + 1
                    style = "bold #ffa62b" if lo <= abs_i <= hi else "dim white"
                    if width:
                        src.append(f"{abs_i:>{width}} ", style="dim")
                    src.append(f"{line}\n", style=style)
                return src
            if scope is None or not scope.rtl:
                src.append(f"(cannot read {file})\n", style="dim italic")
                return src
            # the file is gone but the scope carries an inline snippet: prefer
            # showing RTL over an error. Reachable since port/net leaves began
            # resolving their own src — fixture nets carry a
            # src pointing at a file that need not be checked out.
        if scope is not None:
            # fixture fallback: inline rtl snippet with the rtl_focus band
            rtl = scope.rtl
            lo, hi = scope.rtl_focus
            view = rtl[off :]
            if off:
                src.append("…… (↑ scroll to see earlier lines)\n", style="dim italic")
            width = _gutter_width(len(rtl)) if self.line_numbers else 0
            for i, line in enumerate(view):
                abs_i = i + 1 + off
                style = "bold #ffa62b" if lo <= abs_i <= hi else "dim white"
                if width:
                    src.append(f"{abs_i:>{width}} ", style="dim")
                src.append(f"{line}\n", style=style)
        return src

    def _render_trace_pane(self) -> RichText:
        """The trace pane's tree: the head's rows, plus every expanded branch.

        The border title names the net whose rows hold the cursor and, while
        the tree overflows the pane, the visible line range. The lines render
        bounded (the same count `_trace_view_rows` uses), so a move can never
        leave the cursor outside the window.
        """
        trace = RichText()
        cursor = self._trace_cursor
        if cursor is None:
            return trace
        lines = self._trace_visible()
        self._trace_index = max(0, min(self._trace_index, max(0, len(lines) - 1)))
        view = self._trace_view_rows()
        if self._trace_scroll >= len(lines):
            self._trace_scroll = max(0, len(lines) - view)
        lo = self._trace_scroll
        hi = min(len(lines), lo + view)
        if lines:
            self._trace_cursor = lines[self._trace_index]["node"]
            cursor = self._trace_cursor
        title = f"{self._trace_dir} of {self._trace_head_label(cursor)}"
        if len(lines) > view:
            title += f"  (rows {lo + 1}-{hi} of {len(lines)})"
        self._trace_widget.border_title = title
        if not lines:
            trace.append(f"  {self._trace_empty_note()}\n", style="dim italic")
            return trace
        if lo:
            trace.append("…… (↑ scroll to see earlier rows)\n", style="dim italic")
        for i in range(lo, hi):
            line = lines[i]
            selected = i == self._trace_index
            sel = "▸ " if selected else "  "
            indent = "  " * line["depth"]
            if line["empty"]:
                style = "bold dim italic" if selected else "dim italic"
                trace.append(f"{sel}{indent}{line['label']}\n", style=style)
                continue
            if line["expanded"]:
                branch = "- "
            elif line["expandable"]:
                branch = "+ "
            else:
                branch = "  "
            style = row_style(
                trace_kind(line.get("row")),
                self._row_colors,
                bold=selected,
            )
            trace.append(f"{sel}{indent}{branch}{line['label']}\n", style=style)
        if hi < len(lines):
            trace.append(f"…… (↓ {len(lines) - hi} more rows)\n", style="dim italic")
        return trace

    def _port_color(self, direction: str) -> str:
        """I/O pane colour of a port, matching its hierarchy row.

        A disabled or unset hierarchy palette keeps the pane's own colours;
        an explicit ``none`` for ports leaves both panes uncoloured.
        """
        fallback = {
            "out": "#7adf77",
            "inout": "#d17e92",
        }.get(direction, "#a6c9ff")
        return self._row_colors.get(port_kind(direction), fallback)

    def _render_panes(self) -> None:
        scope = self._scope_data(self._current_node)
        focus = self._effective_focus()
        # the trace line's own scope backs the fallback render, so a cell
        # from a child scope shows that scope's inline RTL when its file is
        # unreadable (fixture IR) instead of the tree selection's.
        source_scope = scope
        if (
            self._trace_open
            and self.active == "source"
            and self._search is None
        ):
            line = self._trace_line()
            if line is not None:
                source_scope = self._scopes.get(line["node"].scope_path, scope)
        # What the centre pane is showing: a file span, or (fixture IR) the
        # scope's inline snippet. Scroll position belongs to *that* target, so
        # compare both — focus alone stays None across a move between two
        # src-less scopes and used to carry the previous scope's scroll offset
        # into an unrelated snippet. Within one target (j in the source pane)
        # nothing is reset. The focus is the effective selection's:
        # the I/O pane's selected port while that pane is active, so moving
        # its marker re-centres the declaration like a tree leaf does.
        target = (focus, source_scope.name if source_scope is not None else None)
        if target != self._render_target:
            self._render_target = target
            self._focus = focus
            if focus is not None:
                self._follow(focus)
            else:
                self.src_offset = 0  # inline RTL view starts at the top
        # the trace pane is a bordered panel below the source pane; the centre
        # column carries the split, so the source view needs no line limit here
        self._trace_widget.display = self._trace_open
        self._source_widget.update(self._render_source(focus, source_scope))
        if self._trace_open:
            self._trace_widget.update(self._render_trace_pane())
            height = self._trace_widget.size.height
            if height:
                self._trace_pane_rows = height
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
                base = self._port_color(direction)
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
        self._clear_yank_on_move()
        self._status_widget.update(self._selection_status())
        self._render_pane_cue()

    def _panes(self) -> tuple[tuple[str, Widget], ...]:
        """The three panes in screen order, keyed by the name `active` uses.

        The centre pane is the column container: it holds the source pane
        and, while a trace is open, the trace pane below it. Width and the
        zoom display belong to the column; the panes inside fill it.
        """
        return (
            ("tree", self._tree_widget),
            ("source", self._centre),
            ("ports", self._ports_widget),
        )

    def _apply_layout(self) -> None:
        """Size the panes for the current zoom state.

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
        # the column container never shows through (its panes fill it), so the
        # source pane paints its own cue background; the trace pane keeps its
        # chrome background at every cue
        self._source_widget.styles.background = (
            PANE_BG_ACTIVE if self.active == "source" else PANE_BG_IDLE
        )
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
        """(scope_path, net_name) of the effective selection, else None.

        The I/O pane's marker is the selection while that pane is active,
        as it is for the source focus. Everywhere else the tree row names
        the net; a port row carries the net the netlist lists for it, so
        tracing a port works like tracing the net it names.
        """
        scope = self._scope_data(self._current_node)
        port = self._io_selection_port()
        if port is not None and scope is not None:
            return scope.name, port.name
        node = self._current_node
        while node is not None and node.data is not None:
            net = node.data.get("net")
            if net is not None:
                scope = self._scope_data(node)
                return (scope.name, net.name) if scope is not None else None
            node = node.parent
        return None

    def _trace_head_label(self, node: TraceNode) -> str:
        """How the trace head reads in the title and the `;` dump.

        A named net is its path. A yosys `$...` intermediate has no name
        worth showing, so it reads as the pin it was reached through, e.g.
        `top.u_core.u_alu0.A`.
        """
        if not node.net_name.startswith("$"):
            label = f"{node.scope_path}.{node.net_name}"
            if node.positions is not None:
                scope = self._scopes.get(node.scope_path)
                net = next(
                    (n for n in scope.nets if n.name == node.net_name),
                    None,
                ) if scope is not None else None
                if net is not None:
                    label += _net_slice(net, node.positions)
            return label
        if node.pin_label:
            return f"{node.scope_path}.{node.pin_label}"
        return f"{node.scope_path}.{node.net_name}"

    def _trace_rows(self) -> list[dict]:
        """Driver/load rows of the net under the cursor."""
        cursor = self._trace_cursor
        return self._trace_rows_of(cursor) if cursor is not None else []

    def _trace_rows_of(self, node: TraceNode) -> list[dict]:
        """Driver/load rows of one trace node, cached for the direction.

        A net's rows stop at the module edge. A port also has the connection
        on the other side of that edge, so the outward rows of
        `_outward_rows` join the in-scope ones. An internal cell row carries
        the nets on its other side, so the tree can step into the logic.
        """
        if node.rows is not None:
            return node.rows
        scope_path, net_name = node.scope_path, node.net_name
        scope = self._scopes.get(scope_path)
        if scope is None:
            node.rows = []
            return node.rows
        conn = next(
            (c for c in self.design.connectivity if c.scope == scope.module and c.net == net_name),
            None,
        )
        cells = self._cells_of(scope)
        other_side = "in" if self._trace_dir == "drivers" else "out"
        pin_nets: (
            dict[
                tuple[str, str],
                list[tuple[str, str, tuple[int, ...], tuple[int, ...]]],
            ]
            | None
        ) = None
        seen: set[tuple[str, str]] = set()
        rows: list[dict] = []
        bits = conn.bits if conn is not None else ()
        if node.positions is not None:
            bits = tuple(
                bits[p] for p in node.positions if 0 <= p < len(bits)
            )
        for bit in bits:
            eps = bit.drivers if self._trace_dir == "drivers" else bit.loads
            # a port whose direction the netlist cannot tell shows in both
            # directions, marked; it must never vanish from a trace
            marked = [(e, False) for e in eps] + [(e, True) for e in bit.unknown]
            for endpoint, unknown in marked:
                cell, port = endpoint.cell, endpoint.port
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
                        row["target"] = (child, port)
                        row["label"] = f"{cell}.{port}  → {child_scope.module}"
                        child_positions = None
                        if node.positions is not None:
                            selected_bits = tuple(
                                sorted(
                                    {
                                        e.bit
                                        for b in bits
                                        for e in (
                                            *b.drivers,
                                            *b.loads,
                                            *b.unknown,
                                        )
                                        if e.cell == cell and e.port == port
                                    }
                                )
                            )
                            child_positions = _normalise_positions(
                                child_scope, port, selected_bits
                            )
                        row["positions"] = child_positions
                        row["key"] = ("edge", child, port, child_positions)
                else:
                    meta = cells.get(cell)
                    row["kind"] = "cell"
                    net_label = (
                        net_name
                        if not net_name.startswith("$")
                        else self._trace_variable(node)
                    )
                    row["label"] = _cell_row_label(meta, cell, port, net_label)
                    # the key carries the direction: the other side of a
                    # driver is its inputs, of a load its outputs
                    row["key"] = ("cell", self._trace_dir, cell, port)
                    if meta is not None:
                        if pin_nets is None:
                            pin_nets = self._cell_pin_nets(scope)
                        selected_pin_bits = (
                            {
                                e.bit
                                for b in bits
                                for e in (*b.drivers, *b.loads, *b.unknown)
                                if e.cell == cell and e.port == port
                            }
                            if node.positions is not None
                            else None
                        )
                        row["pins"] = []
                        for pin in meta.pins:
                            if pin.direction != other_side:
                                continue
                            for net_name, label, net_positions, pin_bits in pin_nets.get(
                                (cell, pin.name), ()
                            ):
                                if selected_pin_bits is None:
                                    positions = net_positions
                                else:
                                    positions = tuple(
                                        position
                                        for position, bit in zip(
                                            net_positions, pin_bits
                                        )
                                        if bit in selected_pin_bits
                                    )
                                    if not positions:
                                        continue
                                row["pins"].append(
                                    (
                                        pin.name,
                                        net_name,
                                        label,
                                        _normalise_positions(
                                            scope, net_name, positions
                                        ),
                                    )
                                )
                if unknown:
                    row["unknown"] = True
                    row["label"] += "  (direction unknown)"
                rows.append(row)
        rows.extend(
            self._outward_rows(scope, scope_path, net_name, node.positions)
        )
        node.rows = rows
        return rows

    def _cells_of(self, scope: Scope) -> dict[str, Cell]:
        """Netlist cells of a scope by name (shared per module)."""
        key = scope.module or scope.name
        cells = self._cells_by_module.get(key)
        if cells is None:
            cells = {cell.name: cell for cell in scope.cells}
            self._cells_by_module[key] = cells
        return cells

    def _trace_focus(self) -> tuple[str, int, int] | None:
        """Source focus of the highlighted trace line.

        A cell row points at the statement it implements, so walking a cone
        highlights the logic instead of the net the trace started from. A
        pin line or an empty-branch note points at the net it names; an
        intermediate net with no declaration falls back to the cell that
        owns the pin. Everything else keeps the tree selection's focus.
        """
        line = self._trace_line()
        if line is None:
            return None
        node = line["node"]
        row = line.get("row")
        if row is not None:
            focus = self._cell_focus(node.scope_path, row.get("cell"))
            if focus is not None:
                return focus
            if row.get("kind") == "cell":
                return self._net_focus(node.scope_path, node.net_name)
            return None
        focus = self._net_focus(node.scope_path, node.net_name)
        if focus is not None:
            return focus
        via = node.via
        if via is not None and via[0] == "cell":
            return self._cell_focus(node.scope_path, via[2])
        return None

    def _trace_variable(self, node: TraceNode | None) -> str | None:
        """Nearest named net above a trace node.

        A yosys `$...` intermediate has no source-level name, but its rows
        still belong to the cone of a named net: a mux chain under a
        register computes that register's next value. Walk up the tree so
        the rows lead with the variable the user is tracing instead of a
        second anonymous cell name.
        """
        while node is not None:
            if not node.net_name.startswith("$"):
                return node.net_name
            node = node.parent
        return None

    def _cell_focus(
        self, scope_path: str, cell_name: str | None
    ) -> tuple[str, int, int] | None:
        """Source span of a netlist cell in the trace line's scope."""
        if not cell_name:
            return None
        scope = self._scopes.get(scope_path)
        if scope is None:
            return None
        cell = self._cells_of(scope).get(cell_name)
        return _src_focus(cell.src) if cell is not None else None

    def _net_focus(
        self, scope_path: str, net_name: str
    ) -> tuple[str, int, int] | None:
        """Declaration span of a net, or of the port with the same name."""
        scope = self._scopes.get(scope_path)
        if scope is None:
            return None
        for net in scope.nets:
            if net.name == net_name:
                declared = _src_focus(net.src)
                if declared is not None:
                    return declared
                break
        for port in scope.ports:
            if port.name == net_name:
                return _src_focus(port.src)
        return None

    def _cell_pin_nets(
        self, scope: Scope
    ) -> dict[
        tuple[str, str],
        list[tuple[str, str, tuple[int, ...], tuple[int, ...]]],
    ]:
        """(cell, pin) -> [(net, label, net positions, pin bits)].

        Built once per module from the connectivity map: every bit endpoint
        names the net that carries it, so a pin's nets fall out of the same
        data the rows use. A pin wired only to constants has no entry.

        Netnames that carry the same bits are aliases of one wire (a plain
        `assign x = a`). They collapse to one entry, so the cone below is
        walked once; the label names the wire and notes the other names. A
        wire with no source-level name (a yosys `$...` intermediate) leaves
        the net part out, so the line reads as the pin alone. A pin spread
        over several wires carries its bit select, as `S[0]`. The entry also
        keeps the net positions and pin bits, so a sliced trace can carry its
        selected bits through the cell.
        """
        key = scope.module or scope.name
        index = self._cell_pins_by_module.get(key)
        if index is not None:
            return index
        nets = {net.name: net for net in scope.nets}
        ports = {port.name for port in scope.ports}
        pins = {
            (cell.name, pin.name): pin for cell in scope.cells for pin in cell.pins
        }
        hits: dict[tuple[str, str], dict[str, list[tuple[int, int]]]] = {}
        for conn in self.design.connectivity:
            if conn.scope != scope.module:
                continue
            for bit in conn.bits:
                for endpoint in (*bit.drivers, *bit.loads, *bit.unknown):
                    hits.setdefault((endpoint.cell, endpoint.port), {}).setdefault(
                        conn.net, []
                    ).append((bit.position, endpoint.bit))
        index = {}
        for pin_key, by_net in hits.items():
            pin = pins.get(pin_key)
            groups: dict[
                tuple, list[tuple[str, str, tuple[int, ...], list[int]]]
            ] = {}
            for net_name in sorted(by_net):
                pairs = by_net[net_name]
                net = nets.get(net_name)
                positions = [position for position, _ in pairs]
                select = _net_slice(net, positions) if net is not None else ""
                if net is None:
                    signature: tuple = (net_name,)
                else:
                    signature = tuple(net.bits[position] for position in positions)
                groups.setdefault(signature, []).append(
                    (
                        net_name,
                        select,
                        tuple(positions),
                        [bit for _, bit in pairs],
                    )
                )
            entries: list[
                tuple[int, str, str, tuple[int, ...], tuple[int, ...]]
            ] = []
            for members in groups.values():
                members.sort(
                    key=lambda item: (
                        item[0].startswith("$"),
                        item[0] in ports,
                        item[0],
                    )
                )
                primary, select, net_positions, pin_bits = members[0]
                named = [
                    f"{name}{sel}"
                    for name, sel, _, _ in members
                    if not name.startswith("$")
                ]
                head = f"{pin.name if pin else pin_key[1]}"
                if pin is not None:
                    head += _pin_slice(pin, pin_bits)
                if named:
                    text = named[0]
                    if len(named) > 1:
                        text += " (alias " + ", ".join(named[1:]) + ")"
                    arrow = "<-" if pin is None or pin.direction == "in" else "->"
                    label = f"{head} {arrow} {text}"
                else:
                    label = head
                entries.append(
                    (
                        min(pin_bits),
                        primary,
                        label,
                        net_positions,
                        tuple(pin_bits),
                    )
                )
            index[pin_key] = [
                (net, label, net_positions, pin_bits)
                for _, net, label, net_positions, pin_bits in sorted(entries)
            ]
        self._cell_pins_by_module[key] = index
        return index

    def _trace_children(self, node: TraceNode, row: dict) -> list[TraceNode]:
        """Child nodes a row leads to, once they exist."""
        key = row.get("key")
        if key is None:
            return []
        return [child for child in node.children if child.via == key]

    def _trace_line(self) -> dict | None:
        """The highlighted line of the trace tree, or None when it is shut."""
        if not self._trace_open:
            return None
        lines = self._trace_visible()
        if not lines:
            return None
        return lines[max(0, min(self._trace_index, len(lines) - 1))]

    def _trace_visible(self) -> list[dict]:
        """The trace tree as indented lines, depth-first.

        Each node contributes its rows. An expanded edge row contributes the
        rows of the net it crossed to, one level deeper. An expanded cell row
        contributes a line per net on its other side, and each of those lines
        can expand to that net's rows. A net with no rows of its own (a design
        input, say) keeps one dim note line, so the cursor can rest on it and
        the path stays whole.
        """
        lines: list[dict] = []
        root = self._trace_root
        if root is not None:
            self._walk_trace(root, 0, lines)
        return lines

    def _walk_trace(self, node: TraceNode, depth: int, lines: list[dict]) -> None:
        for row in self._trace_rows_of(node):
            key = row.get("key")
            if key is not None and key[0] == "cell":
                expanded = key in node.expanded_rows
                lines.append(
                    {
                        "label": row["label"],
                        "row": row,
                        "node": node,
                        "depth": depth,
                        "expandable": bool(row.get("pins")),
                        "expanded": expanded,
                        "empty": False,
                        "pin": False,
                    }
                )
                if not expanded:
                    continue
                for child in self._trace_children(node, row):
                    self._walk_pin(child, depth + 1, lines)
                continue
            child = next(iter(self._trace_children(node, row)), None)
            expanded = child is not None and child.expanded
            lines.append(
                {
                    "label": row["label"],
                    "row": row,
                    "node": node,
                    "depth": depth,
                    "expandable": key is not None,
                    "expanded": expanded,
                    "empty": False,
                    "pin": False,
                }
            )
            if not expanded or child is None:
                continue
            self._walk_branch(child, depth + 1, lines)

    def _walk_pin(self, child: TraceNode, depth: int, lines: list[dict]) -> None:
        lines.append(
            {
                "label": child.pin_label or child.net_name,
                "row": None,
                "node": child,
                "depth": depth,
                "expandable": True,
                "expanded": child.expanded,
                "empty": False,
                "pin": True,
            }
        )
        if child.expanded:
            self._walk_branch(child, depth + 1, lines)

    def _walk_branch(self, child: TraceNode, depth: int, lines: list[dict]) -> None:
        if self._trace_rows_of(child):
            self._walk_trace(child, depth, lines)
        else:
            lines.append(
                {
                    "label": self._trace_empty_note(child),
                    "row": None,
                    "node": child,
                    "depth": depth,
                    "expandable": False,
                    "expanded": False,
                    "empty": True,
                    "pin": False,
                }
            )

    def _port_aliases(
        self, scope: Scope, net_name: str
    ) -> list[tuple[Port, set[int]]]:
        """Ports whose bits the head net carries, with the port bit positions.

        The head may be the port itself, or a net the netlist aliased to it:
        a plain `assign x = a` leaves x carrying a's bits, so tracing x must
        reach the same parent edge that a has.
        """
        nets = {net.name: net for net in scope.nets}
        head = nets.get(net_name)
        if head is None or not head.bits:
            return []
        head_bits = set(head.bits)
        aliases: list[tuple[Port, set[int]]] = []
        for port in scope.ports:
            net = nets.get(port.name)
            if net is None:
                continue
            shared = {i for i, bit in enumerate(net.bits) if bit in head_bits}
            if shared:
                aliases.append((port, shared))
        return aliases

    def _outward_rows(
        self,
        scope: Scope,
        scope_path: str,
        net_name: str,
        selected: tuple[int, ...] | None = None,
    ) -> list[dict]:
        """Rows for the port edge on the outside of the scope.

        An input port is driven from outside the scope and an output port is
        loaded outside it, so the in-scope rows stop at the module edge. The
        parent net that connects the instance port is the next step; enter
        crosses to it. The design top has no parent, so its ports get none.

        ``selected`` is the bit subset a slice trace carries. It narrows the
        parent edge to those bits, so a generated lane output joined to one
        slice of a bus names only that slice's parent net and loads.
        """
        rows: list[dict] = []
        for port, port_positions in self._port_aliases(scope, net_name):
            if selected is not None:
                port_positions = set(port_positions) & set(selected)
            if not port_positions:
                continue
            outward = (
                self._trace_dir == "drivers" and port.direction in ("in", "inout")
            ) or (
                self._trace_dir == "loads" and port.direction in ("out", "inout")
            )
            if not outward:
                continue
            for link in self.design.boundaries:
                if link.child_scope != scope_path or link.port != port.name:
                    continue
                parent = self._scopes.get(link.parent_scope)
                if parent is None:
                    continue
                for conn in self.design.connectivity:
                    if conn.scope != parent.module:
                        continue
                    touched = [
                        bit.position
                        for bit in conn.bits
                        if any(
                            e.cell == link.instance
                            and e.port == link.port
                            and e.bit in port_positions
                            for e in (*bit.drivers, *bit.loads, *bit.unknown)
                        )
                    ]
                    if not touched:
                        continue
                    parent_net = next(
                        (n for n in parent.nets if n.name == conn.net), None
                    )
                    target_positions = tuple(touched)
                    if (
                        parent_net is not None
                        and len(target_positions) == len(parent_net.bits)
                    ):
                        target_positions = None
                    select = (
                        _net_slice(parent_net, touched)
                        if parent_net is not None
                        else ""
                    )
                    rows.append(
                        {
                            "cell": link.instance,
                            "port": link.port,
                            "kind": "up",
                            "target": (link.parent_scope, conn.net),
                            "positions": target_positions,
                            "key": (
                                "edge",
                                link.parent_scope,
                                conn.net,
                                target_positions,
                            ),
                            "label": f"{conn.net}{select}  ↑ {link.parent_scope}",
                        }
                    )
        return rows

    def _trace_empty_note(self, node: TraceNode | None = None) -> str:
        """Why a trace net has no rows.

        A design top's input is driven outside the design and its output is
        loaded outside it, so such a port has no row on that side. Every
        other empty net is a plain "(no rows)".
        """
        node = node if node is not None else self._trace_cursor
        if node is None or _scope_parent(node.scope_path) is not None:
            return "(no rows)"
        scope = self._scopes.get(node.scope_path)
        if scope is None:
            return "(no rows)"
        directions = {port.direction for port, _ in self._port_aliases(scope, node.net_name)}
        if self._trace_dir == "drivers" and directions & {"in", "inout"}:
            return "(design input - its driver is outside the design)"
        if self._trace_dir == "loads" and directions & {"out", "inout"}:
            return "(design output - its loads are outside the design)"
        return "(no rows)"

    def _trace_expand(self, picked: dict) -> bool:
        """Open the highlighted line one level.

        A cell row reveals one line per net on its other side (the inputs of
        a driver, the outputs of a load). An edge row reveals the net it
        crossed to. A pin line reveals the rows of the net it names. The
        cursor moves onto the first line of the new branch (a note line when
        a net has no rows). A leaf row rings the bell.
        """
        node = picked["node"]
        index = self._trace_index
        if picked.get("pin"):
            node.expanded = True
        else:
            row = picked["row"]
            key = row.get("key") if row is not None else None
            if row is None or key is None:
                return False
            if key[0] == "cell":
                if not row.get("pins"):
                    return False
                node.expanded_rows.add(key)
                if not self._trace_children(node, row):
                    for _pin, net_name, label, positions in row["pins"]:
                        node.children.append(
                            TraceNode(
                                node.scope_path,
                                net_name,
                                node,
                                via=key,
                                pin_label=label,
                                positions=positions,
                            )
                        )
            else:
                target = row.get("target")
                if target is None:
                    return False
                children = self._trace_children(node, row)
                if children:
                    child = children[0]
                else:
                    child = TraceNode(
                        target[0],
                        target[1],
                        node,
                        via=key,
                        positions=row.get("positions"),
                    )
                    node.children.append(child)
                child.expanded = True
                self._select_path(child.scope_path, child.net_name)
        lines = self._trace_visible()
        self._trace_index = min(index + 1, len(lines) - 1)
        self._trace_cursor = lines[self._trace_index]["node"]
        return True

    def _trace_collapse(self) -> bool:
        """Close one trace level, or step to the parent line.

        An open line closes in place. Otherwise the cursor goes to the line
        one level up. At the first level there is no line above, so the
        caller closes the split.
        """
        lines = self._trace_visible()
        if not (0 <= self._trace_index < len(lines)):
            return False
        line = lines[self._trace_index]
        node = line["node"]
        if line["expanded"]:
            if line["pin"]:
                node.expanded = False
            elif line["row"] is not None and line["row"].get("key", ("",))[0] == "cell":
                node.expanded_rows.discard(line["row"]["key"])
            elif line["row"] is not None:
                for child in self._trace_children(node, line["row"]):
                    child.expanded = False
            return True
        if line["depth"] == 0:
            return False
        parent = next(
            i
            for i in range(self._trace_index - 1, -1, -1)
            if lines[i]["depth"] == line["depth"] - 1
        )
        parent_line = lines[parent]
        if parent_line["pin"]:
            parent_line["node"].expanded = False
        elif parent_line["row"] is not None:
            row = parent_line["row"]
            if row.get("key", ("",))[0] == "cell":
                parent_line["node"].expanded_rows.discard(row["key"])
            else:
                for child in self._trace_children(parent_line["node"], row):
                    child.expanded = False
        self._trace_index = parent
        self._trace_cursor = parent_line["node"]
        return True

    def _find_scope_node(self, path: str) -> Tree.Node | None:
        """Descend by path segments so only the touched lineage materializes."""
        parts = path.split(".")
        node = self._tree().root
        # a promoted top module lives on the root itself, not on a child row
        rdata = node.data
        start = 1
        if rdata is not None and rdata.get("kind") == "scope":
            top_parts = rdata["scope"].name.split(".")
            if parts[: len(top_parts)] == top_parts:
                if len(parts) == len(top_parts):
                    return node
                start = len(top_parts) + 1
        for i in range(start, len(parts) + 1):
            prefix = ".".join(parts[:i])
            self._ensure_children(node)
            match = self._scope_child(node, prefix)
            if match is None:
                return None
            node = match
        return node

    def _scope_child(self, node: Tree.Node, path: str) -> Tree.Node | None:
        """A child scope row named `path`, also looking inside group rows.

        A group row (the "other roots" holder) carries no data of its own;
        it exists only to group sibling roots, so its scope children answer
        to their own full path.
        """
        for child in node.children:
            data = child.data
            if (
                data is not None
                and data.get("kind") == "scope"
                and data["scope"].name == path
            ):
                return child
        for child in node.children:
            if child.data is None:
                match = self._scope_child(child, path)
                if match is not None:
                    return match
        return None

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        self._ensure_children(event.node)

    def _find_net_leaf(self, scope_node: Tree.Node, net_name: str) -> Tree.Node | None:
        """The leaf for a net: its own row, or the port row that merged it."""
        self._ensure_children(scope_node)  # final node of the descend above is unbuilt
        stack = list(scope_node.children)
        while stack:
            n = stack.pop()
            d = n.data
            net = d.get("net") if d is not None else None
            if net is not None and net.name == net_name:
                return n
            stack.extend(n.children)
        return None

    def _expand_to(self, node: Tree.Node) -> None:
        """Expand a node and every ancestor, including the tree root.

        The root is a real row here (the top module), so expanding a
        selection on it must unfold it like any other node. The earlier
        walk stopped *before* the root, which left a collapsed top
        impossible to reopen.
        """
        chain: list[Tree.Node] = []
        n = node
        while n is not None:
            chain.append(n)
            if n is self._tree().root:
                break
            n = n.parent
        for n in reversed(chain):
            n.expand()

    def _select_path(self, scope_path: str, net_name: str | None = None) -> bool:
        """Select the tree row for a scope (or one of its nets); True on hit.

        The search finder keys on the return value: False means
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

    # ---- global search: jump + preview helpers ---------------

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
            return _src_focus(entry.src), entry.scope
        if entry.kind == "instance":
            inst = entry.instance
            if inst is not None:
                return _src_focus(inst.src or entry.src), (
                    self._scopes.get(inst.module) if inst.module else entry.scope
                )
            return _src_focus(entry.src), entry.scope
        if entry.kind in ("port", "net", "file"):
            return _src_focus(entry.src), entry.scope
        return None, None

    def _preview_offset(self, focus: tuple[str, int, int], rows: int) -> int:
        """Scroll offset for the search preview, centring the focus line."""
        file, lo, _ = focus
        lines = self._file_lines(file)
        return max(0, min(lo - rows // 2, max(0, len(lines) - rows)))

    def _open_trace(self, direction: str) -> None:
        """d/L: trace the selected net, or switch the direction while traced.

        d/L always start from a selected net. With the split open and the
        trace pane holding the cursor, they only swap drivers for loads (and
        back) at the same head, so the accumulated path stays. From another
        pane they start a new trace at the tree selection, which replaces
        the split. The search finder blocks all of them, as the split is not
        reachable from a modal.
        """
        if self._search is not None:
            self.bell()
            return
        if self._trace_open and self.active == "source":
            self._set_trace_direction(direction)
            return
        sel = self._selection_net()
        if sel is None:
            self.bell()
            return
        self._trace_root = TraceNode(sel[0], sel[1], None)
        self._trace_cursor = self._trace_root
        self._trace_dir = direction
        self._trace_open = True
        self._trace_index = 0
        self._trace_scroll = 0
        # the split owns j/k while it is open: the trace rows are the list
        # the user walks. The tree selection follows the trace, so the
        # hierarchy view stays in sync without stealing pane 1. Select the
        # pane through _select_pane: it moves an active zoom to the centre,
        # so a split opened from a zoomed pane is not hidden behind it.
        self._select_pane("source")

    def _set_trace_direction(self, direction: str) -> None:
        """Swap the trace between drivers and loads on the same tree.

        Every node's rows depend on the direction, so the row cache drops.
        The cursor keeps the net it was on; the window follows. A repeat of
        the direction in force changes nothing and rings the bell, the app's
        signal for a key with no effect here.
        """
        if self._trace_dir == direction:
            self.bell()
            return
        cursor = self._trace_cursor
        self._trace_dir = direction
        self._invalidate_trace_rows()
        lines = self._trace_visible()
        if not any(line["node"] is cursor for line in lines):
            cursor = self._trace_root
        self._trace_cursor = cursor
        self._trace_index = next(
            (i for i, line in enumerate(lines) if line["node"] is cursor),
            0,
        )
        self._scroll_trace_into_view(len(lines))
        self._render_panes()

    def _close_trace(self) -> None:
        """escape: closes the split view and clears the trace state."""
        self._trace_open = False
        self._trace_root = None
        self._trace_cursor = None
        self._trace_index = 0
        self._trace_scroll = 0
        self._render_panes()

    def _trace_view_rows(self) -> int:
        """Trace rows the trace pane shows.

        The border title draws in the border, not the content: a verified
        bordered Static reports its content height as `size.height`. Only
        the two edge markers ("earlier rows" / "N more rows") can share the
        space, so `height - 2` rows fit; floor at one row and cap the
        render at TRACE_MAX_ROWS. A pane hidden by the zoom reports zero
        height - keep the last real height, like `_follow` does.
        """
        height = self._trace_widget.size.height or self._trace_pane_rows
        return max(1, min(TRACE_MAX_ROWS, height - 2))

    def _scroll_trace_into_view(self, count: int) -> None:
        """Slide the window so the cursor line stays inside it."""
        view = self._trace_view_rows()
        if self._trace_index < self._trace_scroll:
            self._trace_scroll = self._trace_index
        elif self._trace_index >= self._trace_scroll + view:
            self._trace_scroll = self._trace_index - view + 1
        self._trace_scroll = max(0, min(self._trace_scroll, max(0, count - view)))

    def _trace_move(self, delta: int) -> None:
        """j/k in the trace pane: walk the visible tree lines.

        The cursor stays inside the visible window: a move past an edge
        slides the scroll offset, so the highlighted line never renders
        below the pane's clip.
        """
        lines = self._trace_visible()
        if not lines:
            self.bell()
            return
        self._trace_index = max(0, min(len(lines) - 1, self._trace_index + delta))
        self._trace_cursor = lines[self._trace_index]["node"]
        self._scroll_trace_into_view(len(lines))
        self._render_panes()

    def _trace_enter(self) -> None:
        """enter/right in the trace pane: expand the highlighted row's edge."""
        lines = self._trace_visible()
        if not (0 <= self._trace_index < len(lines)):
            self.bell()
            return
        if self._trace_expand(lines[self._trace_index]):
            self._scroll_trace_into_view(len(self._trace_visible()))
            self._render_panes()
        else:
            self.bell()

    def _trace_back(self) -> None:
        """backspace/left in the trace pane: close one tree level."""
        if self._trace_collapse():
            self._scroll_trace_into_view(len(self._trace_visible()))
            self._render_panes()
        else:
            # at the trace root: backspace closes the split view
            self._close_trace()

    def action_trace_drivers(self) -> None:
        self._open_trace("drivers")

    def action_trace_loads(self) -> None:
        self._open_trace("loads")

    def action_dump_trace(self) -> None:
        cursor = self._trace_cursor
        if cursor is None:
            self.trace_dump = ""
            return
        parts: list[str] = []
        node: TraceNode | None = cursor
        while node is not None:
            parts.append(self._trace_head_label(node))
            node = node.parent
        parts.reverse()
        self.trace_dump = " ▶ ".join(parts)
        self.bell()

    def action_close_trace(self) -> None:
        """escape: close the trace split (a no-op when it is already closed)."""
        if self._trace_open:
            self._close_trace()

    def action_trace_back(self) -> None:
        """backspace: pop the trace while the split is open; a no-op otherwise."""
        if self._trace_open and self.active == "source":
            self._trace_back()

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
            label = base + ("✂" if scope.name in self._user_cut else "")
            node.label = self._styled_label(
                label, self._node_kind(node), bold=True
            )
        self._invalidate_trace_rows()
        self._render_panes()

    def _invalidate_trace_rows(self) -> None:
        """Cached rows may spell a user cut that just changed; drop them all."""
        stack = [self._trace_root] if self._trace_root is not None else []
        while stack:
            n = stack.pop()
            n.rows = None
            stack.extend(n.children)

    # ---- actions ----------------------------------------------------------

    def _select_pane(self, pane: str) -> None:
        """Make a pane active. Zoomed, the zoom moves with it."""
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

        The search modal covers the panes, so a zoom there changes nothing
        the user can see. Refuse it instead of leaving a surprise behind the
        modal. The trace split is just a pane, so zoom works there.
        """
        if self._search is not None:
            self.bell()
            return
        self.zoomed = not self.zoomed
        self._apply_layout()
        self._render_panes()

    def action_toggle_line_numbers(self) -> None:
        """n: show or hide the source view's line-number gutter."""
        self.line_numbers = not self.line_numbers
        self._render_panes()

    def action_show_cheatsheet(self) -> None:
        """?: open the navigation cheatsheet (full key map).

        The sheet lists the trace keys too when the trace split is open. It
        is also reachable while the split is up, where the trace stays
        mounted underneath. A second open is refused: on_event pops the
        sheet on the `?` key instead of reopening it.
        """
        if self._cheat is not None:
            return
        self.push_screen(CheatSheet(include_trace=self._trace_open))

    def action_open_search(self) -> None:
        """/: open the global search over modules/instances/ports/nets/files.

        Refused while the search modal is already open: the finder needs the
        full screen and its Input owns the printable keys. The trace split
        is just a pane, so the search may open over it.
        """
        if self._search is not None:
            self.bell()
            return
        self.push_screen(SearchScreen())

    async def on_event(self, event: events.Event) -> None:
        """The cheatsheet yields to any key: dismiss it, then dispatch on.

        Every nav key is an app-level binding, so textual would run it while
        the sheet stays up. Pop the sheet first, then let the same key reach
        the layer below (the panes, or the trace split it was opened from).
        escape and `?` only dismiss: escape must not close the trace split
        underneath too, and `?` must not reopen the sheet.

        A *committed* search blurs the query box, so no widget guards the
        app's priority printable bindings (q quit, y yank, X cut, ...).
        Stop every key except the finder's own there, or it would act on the
        tree behind the modal. '/' is the one exception, intercepted here
        before the app's priority open_search can steal it: the finder is
        already open, so '/' returns to the query box instead.
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
            # page up/down are priority app bindings. Under the search modal
            # its list owns up/down, but the ListView does not bind page
            # keys, so the app's page actions would fire and move the tree
            # behind the modal. Stop them; the cheatsheet above
            # already popped itself and lets the pane take the page key.
            if self._search is not None and event.key in ("pageup", "pagedown"):
                event.stop()
                return
        await super().on_event(event)

    def action_next_item(self) -> None:
        if self.active == "ports":
            if self.pane_ports:
                self.port_index = (self.port_index + 1) % len(self.pane_ports)
            self._render_panes()
        elif self.active == "source":
            if self._trace_open:
                self._trace_move(1)
            else:
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
            if self._trace_open:
                self._trace_move(-1)
            else:
                self.src_offset = max(0, self.src_offset - 1)
                self._render_panes()
        else:
            self._select(getattr(self, "_index", 1) - 1)

    def _page_rows(self) -> int:
        """Rows a page move covers: the active pane's visible height minus one.

        Editors keep one line of context across a page flip; so does this.
        A hidden pane (the zoom) reports zero, so floor the page
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
        """pagedown: a screenful of the active pane, editor-style.

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
            if self._trace_open:
                self._trace_move(self._trace_view_rows())
            else:
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
            if self._trace_open:
                self._trace_move(-self._trace_view_rows())
            else:
                self.src_offset = max(0, self.src_offset - self._page_rows())
                self._render_panes()
        else:
            self._select(max(0, getattr(self, "_index", 1) - self._page_rows()))

    def action_expand(self) -> None:
        """l: expand the highlighted scope (reveal its children).

        While the trace split is open, l/right crosses the highlighted
        boundary instead (the split owns the nav keys, as the modal did).
        """
        if self._trace_open and self.active == "source":
            self._trace_enter()
            return
        node = getattr(self, "_current_node", None)
        if node is None:
            return
        self._expand_to(node)
        self._ensure_children(node)  # lazy children must exist before render
        self._tree().scroll_to_node(node)
        self._render_panes()

    def action_collapse(self) -> None:
        """h: collapse the highlighted scope (hide its children).

        While the trace split is open, h/left pops the trace instead: the
        split owns the nav keys, as the modal did. At the trace root this
        closes the split.
        """
        if self._trace_open and self.active == "source":
            self._trace_back()
            return
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
        the I/O pane's selected port's declaration. While the trace split is
        open, enter crosses the highlighted boundary instead (the split owns
        the nav keys, as the modal did)."""
        if self._trace_open and self.active == "source":
            self._trace_enter()
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
        """Hierarchical path segments of a selection: [top, u_core] etc.

        Unlike `_selection_parts` (the yank path, which keys off the *module*
        scope a row stands for), the status line is the selection's full
        *hierarchical* path — so an instance row (fixture UIs, unresolved
        black boxes) reports its place in the tree (top.u_core), not the
        module name (core). A scope carries its own full path, so a root
        parked under the "other roots" group still reports its own name, not
        a false path through the design top. Ports/nets contribute nothing;
        their item is appended by `_selection_status`.
        """
        suffix: list[str] = []
        n = node
        while n is not None:
            data = n.data
            if data is not None:
                kind = data.get("kind")
                if kind == "scope":
                    return data["scope"].name.split(".") + suffix
                if kind == "instance":
                    suffix.insert(0, data["instance"].name)
            n = n.parent
        return suffix

    def _selection_key(self) -> tuple[str, int, int | None]:
        """Identity of the current yank target: pane, node, and port index."""
        if self.active == "ports" and self.pane_ports:
            return ("ports", id(self._current_node), self.port_index)
        return ("tree", id(self._current_node), None)

    def _clear_yank_on_move(self) -> None:
        """Drop a yank marker as soon as the selection leaves its target."""
        if not self._yank_flash:
            return
        if self._yank_flash_key == self._selection_key():
            return
        self._yank_flash = False
        self._yank_flash_key = None
        if self._yank_timer is not None:
            self._yank_timer.stop()
            self._yank_timer = None

    def _selection_status(self) -> RichText:
        """The top status line: yazi-style full path of the selection.

        Scope/instance rows show their hierarchical path (top.u_core.g[0].
        u_alu_g); a port/net leaf appends the item name (…u_alu_g.result).
        The path uses the current yank format, so `Y` previews the separator,
        top rule, array spelling and escaping that `y` will copy. Without a
        selection the design top stands in. A recent yank appends a short
        marker while the selection stays on the yanked target.
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
        parts = render_parts(self.yank_format, chain, element)
        if not parts:
            parts = [self.design.top]
        text = RichText()
        element_index = len(parts) - 1 if element is not None else None
        for i, part in enumerate(parts):
            if i:
                text.append(self.yank_format.separator, style="dim")
            if i == element_index:
                style = "bold #ffa62b"
            elif i == len(parts) - 1:
                # the terminal segment is the thing being *looked at*;
                # brighten it only when it is the whole selection
                style = "bold #ffffff"
            else:
                style = "#c9c9c9"
            text.append(part, style=style)
        if self._yank_flash and self._yank_flash_key == self._selection_key():
            text.append(YANKED_MARK, style=YANKED_STYLE)
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
        self._status_widget.update(self._selection_status())
        self.notify(f"yank format: {self.yank_format.name}")
        self.bell()

    def _show_yanked(self) -> None:
        """Mark the status line for a moment after a yank."""
        self._yank_flash = True
        self._yank_flash_key = self._selection_key()
        self._status_widget.update(self._selection_status())
        if self._yank_timer is not None:
            self._yank_timer.stop()
        self._yank_timer = self.set_timer(YANK_FLASH_SECONDS, self._clear_yanked)

    def _clear_yanked(self) -> None:
        self._yank_flash = False
        self._yank_flash_key = None
        self._yank_timer = None
        self._status_widget.update(self._selection_status())

    def _write_clipboard(self, payload: str) -> None:
        """Write the payload through the configured clipboard provider."""
        if self.clipboard_provider == "tmux":
            write_tmux(payload)
        else:
            self._write_osc(payload)

    def action_yank(self) -> None:
        """Yank the selection through the current format and provider."""
        if self.active == "ports" and self.pane_ports:
            port = self.pane_ports[self.port_index]
            scope = self._scope_data(self._current_node)
            chain = scope.name.split(".") if scope is not None else []
            payload = render_format(self.yank_format, chain, port.name)
        else:
            chain, element = self._selection_parts()
            payload = render_format(self.yank_format, chain, element)
        try:
            self._write_clipboard(payload)
        except ClipboardError as exc:
            self.notify(f"clipboard: {exc}", severity="error")
            self.bell()
            return
        self.yanked.append(payload)
        self.bell()
        self._show_yanked()

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
            or self.editor_command
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
        rewire the port map), for a port/net leaf its own declaration,
        for the I/O pane's selected port its declaration. A top-level
        module has no parent: both e and E land on its definition.
        """
        self._open_editor(self._effective_focus())

    def action_open_module_definition(self) -> None:
        """E: open $EDITOR at the *module definition* — the scope's own src
        (Scope.src carries the declaration span), regardless of where the
        selection sits. Leaves defer to the scope they belong to."""
        scope = self._scope_data(self._current_node)
        self._open_editor(_src_focus(scope.src) if scope is not None else None)

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        self._current_node = event.node
        self._render_panes()


def main(argv: list[str] | None = None) -> int:
    """`python -m tren.tui` - open the demo fixture or a manifest, no backends.

    A developer path for the bundled fixture IR and for a manifest that is
    already elaborated. Users open a design with `tren open`.
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
        default=None,
        help="IR cache directory (default: the cache.dir setting)",
    )
    args = parser.parse_args(argv)
    try:
        settings = load_config().config
    except ConfigError as exc:
        print(f"tren: {exc}", file=sys.stderr)
        return 2
    cache = args.cache if args.cache is not None else Path(settings.cache_dir)
    if args.manifest is not None:
        root = Path.cwd()
        design = elaborate(
            Manifest.read(args.manifest),
            root,
            cache=IrCache(cache, root=root),
        )
    else:
        design = load_design()
    TrenApp(design, settings=settings).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
