#!/usr/bin/env bash
# Real-terminal OSC 52 + editor-jump round trip (deck card 214 acceptance).
#
# Runs on a PRIVATE tmux server (`-L trenosc214`) so nothing here touches your
# own tmux state — never `tmux kill-server` without the `-L` socket.
#
# Verifies, against the app's actual terminal output stream:
#   1. `y` writes the raw OSC 52 escape through the app's low-level output
#      path. The pane's byte stream (what any terminal would receive) is
#      captured with `pipe-pane` and decoded: it must be the formatted SV
#      path of the selection, top.u_core.
#   2. `e` spawns $EDITOR at the focused file:line (a fake editor logs its
#      argv): must be "+77 rtl/demo.sv" (core's instantiation site).
#
# Why pipe-pane and not `set-clipboard on` + `show-buffer`: tmux only stores
# OSC 52 in its copy buffer when an *attached client* advertises the Ms
# terminal capability; a headless, client-less server relays the sequence to
# nothing (verified on tmux 3.7c). pipe-pane captures the pane's actual
# output bytes — the authoritative "what the terminal receives" check — and
# works headless. Interactive uses get the buffer too.
#
# Run:  nix develop . -c bash tools/osc52_roundtrip.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SOCK="trenosc214"
SESS="roundtrip"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/tren-osc52.XXXXXX")"
trap 'tmux -L "$SOCK" kill-server 2>/dev/null || true; rm -rf "$TMP"' EXIT

cat >"$TMP/fake_editor.sh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$FAKE_EDITOR_LOG"
EOF
chmod +x "$TMP/fake_editor.sh"

tmux -L "$SOCK" new-session -d -s "$SESS" -x 120 -y 34
tmux -L "$SOCK" pipe-pane -t "$SESS" "cat > '$TMP/pane.raw'"
tmux -L "$SOCK" send-keys -t "$SESS" \
  "cd '$REPO' && export PYTHONPATH=src EDITOR=$TMP/fake_editor.sh FAKE_EDITOR_LOG=$TMP/editor.log && python -m tren.tui --manifest rtl/demo.manifest.json" Enter

sleep 6  # elaboration + textual startup
tmux -L "$SOCK" send-keys -t "$SESS" j l j y   # select top, l to unfold, j to u_core, y
sleep 2

PAYLOAD="$(python3 - "$TMP/pane.raw" <<'EOF'
import base64, re, sys
raw = open(sys.argv[1], "rb").read()
m = re.search(rb"\x1b]52;c;([A-Za-z0-9+/=]+)\x07", raw)
print(base64.b64decode(m.group(1)).decode() if m else "OSC52_NOT_FOUND")
EOF
)"
echo "OSC 52 payload:   ${PAYLOAD:-<empty>}"
[ "$PAYLOAD" = "top.u_core" ] || { echo "FAIL: OSC 52 payload mismatch"; exit 1; }

tmux -L "$SOCK" send-keys -t "$SESS" e
sleep 2
LOG="$TMP/editor.log"
echo "editor argv:     $(cat "$LOG" 2>/dev/null || echo '<not run>')"
grep -q '^+77 rtl/demo.sv$' "$LOG" 2>/dev/null || {
  echo "FAIL: editor not invoked with +77 rtl/demo.sv"; exit 1
}

echo "PASS: OSC 52 byte-level round trip + editor jump on a real terminal"