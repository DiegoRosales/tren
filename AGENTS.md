# tren - Agent Guidelines & Project Instructions

Guidelines and conventions for AI agents and humans working in this
repository. Short on ceremony, long on preventing re-discovery.

## 0. What this repo is (and is not)

This is the **implementation** repo for TREN (TUI RTL Explorer & Navigator).
It currently holds the spike on textual. The evaluation harness that produced
the verified findings - dummy RTL, per-tool experiments, reports - lives in
the **sibling `tren_testing` directory** and belongs there, not here. Do not
copy harness artifacts into this repo; cite them instead.

## 1. Repo layout

```
src/tren/            the package: CLI parsing, IR model, elaboration, TUI
tests/               unit tests + the executable fixtures they drive
  rtl/               demo.sv (the drives) + ranges.sv (declared ranges) and
                     their tracked manifests
  fixtures/          the vendored demo-tree.json fixture IR (python -m
                     tren.tui, the no-backends drive)
  hls_examples/      Vitis HLS example blocks + committed rtl_output/ exports
docs/                verification.md, release.md, quick_start.md,
                     command_reference.md, advanced_usage.md,
                     configuration.md, examples/, notes/ mirrors;
                     tech-concepts.md, sw-bom.md
tools/               round-trip + capture scripts, profilers, BOM + guide generators
out/                 gitignored drive screenshots (shot*.svg)
```

The RTL fixtures live under `tests/rtl/` and the fixture IR under
`tests/fixtures/`. Manifest paths are relative to the repo root, so they
spell `tests/rtl/demo.sv`. Tool output and test expectations carry that same
prefix.

## 2. Sources of truth

- **Design**: Nextcloud note *"TREN - RTL explorer TUI (concept)"*,
  note id `1503454`, category `TREN`. Read it before proposing architecture.
  **A mirror lives in this repo** at `docs/notes/TREN-concept.md` (plus the
  backend- and TUI-exploration reports mirrored alongside); if Nextcloud is
  unreachable, read the mirrors - but treat Nextcloud as canonical.
- **Verified findings / verdicts**: sibling `tren_testing/report.md`
  (backends), `tren_testing/report-tui.md` (framework verdict + how TUIs
  work), and `tren_testing/{t1_yosys,t3_slang,tui_textual}/findings.md`
  (reproductions). If a claim contradicts them, re-run the experiment first.
- **Task breakdown**: Nextcloud **Deck board "TREN"** (stacks = Prio / v1 /
  In progress / Review / Done / Later; cards carry context + acceptance
  criteria). Move a card to *In progress* before you start it. Move it to
  *Review* when the work is ready for review. Move it to *Done* only after
  the user approves the review.

## 3. Decisions already made - do not re-derive

- **Backends** (verified against installed tools, yosys 0.70 / sv-lang 12.0):
  yosys `write_json` for connectivity; `slang-hier --params` and/or
  `slang --ast-json` for scope tree + resolved params. `slang --ast-json`
  flags that matter: `--ast-json-source-info`, `--ast-json-detailed-types`.
- **Yosys rules** (non-negotiable):
  - run `proc` - `write_json` rejects unprocessed designs;
  - never run `opt` - it prunes unobservable cells/nets out of the JSON;
  - never `flatten` - unflattened cells are the hierarchy;
  - bit IDs are **module-local and positional-only** across a boundary:
    match the k-th listed bit of a port, never the numbers;
  - module names are mangled `$paramod\name\param=value` - un-mangle and join
    with `slang-hier` names.
- **UI framework**: textual (pinned 8.2.8) for the spike/MVP; ratatui 0.30 is
  the earmarked rewrite target and must remain a drop-in swap (IR and
  navigation logic framework-agnostic). Screen = tree | source | I/O; keys
  j/k/l/h (the arrow keys are aliases), 1/2/3 pane select, z zooms the active
  pane, enter shows the module (centre), ctrl+u up a level, y yank (OSC 52),
  e editor jump. The "User guide" section of README.md is the keymap
  reference.
- **Black boxes are the normal case**: TREN always opens something. Port
  lists + `blackbox=1` come from yosys JSON already; user cuts toggle a scope
  to opaque.

## 4. Toolchain

Always run under the dev shell: `nix develop . -c <command>` - never bare
`python`/`yosys`/`slang` from the host. The flake pins Python 3.14 + textual
8.2.8 and provides yosys, sv-lang, rust, jq, rg. Version pins are recorded in
`README.md`; bump them deliberately and re-verify the tests.

Enter the shell once (`nix develop`) and run the `make` targets directly: they
run in the current shell and call `tren` from `PATH`. They spawn a shell only
when `NIX_DEV=1`, for a host shell with no dev shell entered.

The **default** shell is the one every gate assumes: the tren package is not
installed there, it is read from the checkout's `src/` through `PYTHONPATH`.
Run all tests and drives there. `.#tren` (nix-installed package) and `.#tren-venv`
(editable `pip install -e .`) exist to exercise tren *as a package*. See the
"Dev shells" section of README.md. Dependencies live in `pyproject.toml`; a
new third-party import must be declared there, or `.#tren` is where it will
break.

For anything touching Xilinx tooling (Vivado / Vitis HLS) use the `#xilinx`
FHS sandbox - the tools need a conventional `/usr/lib` that NixOS lacks:
`nix develop .#xilinx` (x86_64-linux only) and work inside it. Note the env's
runScript shell is interactive-only: `nix develop .#xilinx -c <cmd>` does NOT
execute the command (verified on nix 2.x for buildFHSEnv .env outputs) - run
the Makefile inside the shell instead. The HLS example build flow mirrors
ipyfuse: `make setup_xilinx` writes `.xilinx_env` from `$XILINX_VITIS`,
`make <block>_HLS` re-synthesises one block under `tests/hls_examples/hls/`.
First `nix develop .#xilinx` has a long one-time build (systemd/gcc from
source for the pinned nixpkgs rev).

## 5. Working conventions

- **The user guide lives in the "User guide" section of `README.md`. Keep it
  in sync in the same change as any binding / CLI / behaviour edit**: the
  keymap table, yank formats, trace/black-box semantics and the make targets
  are all documented there and verified by the Pilot drives. After editing
  it, run `make check` (unit + both drives) and, if a key/OSC path changed,
  `make roundtrip`.
- **The verification strategy lives in `docs/verification.md`**: the layered
  gates (unit -> elaboration acceptance -> Pilot drives -> real-terminal round
  trip -> scale/cache profile -> real-artefact importers), what each layer
  cannot catch (e.g. Pilot asserts state, not rendered output), and the
  change checklist. Consult it before deciding a change is "done".
- **Verify against the installed tool; never assert flags/formats from
  memory.** This project has burned real time on exactly that.
- **Distinguish verified from assumed**: mark a guess with `WARNING:` (the
  design note's warning-symbol convention, written in ASCII).
- **Report negative results.** "X is not packaged / API Y is gone" is a
  finding that changes architecture (see `tui_textual/findings.md`).
- **Do not cite Deck card numbers in code, comments, docstrings, test names
  or README prose.** The card holds the context; write what the code does.
  Deck references belong in the card, the commit message or the chat report.
- Python: 3.14, `from __future__ import annotations`, dataclasses for the IR,
  type hints, no comments unless the why is non-obvious. Keep three layers
  separate: CLI parsing -> IR model -> UI.
- Tests: pure logic as unit tests; UI flows via textual **Pilot**
  (`test_drive.py`). Headless and CI-able by design.
- Elaboration output is an on-disk cache keyed by
  `sha256(manifest + source mtimes)`; start instantly on cache hits.
- **Never `export PYTHONPATH=src`** at the shell level for runtimes that
  import textual - the dev shell exposes rich/textual through its own
  `PYTHONPATH`, and replacing it breaks `import rich`/`import textual`. The
  test files self-insert `src` via `sys.path.insert`; use
  `PYTHONPATH=src:$PYTHONPATH` only when you must (e.g. `python -c` smoke
  checks).

### Writing style: Simplified Technical English (ASD-STE100)

Write prose in **ASD-STE100 Simplified Technical English** in code comments,
docstrings, commit messages, `docs/`, Deck card text, and the chat report of
what you did. `README.md` is the exception; see *Voice* below. Identifiers
follow the same spirit (one word per meaning), but keep established names.

The rules that matter here:

- One instruction per sentence. Keep procedural sentences to 20 words or
  less, descriptive sentences to 25 words or less.
- Active voice, present tense. Use the imperative for instructions
  ("Run `make check`", not "the tests should be run").
- One word, one meaning. Do not use "test" as both verb and noun in the same
  paragraph if a clearer word exists. Do not switch synonyms mid-document
  (pick "scope" or "node", then keep it).
- Keep articles and helper words: "the port", not "port". Do not compress
  into noun clusters of more than three words.
- No slang, metaphor, irony, or backstory. Say what the change does.
- Paragraphs of 6 sentences or less.

### Voice: write the README for a person

`README.md` is the project's front page. Write it for a human who is deciding
whether to use TREN, and let it carry a voice: personality, a comparison to a
familiar tool, an opinion about the problem it solves. The STE rules above
apply to every other file, not to the README.

The README must not read like a specification for a language model, and it
must not restate the repository conventions. That is this file's job. Keep the
README short and point to `docs/` for detail.

Creative language does not relax the following:

- Correct grammar and spelling. A sentence still needs its verb and its full
  stop.
- Accurate commands, key names, paths, versions and configuration values.
  Read them from the program, not from memory.
- Plain ASCII, as described below.
- One meaning per technical term, in the README and in `docs/` alike.

### Character set: ASCII only

Prose and code must use plain ASCII. This applies to files in this repo and
to commit messages.

- Never use: em dash (U+2014), en dash (U+2013), curly quotes, the ellipsis
  character, the non-breaking space, arrows, emoji, or box-drawing and symbol
  characters used as decoration.
- Use instead: `-` or `--`, `'` and `"`, `...`, a normal space, `->`.
- Mark an unverified claim with `WARNING:` (the design note writes this as a
  warning symbol; keep the meaning, not the character).
- Exceptions, both narrow:
  - **Glyphs the TUI paints.** `src/tren/tui.py` uses marker characters on
    purpose (the override pencil, the cut scissors, the black-box sign, the
    trace arrow). Keep them, keep the tests that assert them, and keep the
    README samples that show what the screen looks like. Do not add a new
    glyph unless it carries meaning that ASCII cannot.
  - **Data that must stay byte-exact**: captured tool output, fixture RTL,
    a terminal escape sequence in a test.
- Check a change with `rg -n '[^\x00-\x7F]'` on the files you touched before
  you commit.

### Known gotchas (verified, keep for later)

- textual 8.2: `Tree.cursor_node` is read-only -> track the cursor in the app;
  `visible_nodes` was removed -> walk `node.children` (respect `is_expanded`);
  widget queries can race the first tick -> cache refs in `on_mount()`; app
  binding `priority=True` wins over a focused widget; `export_screenshot()`
  returns **SVG**; rich style modifiers go first (`"bold #a6c9ff"` not
  `"#a6c9ff,bold"`).
- textual 8.2 `Tree` caches rendered line **strips** in `_line_cache`, keyed
  on `(line, hover, width, _updates, pseudo-classes, node updates)` - the
  widget background is *not* in the key. Restyling a Tree
  (`styles.background = ...`) leaves every line carrying a node painted at the
  old colour; only the blank region below repaints. Call
  `_clear_line_cache()` + `refresh()` (`_invalidate()` also resets nodes and
  re-layouts - heavier than a repaint needs).
- textual `can_focus = False` does **not** blur a widget that is already
  focused. On a real terminal textual has auto-focused the Tree by the time
  `on_mount` runs, so the tree stays focused and picks up
  `Tree:focus { background-tint: $foreground 5% }` (~4% lighter than every
  other pane). Headless Pilot reports `focused=None`, so the drives cannot
  see any of this - call `self.set_focus(None)` explicitly and gate on
  `make pane-cue`.
- textual 8.2 **does** ship a clipboard API: `App.copy_to_clipboard(text)`
  writes the raw OSC 52 escape (`\x1b]52;c;<b64>\a`) straight through the
  driver. `App.console` is a rich Console bound to `_NullFile()` - never use
  it as an output sink. (Correction: the tui_textual finding "textual has no
  clipboard API" predates this method.)
- textual 8.2 theme `text-primary`, `text-secondary` and the other `text-*`
  roles are generated for the theme's own surface. TREN paints its panes on
  fixed dark surfaces even under a light theme, so `tren/colors.py` lightens
  a theme role until it clears 4.5:1 on `PANE_BG_ACTIVE` and `PANE_BG_IDLE`.
  Never use a `text-*` role directly in a Rich style here.
- textual 8.2 normalizes `?` to the key name `question_mark` (the `Binding`
  still reads `"?"`). A modal screen cannot beat an App `priority=True`
  binding: `App.on_event` runs `_check_bindings` app-first, so the `?`
  cheatsheet pops itself in `TrenApp.on_event` before dispatch - that is how
  one nav key both dismisses the sheet and keeps acting.
- The cheatsheet (`?`) and the README "Keys" table render from the same list
  in `tren/keys.py`; change a binding and run `make guide` (asserted by
  `tests/test_keys.py`).
- textual 8.2 strips every *printable* key from the binding chain while an
  `Input` holds focus (`Input.check_consume_key`), so typing in a modal can
  never fire the app's priority j/k/d/L/... bindings; and the screen binding
  chain truncates at the last `ModalScreen` (`_modal_binding_chain`), so a
  non-priority binding there (the / search modal's up/down) beats the app's
  non-priority arrow aliases. That pairing is what lets the / search modal
  treat j/k/d/L as text while up/down move the results. It also
  means the user cannot type a query containing j or k *as movement*: the
  arrows are the j/k aliases, documented as such. The moment the Input is
  *blurred* the printable-key filter is gone: the app's `priority=True`
  bindings fire for any key the modal does not bind (q would quit, y would
  yank), so a committed modal must stop unwanted keys in `TrenApp.on_event`.
- textual 8.2 key **names** differ from the `Binding`/`event.key` spelling:
  `N` is event name `upper_n`, `?` is `question_mark`, and `ctrl+enter` is
  `ctrl_enter` while the binding and `event.key` keep their original form.
  Gate an app-level key filter on `event.key`, not `event.name`.
- textual 8.2 `Tree` defines `action_page_up`/`action_page_down` but binds
  neither (only up/down/enter/space/shift+... are in Tree.BINDINGS), and
  ListViews bind nothing beyond up/down/enter. App-level paging must
  therefore be its own priority binding dispatching on `self.active`,
  and `on_event` must stop the page keys while the search modal owns the
  screen - otherwise they page the tree behind it, whose list has no page
  handling to claim them. The trace split is a pane, so it pages its own
  rows.
- textual 8.2 `Input.select_on_focus` defaults to **True**: refocusing an
  Input selects the whole value, so the next keystroke REPLACES the text.
  A committed search that returns focus to the query box keeps the caret by
  `select_on_focus=False`, so a letter appends instead of wiping the query.
- textual 8.2 `Tree.auto_expand` runs in an async `@on(NodeSelected)`
  handler that **toggles** - under a key burst (autorepeat / scripted paste)
  it re-orders against the app's own visible-walk, and pre-expanding the node
  makes the handler collapse it. Set `tree.auto_expand = False` (it is a var,
  not a ctor kwarg) and expand deterministically in `_select`.
- textual 8.2 `ListView.extend` costs about 0.5 ms per `ListItem` widget,
  measured headless (10k rows ~ 4.6 s, 20k ~ 10 s). A finder that rebuilds
  its list on every keystroke must render a bounded window (the search modal
  caps at `MAX_ROWS = 200`) and show the real count in a label; otherwise a
  broad query on a large design blocks the UI for seconds per key.
- yosys 0.70 JSON: `write_json` needs `proc`; `opt` is destructive; generate
  instances appear as `g[i].u_alu_g`-style cell names; body-less modules
  serialize `ports` + `blackbox=1`.
- sv-lang 12 `--ast-json` keeps a **declared range** as the source writes it
  (`[7:1]`, `[0:7]`, `[15:8]`) on the type node of a `Port`, `Variable` or
  `Net` member, and resolves a parametrised bound per instance. yosys carries
  a bit count only, so the range is the overlay's to provide. Body nets are
  `Variable` members (a `wire` is a `Net`); a port appears a second time as a
  `Variable` with the same type. A generate block's name is empty in the AST
  while yosys spells the net `g[0].gr` - index the blocks in order to join.
- slang `--ast-json` lists one design `Instance` member per **top-level
  module**, not one per design. A source set whose modules no single top
  reaches has several. Walk every one: reading only the first leaves the other
  modules without port `src` spans, so the TUI has no declaration to
  highlight. A change in what an existing IR field means needs a
  `SCHEMA_VERSION` bump (`ir.py`), or old cache blobs keep serving it.
- slang **normalises an integer literal**: the source text `3'b000` comes
  back as `3'b0` and `2'd1` as `2'b1`. Never compare a resolved value with a
  source-text default as strings (that is what put a false override marker on
  every HLS state parameter); compare the numbers (`ir.literal_int`).
- sv-lang hard-errors on multiple continuous assignments to one net - keep
  fixture RTL SV-legal (else verilator warns and yosys is silent; slang is
  the strict one - trust it).

## 6. Git & commits

- **Do not commit until a review is done.** By default, leave the work as
  changes in the working tree. Run the gates, then move the Deck card to
  *Review* and ask the user to review. Do not create a commit at that point.
- When the user approves the review, create the commit and move the card to
  *Done*. Do not push unless the user explicitly requests a push. Do not
  commit a change that the user has not reviewed, unless the user asks for it
  directly.
- Direct commits to `main` (linear solo history). No feature branches unless
  asked.
- Messages focus purely on what the change does and non-obvious reasons;
  no backstories.
- If attributing, name the model in use at the moment (e.g.
  `Co-Authored-By: DeepSeek V4.1 Flash 1017`), no emails or URLs.
