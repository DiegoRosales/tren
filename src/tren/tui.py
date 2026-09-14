"""tren — 3-pane textual spike, driven by the IR (tren.ir.Design).

Left:    scope tree (filesystem-like hierarchy; generate scopes nested)
Center:  source of the module you're sitting on — the *actual RTL file*
         loaded at the scope's src (file:line already in IR), with the
         focused range highlighted. Semantics: the module's *instantiation
         in its parent* (its src), else the module declaration; falls back
         to the inline rtl snippet when no file src exists (fixture).
Right:   the module's I/O (ports)

Auto-follow: on scope change the focus band is kept in view
(src_offset = focus_start - rows/2, clamped). `e` opens the editor (stub)
at the focused line.

Keys: j/k move, l open/expand, h up, 1/2/3 pane select, y yank, e editor,
q quit. Headless Pilot tests:  python test_drive.py [--manifest …]
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
from pathlib import Path

from rich.text import Text as RichText
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Label, ListItem, ListView, Static, Tree

from .elaborate import elaborate
from .formats import BY_NAME, DEFAULT, PathFormat, render as render_format
from .ir import Design, Scope, design_from_fixture
from .manifest import Manifest

FIXTURE = Path(__file__).resolve().parents[2] / "tui" / "fixtures" / "demo-tree.json"


def load_design(fixture: Path | None = None) -> Design:
    return design_from_fixture(fixture or FIXTURE)


def _scope_parent(path: str) -> str | None:
    parts = path.rsplit(".", 1)
    return parts[0] if len(parts) == 2 else None


def _parse_src(src: str | None) -> tuple[str, int, int] | None:
    """'rtl/demo.sv:77.14-77.65' -> ('rtl/demo.sv', 77, 77)."""
    if not src:
        return None
    file, _, rest = src.rpartition(":")
    if not file:
        return None
    m = re.match(r"(\d+)(?:\.\d+)?(?:-(\d+)(?:\.\d+)?)?$", rest)
    if m is None:
        return None
    lo = int(m.group(1))
    hi = int(m.group(2) or lo)
    return file, lo, hi


class SourcePane(Static):
    """Center: RTL source with the focused range highlighted."""


class PortsPane(Static):
    """Right: I/O list of the current module."""


class TraceScreen(ModalScreen[None]):
    """Drivers/loads of the trace head: enter descends, backspace pops.

    Enter is handled via ListView.Selected (ListView owns the enter key).
    List navigation uses arrow keys: the App's j/k bindings are
    priority=True and would shadow same-letter modal bindings (verified).
    """

    BINDINGS = [
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

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        app = self._app
        idx = event.index if event.index is not None else self.listwidget.index
        if idx is not None and 0 <= idx < len(self.rows):
            if app._trace_descend(self.rows[idx]):
                self._refresh()
                return
        app.bell()

    def action_pop_trace(self) -> None:
        app = self._app
        if app._trace_pop():
            self._refresh()
        else:
            app.bell()


class TrenApp(App[None]):
    TITLE = "tren (textual)"
    BINDINGS = [
        Binding("1", "select_pane_tree", "tree", priority=True),
        Binding("2", "select_pane_source", "source", priority=True),
        Binding("3", "select_pane_ports", "ports", priority=True),
        Binding("j", "next_item", "down", priority=True),
        Binding("k", "prev_item", "up", priority=True),
        Binding("l", "open_member", "enter", priority=True),
        Binding("h", "up_scope", "back", priority=True),
        Binding("y", "yank", "yank", priority=True),
        Binding("Y", "cycle_format", "fmt", priority=True),
        Binding("d", "trace_drivers", "drivers", priority=True),
        Binding("L", "trace_loads", "loads", priority=True),
        Binding("X", "toggle_cut", "cut", priority=True),
        Binding(";", "dump_trace", "dump", priority=True),
        Binding("e", "open_editor", "editor", priority=True),
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
        self.src_offset = 0
        self.port_index = 0
        self.pane_ports: list[Port] = []
        self._scopes = {s.name: s for s in design.scopes}
        self._focus: tuple[str, int, int] | None = None
        self._file_cache: dict[str, list[str]] = {}
        self.editor_target: str | None = None
        # trace stack (scope_path, net_name); d/L populates it; ; dumps
        self._trace: list[tuple[str, str]] = []
        self._trace_dir = "drivers"
        self._modal: "TraceScreen | None" = None
        self.trace_dump: str = ""
        # user cuts (X): scope paths made opaque at runtime
        self._user_cut: set[str] = set()

    def compose(self) -> ComposeResult:
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
        # textual's select-time auto-expand runs in an async handler that
        # *toggles*: under a key burst it re-orders against our visible-walk
        # (and pre-expanding here would make it collapse). Disable it and
        # expand deterministically in _select.
        self._tree_widget.auto_expand = False
        self._source_widget = self.query_one("#source")
        self._ports_widget = self.query_one("#ports")
        self._index = -1
        self._current_node = None
        # three columns: tree / source / ports; panes fill the viewport so
        # size.height is stable (not content-driven) for auto-follow
        for selector, width in (("#tree", "32%"), ("#source", "48%"), ("#ports", "20%")):
            widget = self.query_one(selector)
            widget.styles.width = width
            widget.styles.height = "1fr"

        roots = [s for s in self.design.scopes if _scope_parent(s.name) not in self._scopes]
        for scope in roots:
            self._add_scope_node(self._tree_widget.root, scope)
        self._render_panes()

    # ---- tree construction ------------------------------------------------

    def _add_scope_node(
        self, parent: Tree.Node, scope: Scope, label: str | None = None
    ) -> Tree.Node:
        base_label = label or f"{scope.name}/"
        node = parent.add(
            base_label, data={"kind": "scope", "scope": scope, "label": base_label}
        )
        for inst in scope.instances:
            child = self._scopes.get(f"{scope.name}.{inst.name}")
            if child is not None:
                # the instance is a real sub-scope (elaborated IR): nest it
                marker = "!" if inst.blackbox else ""
                self._add_scope_node(node, child, f"{inst.name}/{marker}")
            else:
                # fixture shape: the instance's module scope supplies ports
                inst_node = node.add(
                    f"{inst.name}/", data={"kind": "instance", "instance": inst}
                )
                inst_scope = self._scopes.get(inst.module) if inst.module else None
                if inst_scope is not None:
                    self._add_ports_nets(inst_node, inst_scope)
        self._add_ports_nets(node, scope)
        return node

    def _add_ports_nets(self, node: Tree.Node, scope: Scope) -> None:
        for port in scope.ports:
            node.add_leaf(
                f"port {port.name}  {port.direction}[{port.width}]",
                data={"kind": "port", "port": port},
            )
        for net in scope.nets:
            node.add_leaf(
                f"net {net.name}  [{net.width}]",
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

        An instance node carries its own src (the instantiation site). A
        sub-scope node focuses its instantiation in the parent (the parent's
        instance of it); a root scope focuses its module declaration.
        """
        node = node or self._current_node
        while node is not None and node.data is not None:
            data = node.data
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
        # floor the viewport: early renders report a tiny/zero height
        rows = max(10, self._source_widget.size.height or 30)
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
        # auto_expand=False is set in compose (async toggle handler races key
        # bursts), so expansion is deterministic here: selecting always
        # shows the node's children, like textual's own auto-expand.
        self._expand_to(node)
        self._tree().scroll_to_node(node)
        self._render_panes()

    def _render_source(
        self, focus: tuple[str, int, int] | None, scope: Scope | None
    ) -> RichText:
        src = RichText()
        if focus is not None:
            file, lo, hi = focus
            lines = self._file_lines(file)
            if not lines:
                src.append(f"(cannot read {file})\n", style="dim italic")
                return src
            src.append(f"· {file}  lines {lo}-{hi}\n", style="dim")
            view = lines[self.src_offset :]
            if self.src_offset:
                src.append("…… (↑ scroll to see earlier lines)\n", style="dim italic")
            for i, line in enumerate(view):
                abs_i = self.src_offset + i + 1
                style = "bold #ffa62b" if lo <= abs_i <= hi else "dim white"
                src.append(f"{line}\n", style=style)
        elif scope is not None:
            # fixture fallback: inline rtl snippet with the rtl_focus band
            rtl = scope.rtl
            lo, hi = scope.rtl_focus
            view = rtl[self.src_offset :]
            if self.src_offset:
                src.append("…… (↑ scroll to see earlier lines)\n", style="dim italic")
            for i, line in enumerate(view):
                abs_i = i + 1 + self.src_offset
                style = "bold #ffa62b" if lo <= abs_i <= hi else "dim white"
                src.append(f"{line}\n", style=style)
        return src

    def _render_panes(self) -> None:
        scope = self._scope_data(self._current_node)
        focus = self._focus_for(self._current_node)
        if focus != self._focus:
            self._focus = focus
            if focus is not None:
                self._follow(focus)
        self._source_widget.update(self._render_source(focus, scope))
        # right: I/O list with selection
        ports = RichText()
        self.pane_ports = list(scope.ports) if scope is not None else []
        if scope is not None:
            for n, p in enumerate(self.pane_ports):
                direction = p.direction
                base = "#7adf77" if direction == "out" else "#a6c9ff"
                sel = "▸ " if n == self.port_index else "  "
                color = f"bold {base}" if n == self.port_index else base
                ports.append(f"{sel}{p.name}", style=color)
                ports.append(f"  {direction}[{p.width}]\n", style="dim white")
        # black-box / user-cut provenance header on the I/O pane
        if scope is not None:
            header = RichText()
            if scope.blackbox:
                header.append("▊ BLACK BOX\n", style="bold yellow")
            if scope.name in self._user_cut:
                header.append("✂ USER CUT\n", style="bold red")
            ports = header + ports
        self._ports_widget.update(ports)
        # active-pane cue
        for pid, widget in (
            ("tree", self._tree_widget),
            ("source", self._source_widget),
            ("ports", self._ports_widget),
        ):
            background = "#262626" if pid == self.active else "#121212"
            widget.styles.background = background

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
        stack = list(self._tree().root.children)
        while stack:
            n = stack.pop()
            d = n.data
            if d is not None and d.get("kind") == "scope" and d["scope"].name == path:
                return n
            stack.extend(n.children)
        return None

    def _find_net_leaf(self, scope_node: Tree.Node, net_name: str) -> Tree.Node | None:
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

    def _select_path(self, scope_path: str, net_name: str | None = None) -> None:
        scope_node = self._find_scope_node(scope_path)
        if scope_node is None:
            return
        self._expand_to(scope_node)
        if net_name is not None:
            leaf = self._find_net_leaf(scope_node, net_name)
            if leaf is not None:
                self._expand_to(leaf)
                self._select(self._visible().index(leaf))
                return
        self._select(self._visible().index(scope_node))

    def _open_trace(self, direction: str) -> None:
        if self._modal is not None:
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

    def action_select_pane_tree(self) -> None:
        self.active = "tree"
        self._render_panes()

    def action_select_pane_source(self) -> None:
        self.active = "source"
        self._render_panes()

    def action_select_pane_ports(self) -> None:
        self.active = "ports"
        self._render_panes()

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

    def action_open_member(self) -> None:
        node = getattr(self, "_current_node", None)
        if node is None:
            return
        node.toggle()
        self._render_panes()

    def action_up_scope(self) -> None:
        node = getattr(self, "_current_node", None)
        if node is None or node.parent is None:
            return
        try:
            self._select(self._visible().index(node.parent))
        except ValueError:
            pass

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

    def action_open_editor(self) -> None:
        """Open $EDITOR at the focused line (suspend; record only headless)."""
        focus = self._focus_for(self._current_node)
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

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        self._current_node = event.node
        self._render_panes()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        help="elaborate this manifest instead of the demo fixture",
    )
    args = parser.parse_args()
    if args.manifest is not None:
        design = elaborate(Manifest.read(args.manifest), Path.cwd())
    else:
        design = load_design()
    TrenApp(design).run()
