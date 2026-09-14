# tren — Agent Guidelines & Project Instructions

Guidelines and conventions for AI agents and humans working in this
repository. Short on ceremony, long on preventing re-discovery.

## 0. What this repo is (and is not)

This is the **implementation** repo for TREN (TUI RTL Explorer & Navigator).
It currently holds the spike on textual. The evaluation harness that produced
the verified findings — dummy RTL, per-tool experiments, reports — lives in
the **sibling `tren_testing` directory** and belongs there, not here. Do not
copy harness artifacts into this repo; cite them instead.

## 1. Sources of truth

- **Design**: Nextcloud note *"TREN — RTL explorer TUI (concept)"*,
  note id `1503454`, category `TREN`. Read it before proposing architecture.
  **A mirror lives in this repo** at `docs/notes/TREN-concept.md` (plus the
  backend- and TUI-exploration reports mirrored alongside); if Nextcloud is
  unreachable, read the mirrors — but treat Nextcloud as canonical.
- **Verified findings / verdicts**: sibling `tren_testing/report.md`
  (backends), `tren_testing/report-tui.md` (framework verdict + how TUIs
  work), and `tren_testing/{t1_yosys,t3_slang,tui_textual}/findings.md`
  (reproductions). If a claim contradicts them, re-run the experiment first.
- **Task breakdown**: Nextcloud **Deck board "TREN"** (stacks = Spike / MVP /
  v1 / Later; cards carry context + acceptance criteria). Move a card to
  *In progress* before starting it and to *Done* when its acceptance criteria
  pass.

## 2. Decisions already made — do not re-derive

- **Backends** (verified against installed tools, yosys 0.69 / sv-lang 11.0):
  yosys `write_json` for connectivity; `slang-hier --params` and/or
  `slang --ast-json` for scope tree + resolved params. `slang --ast-json`
  flags that matter: `--ast-json-source-info`, `--ast-json-detailed-types`.
- **Yosys rules** (non-negotiable):
  - run `proc` — `write_json` rejects unprocessed designs;
  - never run `opt` — it prunes unobservable cells/nets out of the JSON;
  - never `flatten` — unflattened cells are the hierarchy;
  - bit IDs are **module-local and positional-only** across a boundary:
    match the k-th listed bit of a port, never the numbers;
  - module names are mangled `$paramod\name\param=value` — un-mangle and join
    with `slang-hier` names.
- **UI framework**: textual (pinned 8.2.8) for the spike/MVP; ratatui 0.30 is
  the earmarked rewrite target and must remain a drop-in swap (IR and
  navigation logic framework-agnostic). Screen = tree | source | I/O; keys
  j/k/l/h, 1/2/3 pane select, enter shows the module (centre), ctrl+u up a
  level, y yank (OSC 52), e editor jump. The guide in README.md § User guide
  is the keymap reference.
- **Black boxes are the normal case**: TREN always opens something. Port
  lists + `blackbox=1` come from yosys JSON already; user cuts toggle a scope
  to opaque.

## 3. Toolchain

Always run under the dev shell: `nix develop . -c <command>` — never bare
`python`/`yosys`/`slang` from the host. The flake pins Python 3.14 + textual
8.2.8 and provides yosys, sv-lang, rust, jq, rg. Version pins are recorded in
`README.md`; bump them deliberately and re-verify the tests.

For anything touching Xilinx tooling (Vivado / Vitis HLS) use the `#xilinx`
FHS sandbox — the tools need a conventional `/usr/lib` that NixOS lacks:
`nix develop .#xilinx` (x86_64-linux only) and work inside it. Note the env's
runScript shell is interactive-only: `nix develop .#xilinx -c <cmd>` does NOT
execute the command (verified on nix 2.x for buildFHSEnv .env outputs) — run
the Makefile inside the shell instead. The HLS example build flow mirrors
ipyfuse: `make setup_xilinx` writes `.xilinx_env` from `$XILINX_VITIS`,
`make <block>_HLS` re-synthesises one block under `test/hls_examples/hls/`.
First `nix develop .#xilinx` has a long one-time build (systemd/gcc from
source for the pinned nixpkgs rev).

## 4. Working conventions

- **The user guide lives in `README.md` (§ User guide). Keep it in sync in
  the same change as any binding / CLI / behaviour edit** — the keymap table,
  yank formats, trace/black-box semantics and the make targets are all
  documented there and verified by the Pilot drives; after editing it, run
  `make check` (unit + both drives) and, if a key/OSC path changed,
  `make roundtrip`.
- **Verify against the installed tool; never assert flags/formats from
  memory.** This project has burned real time on exactly that.
- **Distinguish verified from assumed**; carry the ⚠ convention from the
  design note if something is a guess.
- **Report negative results.** "X is not packaged / API Y is gone" is a
  finding that changes architecture (see `tui_textual/findings.md`).
- Python: 3.14, `from __future__ import annotations`, dataclasses for the IR,
  type hints, no comments unless the why is non-obvious. Keep three layers
  separate: CLI parsing → IR model → UI.
- Tests: pure logic as unit tests; UI flows via textual **Pilot**
  (`test_drive.py`). Headless and CI-able by design.
- Elaboration output is an on-disk cache keyed by
  `sha256(manifest + source mtimes)`; start instantly on cache hits.
- **Never `export PYTHONPATH=src`** at the shell level for runtimes that
  import textual — the dev shell exposes rich/textual through its own
  `PYTHONPATH`, and replacing it breaks `import rich`/`import textual`. The
  test files self-insert `src` via `sys.path.insert`; use
  `PYTHONPATH=src:$PYTHONPATH` only when you must (e.g. `python -c` smoke
  checks).

### Known gotchas (verified, keep for later)

- textual 8.2: `Tree.cursor_node` is read-only → track the cursor in the app;
  `visible_nodes` was removed → walk `node.children` (respect `is_expanded`);
  widget queries can race the first tick → cache refs in `on_mount()`; app
  binding `priority=True` wins over a focused widget; `export_screenshot()`
  returns **SVG**; rich style modifiers go first (`"bold #a6c9ff"` not
  `"#a6c9ff,bold"`).
- textual 8.2 **does** ship a clipboard API: `App.copy_to_clipboard(text)`
  writes the raw OSC 52 escape (`\x1b]52;c;<b64>\a`) straight through the
  driver. `App.console` is a rich Console bound to `_NullFile()` — never use
  it as an output sink. (Correction: the tui_textual finding "textual has no
  clipboard API" predates this method.)
- textual 8.2 `Tree.auto_expand` runs in an async `@on(NodeSelected)`
  handler that **toggles** — under a key burst (autorepeat / scripted paste)
  it re-orders against the app's own visible-walk, and pre-expanding the node
  makes the handler collapse it. Set `tree.auto_expand = False` (it is a var,
  not a ctor kwarg) and expand deterministically in `_select`.
- yosys 0.69 JSON: `write_json` needs `proc`; `opt` is destructive; generate
  instances appear as `g[i].u_alu_g`-style cell names; body-less modules
  serialize `ports` + `blackbox=1`.
- sv-lang hard-errors on multiple continuous assignments to one net — keep
  fixture RTL SV-legal (else verilator warns and yosys is silent; slang is
  the strict one — trust it).

## 5. Git & commits

- Direct commits to `main` (linear solo history). No feature branches unless
  asked.
- Messages focus purely on what the change does and non-obvious reasons;
  no backstories.
- If attributing, name the model in use at the moment (e.g.
  `Co-Authored-By: opencode`), no emails or URLs.
- Push to `main` when the acceptance criteria for the Deck card pass.