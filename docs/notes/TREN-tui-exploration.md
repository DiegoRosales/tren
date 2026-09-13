<!--
  MIRROR of a Nextcloud note — NOT the source of truth.
  source     : Nextcloud Notes app, category "TREN"
  note_id    : 1503470
  title      : TREN — TUI framework exploration & recommendations
  etag       : f86c515274ff33bde21d25653df9843d
  mirrored   : 2026-09-13
  The Nextcloud note is canonical; this file may drift. Refresh by re-fetching
  the note (the Nextcloud editor reformats markdown on save).
-->
# TREN — TUI framework exploration & recommendations

Date 2026-09-13 · hands-on in the nix dev shell: rust 1.98.1 + ratatui
0.30.2 (tui_ratatui/) and Python 3.14 + textual 8.2.8 (tui_textual/).
Verified by cargo test, a textual Pilot headless run, and real-terminal
tmux screenshots (out/shot-*.ansi). Alternatives in tui_survey.md (desk-only,
marked assumed, not installed).

## Verdict

Build the TREN spike/MVP on textual 8.2; keep ratatui 0.30 as the rewrite
target. Both have now run the exact wanted screen (tree | source focus |
module I/O) and 1/2/3 pane selection, all verified.

Why:

- textual maps to TREN's needs almost one-to-one: built-in Tree widget,
  native async workers (the slow-elaboration problem), CSS-like 3-pane
  layout, focus/key bindings, and a real headless test harness (Pilot).
  Each of those is a feature TREN wants, and each cost ~0 code because it is
  a framework feature.
- ratatui did the same thing with ~65% more code and no framework help
  (~330 vs ~200 LOC): we hand-rolled the tree cursor, layout constraints,
  event routing and source scrolling, and fought a churned 0.30 API and a
  newer Rust dialect. It works — 7/7 unit tests + tmux frames — but every
  TREN feature is a feature you must write.
- The catch is not the demo, it is scale: ratatui's reasons to exist are
  exactly the triggers the concept note set — a single static binary, the
  100k-instance ceiling, and raw terminal-output control (OSC 52, cursor,
  mouse). None of that is decided by this week's demos.
- So the decision rule stays the note's, now with evidence: prove the
  product on textual (fast iteration, CI-testable), promote to ratatui when
  and if a real 100k-instance IR shows the ceiling is real. Keep the IR and
  navigation model widget-agnostic either way; both demos already separate
  them, so the TUI layer stays disposable.
- Caveats both demos proved (not guessed): OSC 52 yank is app code in both
  (raw escape via the framework's output path — easy in ratatui/crossterm,
  a low-level write in textual); pin ratatui 0.30 / textual 8.2 (both
  churn); lazy scope population on expand is mandatory.

## How TUI frameworks work (no prior knowledge needed)

A terminal UI is not a GUI toolkit: there is no pixel canvas. Everything a TUI
framework does rests on four primitives.

1. The terminal is a byte stream, and the app is a state machine.
   The app writes text + escape sequences to stdout and reads keystrokes
   (also escape sequences) from stdin. "Drawing" means emitting characters and
   cursor/color control sequences. The terminal emulator (alacritty, tmux, ...)
   is the renderer. On startup the framework switches the terminal to raw mode
   (termios: no echo, no line buffering), usually onto the alternate screen,
   and restores it on exit.
2. Every frame, the app re-renders from its own state, but redraw is a diff.
   Keeping an in-memory grid (cells x styles), the framework renders the
   desired frame, diffs it against the previous frame, and sends only changed
   cells. That is why a TUI can feel fast over slow SSH. Both ratatui and
   textual do exactly this (ratatui: Buffer diff via crossterm; textual:
   Rich-rendered diff).
3. The event loop is yours (ratatui) or owned by the framework (textual).
   - ratatui: terminal.draw(fn) then event read in a loop you write. Widgets
     are just functions that paint into a buffer: no widget tree, no focus
     model, no scrolling container. You implement navigation, selection and
     any widget content yourself (the demo's step/descend/go_up are exactly
     that).
   - textual: you compose a widget tree and define action methods; textual
     runs the async event loop and does the layout (CSS-like containers),
     focus, scrolling, key bindings, diffing and repaint. Tree is a built-in
     widget with expandable, lazily-added children.
4. Input to action to repaint is the whole job. Both frameworks translate raw
   key bytes into structured keys; your handler mutates application state
   only; the next frame (or diff) reflects it. Slow work (TREN's elaboration)
   must never block the loop: call it from a thread/worker that posts a
   message back into the UI loop — native in textual (workers), a channel +
   poll-timeout in ratatui.

Not free in either: OSC 52 clipboard (needs the raw escape emitted by app
code; textual has no built-in API, ratatui emits it via crossterm commands),
and mouse support (both opt-in).

## The two verified candidates

| | ratatui 0.30 (Rust) | textual 8.2 (Python) |
|---|---|---|
| Loop / rendering | you own draw+input loop; widgets paint a Buffer | framework owns async loop; widgets + CSS layout |
| Tree widget | none (hand-rolled in the demo) | yes (expandable, lazy) |
| Scroll/focus | manual | built-in |
| Async for elaboration | thread + channel (your code) | run_worker (native) |
| Headless tests | pure-logic unit tests + tmux screenshots (verified) | run_test + pilot.press + SVG export (verified) |
| Raw output (OSC 52) | crossterm style Print commands | no API, custom low-level write (assumed) |
| Shipping | single binary | Python runtime (trivial under the nix flake) |
| Effort for the demo | ~330 LOC + 7 unit tests; fought the 0.30 dialect | ~200 LOC; 3 small quirks hit |

## The three-pane layout (user sketch) — verified in both

The wanted screen is three panes, not Miller columns:

- Left: hierarchy tree, filesystem-style.
- Center: the RTL source with the module you sit on focused/highlighted (e.g.
  the instantiation block "my_module #( .some_param(32) ) hier_1 ( .clk(clk), ... )").
- Right: the module's I/O list.

All three panes are plain framework material:

- Left: textual Tree widget; ratatui hand-rolled tree + cursor (both done).
  The hard part is lazy loading, not drawing.
- Center: a styled text region, not a full editor. textual: Static updated
  with a rich Text whose focused line range is styled. ratatui: a Paragraph
  built per line with patch_style — that is exactly how the highlight becomes
  an ANSI style. The data is already in TREN's IR: the src attribute names
  the file, and elaboration gives the declaration range to highlight.
- Right: a text list of the module's ports (done in both).

Both demos now implement exactly this layout and re-verify: ratatui cargo
test 7/7 + tmux frames (center/right panes asserted in out/shot3-*.ansi),
and the textual Pilot test asserts the source pane shows "core #(32) u_core("
with ".alu_result(out)" and that the right pane lists the ports. Nothing
changes in the verdict above; the layout is a Horizontal/CSS split in textual
versus one more horizontal constraint in ratatui.

## Pane selection with 1/2/3 — added and verified in both

Pressing 1/2/3 selects tree / source / I/O, and j/k/l/h/y then act on the
active pane:

- pane 1 (tree): j/k move the scope cursor, l descends, h ascends.
- pane 2 (source): j/k scroll the source viewport; the focus highlight tracks
  absolute line numbers, so the yellow band slides with the text; a scrolled
  gutter line appears past the top.
- pane 3 (I/O): j/k move the port selection, y yanks scope.port.

Verified: textual Pilot test presses 1/2/3 and asserts the routing
(active pane state, source offset, port index, yanked port name, tree
movement). ratatui unit-tested (7/7) plus a tmux smoke (out/shot4-*.ansi)
showing the status bar pane marker, scrolled source, and yanked port path.

## What the demos proved

- ratatui: cargo test 7/7 green (navigation logic + source/I/O fixture +
  pane routing); tmux frames show real terminal rendering, correct
  top-to-core-to-alu descent via injected keys, the 3-pane layout with focus
  highlight, and 1/2/3 pane selection.
- textual: the Pilot headless run asserts structure, j/l navigation,
  expansion, source-pane focus, I/O-pane sync, 1/2/3 pane routing, yank,
  h-ascent; SVG output.

## Recommendation (recap — see Verdict)

1. Use textual for the spike/MVP. The Tree widget, async workers, key
   bindings and Pilot tests map one-to-one onto TREN's needs (tree + source
   focus + I/O + pane selection + lazy scopes + slow elaboration + scriptable
   CI tests). The note's "Python wins on prototyping speed" is now
   load-bearing. Documented quirks: read-only cursor_node, no visible_nodes
   property, priority=True bindings, cache widget refs in on_mount.
2. Keep ratatui as the rewrite target, and factor for it now. The IR contract
   must stay widget-agnostic (it already is: scope tree + bit map + driver
   sets). Ratatui's win — single binary, 100k-instance ceiling, raw-output
   control — is exactly the "if it earns it" trigger in the note. Decide on a
   real 100k-instance JSON, not before.
3. Either way, plan for these cross-cutting items (both demos hit them):
   - yank is raw OSC 52 in the real build (textual needs a low-level write
     path — verify on the CI terminal, marked assumed);
   - lazy scope population on expand; never materialize the full graph;
   - keep all navigation logic model-only and unit-tested; the TUI layer is
     then disposable.

## Version record (pin these)

rustc/cargo 1.98 · ratatui 0.30.2 + ratatui-core 0.1.2 + crossterm 0.29.0 ·
Python 3.14 · textual 8.2.8.