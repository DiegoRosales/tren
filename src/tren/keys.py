"""Declarative key map for the TUI.

One list drives both the on-screen navigation cheatsheet (`?`) and the README
"Keys" table, so the two cannot drift. The key strings and effects are plain
text; each consumer formats them (markdown backticks for the README table,
column padding for the TUI overlay).

Regenerate the README block after a binding change:
    nix develop . -c python tools/guide.py     (or: make guide)

`tests/test_keys.py` asserts the committed README block still matches this
list, so a forgotten regen fails `make test`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KeyDoc:
    keys: str  # one or more keys, space separated ("j k", "e E", "1 2 3")
    effect: str


SCREEN_KEYS: tuple[KeyDoc, ...] = (
    KeyDoc("1 2 3", "select the tree / source / I/O pane"),
    KeyDoc("z", "zoom the active pane to the full width, or restore the three columns"),
    KeyDoc("j k", "move down/up: in the tree, through the visible nodes; in source, scroll; in I/O, select a port"),
    KeyDoc("pageup pagedown", "move a screen up/down in the active pane: in the tree, jump to the next/previous page of nodes; in source, scroll a screen; in I/O, select a port a page away"),
    KeyDoc("l", "tree: expand the highlighted scope (reveal its children)"),
    KeyDoc("h", "tree: collapse the highlighted scope (hide its children)"),
    KeyDoc("arrows", "aliases: down/up = j/k, right/left = l/h"),
    KeyDoc("enter", "show the selection in the centre RTL pane with context - tree, the highlighted module; I/O, the selected port's declaration"),
    KeyDoc("ctrl+u", "tree: step up one level, current scope first - a port/net leaf jumps to its own module, a module row to its parent"),
    KeyDoc("/", "open the global search: fuzzy find over modules, instances, ports, nets and files (type to filter, enter to commit, n/N next/prev match, tab switches results/matched-lines pane, ctrl+enter to jump)"),
    KeyDoc("y", "yank the selection through the current path format (raw OSC 52 -> the terminal's clipboard)"),
    KeyDoc("Y", "cycle the yank format: sv -> vivado -> yosys -> vcd"),
    KeyDoc("d L", "trace drivers / loads of the selected net (splits the centre pane)"),
    KeyDoc("X", "cut/restore the highlighted scope (treat it as a black box)"),
    KeyDoc(";", "dump the current trace path as text"),
    KeyDoc("e", "open $EDITOR at the selection's declaration - a scope's instantiation in its parent, a port/net's own declaration"),
    KeyDoc("E", "open $EDITOR at the module definition (the module's declaration line); on a top module both e and E land there"),
    KeyDoc("?", "open this navigation cheatsheet"),
    KeyDoc("q", "quit"),
)

TRACE_KEYS: tuple[KeyDoc, ...] = (
    KeyDoc("enter or right", "crosses a boundary into the child scope"),
    KeyDoc("backspace or left", "pops the trace tree (at the root, closes the split)"),
    KeyDoc("down/up", "move through the rows"),
    KeyDoc("escape", "closes the split"),
)

# README markers around the regenerated block (tools/guide.py rewrites only
# what sits between them).
KEYS_BEGIN = "<!-- keys:begin -->"
KEYS_END = "<!-- keys:end -->"


def _backtick_keys(keys: str) -> str:
    """'enter or right' -> '`enter` or `right`' - one backtick per key."""
    tokens = (token if token == "or" else f"`{token}`" for token in keys.split())
    return " ".join(tokens)


def render_keys_table() -> str:
    """The README table body for the main screen keys."""
    lines = ["| Key | Effect |", "|---|---|"]
    lines.extend(
        f"| {_backtick_keys(doc.keys)} | {doc.effect} |" for doc in SCREEN_KEYS
    )
    return "\n".join(lines)


def render_trace_paragraph() -> str:
    """The README prose the trace-split keys: one sentence, plus the pane
    chrome (a bordered pane below the source, its title naming the head)."""
    phrases = ", ".join(f"{_backtick_keys(doc.keys)} {doc.effect}" for doc in TRACE_KEYS)
    return (
        f"In the **trace split**: {phrases}."
        " The split is a bordered pane below the source view."
        " The pane title names the trace head and the visible row range."
    )


def render_readme_block() -> str:
    """The whole README "Keys" generated block, markers included."""
    return "\n".join(
        [
            KEYS_BEGIN,
            "",
            render_keys_table(),
            "",
            render_trace_paragraph(),
            "",
            KEYS_END,
        ]
    )