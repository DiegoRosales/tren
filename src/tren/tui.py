"""tren — 3-pane textual spike, now driven by the IR (tren.ir.Design).

Left:    scope tree (filesystem-like hierarchy)
Center:  source of the module you're sitting on, with the focused range
         (rtl_focus) highlighted
Right:   the module's I/O (ports)

Data loads through the IR module (tren.ir.design_from_fixture) — the
hand-written demo-tree.json fixture path first; a real manifest can be
elaborated via ``--manifest``. The tree/source/ports all read Design/Scope/
Instance/Port objects, never raw JSON.

Keys: j/k move, l open/expand, h up, 1/2/3 pane select, y yank, q quit.
Headless Pilot test:  python test_drive.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rich.text import Text as RichText
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Static, Tree

from .elaborate import elaborate
from .ir import Design, Scope, design_from_fixture
from .manifest import Manifest

FIXTURE = Path(__file__).resolve().parents[2] / "tui" / "fixtures" / "demo-tree.json"


def load_design(fixture: Path | None = None) -> Design:
    return design_from_fixture(fixture or FIXTURE)


class SourcePane(Static):
    """Center: RTL source with the focused range highlighted."""


class PortsPane(Static):
    """Right: I/O list of the current module."""


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
        Binding("q", "quit", "quit", priority=True),
    ]

    def __init__(self, design: Design) -> None:
        super().__init__()
        self.design = design
        self.yanked: list[str] = []
        self.active = "tree"  # which pane j/k/l/h/y act on: tree | source | ports
        self.src_offset = 0
        self.port_index = 0
        self.pane_ports: list[Port] = []

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Tree("design", data=None, id="tree")
            yield SourcePane("", id="source")
            yield PortsPane("", id="ports")
        yield Footer()

    def on_mount(self) -> None:
        self._tree_widget = self.query_one("#tree")
        self._source_widget = self.query_one("#source")
        self._ports_widget = self.query_one("#ports")
        self._index = -1
        self._current_node = None
        # three columns: tree / source / ports
        for selector, width in (("#tree", "32%"), ("#source", "48%"), ("#ports", "20%")):
            self.query_one(selector).styles.width = width

        tree = self._tree_widget
        for scope in self.design.scopes:  # fixture/module order: top first
            scope_node = tree.root.add(
                f"{scope.name}/", data={"kind": "scope", "scope": scope}
            )
            for inst in scope.instances:
                marker = "!" if inst.blackbox else ""
                inst_node = scope_node.add(
                    f"{inst.name}/{marker}",
                    data={"kind": "instance", "instance": inst},
                )
                inst_scope = self.design.scope(inst.module) if inst.module else None
                if inst_scope is not None:
                    for port in inst_scope.ports:
                        inst_node.add_leaf(
                            f"port {port.name}  {port.direction}[{port.width}]",
                            data={"kind": "port", "port": port},
                        )
                    for net in inst_scope.nets:
                        inst_node.add_leaf(
                            f"net {net.name}  [{net.width}]",
                            data={"kind": "net", "net": net},
                        )
            for port in scope.ports:
                scope_node.add_leaf(
                    f"port {port.name}  {port.direction}[{port.width}]",
                    data={"kind": "port", "port": port},
                )
            for net in scope.nets:
                scope_node.add_leaf(
                    f"net {net.name}  [{net.width}]", data={"kind": "net", "net": net}
                )
        self._render_panes()

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
                return self.design.scope(inst.module)
            node = node.parent
        return None

    def _select(self, index: int) -> None:
        nodes = self._visible()
        if not nodes:
            return
        index %= len(nodes)
        node = nodes[index]
        self._index = index
        self._current_node = node
        self._tree().select_node(node)
        self._tree().scroll_to_node(node)
        self._render_panes()

    def _render_panes(self) -> None:
        scope = self._scope_data(self._current_node)
        # center: instantiation/source text with focused range highlighted
        src = RichText()
        if scope is not None:
            rtl = scope.rtl
            lo, hi = scope.rtl_focus
            view = rtl[self.src_offset :]
            if self.src_offset:
                src.append("…… (↑ scroll to see earlier lines)\n", style="dim italic")
            for i, line in enumerate(view):
                abs_i = i + 1 + self.src_offset
                style = "bold #ffa62b" if lo <= abs_i <= hi else "dim white"
                src.append(f"{line}\n", style=style)
        self._source_widget.update(src)
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
        self._ports_widget.update(ports)
        # active-pane cue
        for pid, widget in (
            ("tree", self._tree_widget),
            ("source", self._source_widget),
            ("ports", self._ports_widget),
        ):
            background = "#262626" if pid == self.active else "#121212"
            widget.styles.background = background

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

    def action_yank(self) -> None:
        if self.active == "ports" and self.pane_ports:
            port = self.pane_ports[self.port_index]
            self.yanked.append(f"{port.name} ({port.direction})")
            self.bell()
            return
        node = getattr(self, "_current_node", None)
        if node is None:
            return
        self.yanked.append(node.label.plain)
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
