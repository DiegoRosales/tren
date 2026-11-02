#!/usr/bin/env bash
# Real-terminal render check of the active-pane cue.
#
# Runs on a PRIVATE, run-specific tmux server (`-S <tmp>/tmux.sock`) so nothing
# here touches your own tmux state or a concurrent run of this gate - never
# `tmux kill-server` without the private socket.
#
# Why this exists as a layer-4 gate (docs/verification.md): Pilot can assert
# `widget.styles.background`, but not what the terminal paints. Two real bugs
# lived in exactly that gap, and both are asserted here:
#
#   1. textual 8.2 `Tree` caches rendered line *strips* keyed on
#      (line, cursor, hover, width, updates, pseudo-classes) - the background
#      is NOT part of the key. Changing `styles.background` therefore repainted
#      only the blank region below the nodes, while every line carrying a node
#      kept the background it was first rendered with. Style state was correct;
#      the screen was not.
#   2. `can_focus = False` does not blur a widget that is ALREADY focused, and
#      on a real terminal textual auto-focuses the Tree before on_mount runs.
#      A focused Tree picks up `Tree:focus { background-tint: $foreground 5% }`,
#      lifting the whole tree column ~4% - an idle tree still read as active.
#      Headless Pilot reports `focused=None`, so layer 3 cannot see this at all.
#
# Assertion: pressing 1/2/3 paints exactly one pane at PANE_BG_ACTIVE and the
# other two at PANE_BG_IDLE, on every row - including rows carrying tree nodes.
#
# Run:  nix develop . -c bash tools/pane_cue_capture.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SESS="panecue"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/tren-panecue.XXXXXX")"
SOCK="$TMP/tmux.sock"
trap 'tmux -S "$SOCK" kill-server 2>/dev/null || true; rm -rf "$TMP"' EXIT

# -f /dev/null ignores ~/.tmux.conf; the explicit shell ignores the user's
# interactive rc files, so the gate cannot fail because of local zsh setup.
tmux -S "$SOCK" -f /dev/null new-session -d -s "$SESS" -x 120 -y 34 \
  -- bash --noprofile --norc
tmux -S "$SOCK" send-keys -t "$SESS" \
  "cd '$REPO' && export PYTHONPATH=src TREN_SYSTEM_CONFIG='$TMP/no-config' TREN_USER_CONFIG='$TMP/no-config' TREN_PROJECT_CONFIG='$TMP/no-config' && python -m tren.tui --manifest tests/rtl/demo.manifest.json" Enter
sleep 8  # elaboration + textual startup

# Unfold the tree so the sampled rows carry real nodes - the stale-line-cache
# bug is invisible on the blank region below the tree.
tmux -S "$SOCK" send-keys -t "$SESS" j; sleep 1
tmux -S "$SOCK" send-keys -t "$SESS" l; sleep 1
tmux -S "$SOCK" send-keys -t "$SESS" j; sleep 1

for pane in 1 2 3; do
  tmux -S "$SOCK" send-keys -t "$SESS" "$pane"
  sleep 1
  tmux -S "$SOCK" capture-pane -e -p -t "$SESS" >"$TMP/pane-$pane.ansi"
done

python3 - "$TMP" "$REPO" <<'EOF'
import pathlib
import re
import sys

tmp, repo = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
sys.path.insert(0, str(repo / "src"))
from tren.tui import PANE_BG_ACTIVE, PANE_BG_IDLE  # noqa: E402

SGR = re.compile(r"\x1b\[([0-9;]*)m")
RGB = re.compile(r"48;2;(\d+);(\d+);(\d+)")
# a column comfortably inside each pane (120 cols: 32% / 48% / 20%)
COLUMNS = {"tree": 20, "source": 55, "ports": 105}
ROWS = (2, 4, 8, 14)  # rows 2-8 carry tree nodes; 14 is the blank region below


def background_at(line: str, col: int) -> str | None:
    """The background colour the terminal has in effect at a visible column.

    tmux emits SGR minimally: a cell whose background matches what is already
    in effect carries no code, so this walks the line and tracks state.
    """
    bg, visible, cursor = None, 0, 0
    for match in SGR.finditer(line):
        start, end = match.span()
        text = SGR.sub("", line[cursor:start])
        if visible <= col < visible + len(text):
            return bg
        visible += len(text)
        params = match.group(1)
        if (rgb := RGB.search(params)) is not None:
            bg = "#%02x%02x%02x" % tuple(int(v) for v in rgb.groups())
        elif params in ("", "0", "49"):
            bg = None
        cursor = end
    return bg


failures = []
for pane, active in (("1", "tree"), ("2", "source"), ("3", "ports")):
    lines = (tmp / f"pane-{pane}.ansi").read_text().splitlines()
    for row in ROWS:
        if row >= len(lines):
            failures.append(f"pane {pane}: screen has no row {row}")
            continue
        for name, col in COLUMNS.items():
            got = background_at(lines[row], col)
            want = PANE_BG_ACTIVE if name == active else PANE_BG_IDLE
            if name == active:
                ok = got == want
            else:
                # An idle pane may legitimately carry no background code (tmux
                # drops it when it matches what is in effect); what it must
                # never do is paint the active colour or anything lighter.
                ok = got in (want, None)
            if not ok:
                failures.append(
                    f"pane {pane} active, row {row}: {name} renders {got}, expected {want}"
                )
        print(
            f"  pane {pane} row {row:2d}: "
            + "  ".join(f"{n}={background_at(lines[row], c)}" for n, c in COLUMNS.items())
        )

# the status line (row 0) renders the selection's full path. The
# keys pressed above leave the cursor on u_core, so row 0 must read the
# hierarchical path, not the bare module name.
row0 = SGR.sub("", (tmp / "pane-1.ansi").read_text().splitlines()[0])
if "top.u_core" not in row0:
    failures.append(f"status line row 0: {row0!r} missing top.u_core")
else:
    print(f"  status line row 0: {row0.strip()!r}")

if failures:
    print("\nFAIL: the active-pane cue is not symmetric on a real terminal")
    for failure in failures:
        print(f"  {failure}")
    sys.exit(1)
print(f"\nPASS: exactly one pane at {PANE_BG_ACTIVE}, the rest idle, on every sampled row")
EOF
