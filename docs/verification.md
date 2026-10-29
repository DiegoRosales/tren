# TREN - Verification strategy

How TREN is verified at every level, from pure logic to a real terminal, and
what each layer does not catch.

The two rules that shape everything here (AGENTS.md section 4):

1. **Verify against the installed tool; never assert a flag, output format
   or package name from memory.** yosys/slang are the source of truth for
   what the IR contains; testing only shadows them.
2. **Headless-first, real-terminal-when-it-matters.** Everything the tool
   *computes* is asserted in-process (fast, deterministic, CI-able). Anything
   that depends on how the terminal *renders* or on OSC/ty semantics is
   verified against a real terminal.

## 1. The verification layers

| Layer | What it checks | Where | CI? |
|---|---|---|---|
| 1. Unit tests | pure logic: parsers, IR model, yank formats, importer grammar, bit-key handling | `tests/*.py`, `make test` | yes |
| 2. Elaboration acceptance | yosys + slang-hier + slang against the real pinned tools | `tests/test_elaborate.py` | yes (skips w/o tools) |
| 3. Headless UI drives | the real TUI driven by textual Pilot | `test_drive.py`, `make drive` / `drive-real` | yes |
| 4. Real-terminal round trip | OSC 52 bytes + `$EDITOR` invocation, and the rendered active-pane cue, on a real terminal | `tools/osc52_roundtrip.sh`, `make roundtrip`, `tools/pane_cue_capture.sh`, `make pane-cue` | no (needs tmux/pty) |
| 5. Scale / cache profile | lazy-load materialisation + elaboration cache | `tools/profile_1k_scopes.py`, `make profile` | no |
| 6. Importers on real artefacts | manifests read real Vivado/HLS RTL workspaces | `make import` / `make hls-*` (manual) | no (reference workspaces live outside CI) |

`make check` = layers 1 + 3 (the CI workflow's exact steps). Layer 4/5 run
locally; layer 4 needs a terminal so it is not in CI.

The `make` targets run in the shell you are in, so enter a dev shell first
(`nix develop`). From a host shell, `NIX_DEV=1 make ...` wraps each command in
`nix develop $(TREN_SHELL) -c ...`. CI calls the same commands under the
default shell, so neither path changes what a gate checks.

### Layer 1 - unit tests (`make test`)

Pure Python plus `make -n` dry runs of the Makefile. 205 tests.

- **IR** (`tests/test_ir.py`): constructors, lossless JSON round-trip
  (`Design.read(p).to_json() == p`), fixture adapter.
- **Bit map / unmangling** (`tests/test_bits.py`): `$paramod` unmangling,
  positional port matching helper.
- **Elaborator internals** (`tests/test_elaborate.py`): slang-hier line
  parsing, ast-json src/type/default extraction, the yosys constant/x-bit
  key sentinel (a real quirk surfaced by the HLS test set).
- **Yank formats** (`tests/test_formats.py`): every preset + a custom format
  diverging in every dimension.
- **Importers** (`tests/test_importer.py` / `test_vivado.py`): filelist
  grammar, dir scans, Vivado `.xpr` FileSets/TopModule/`$PSRCDIR` vars, `.vh`
  include-file exclusion, format autodetection.
- **Manifest** (`tests/test_ir.py` + `cache`): canonical bytes stable across
  dict ordering; cache key changes with source mtime/size.

All are skipped-free by design (pure), so they are the project's fastest
regression net - ~0.3s for the whole suite.

### Layer 2 - elaboration acceptance (`tests/test_elaborate.py`)

Skipped unless `yosys && slang-hier && slang` on PATH (the dev shell provides
them). Builds the real IR from the *tracked demo RTL* and asserts:

- the exact scope tree (8 scopes: 6 modules, 2 generate, 1 black box;
  path-keyed `top.u_core.g[0]`, `module=None` for generate);
- resolved + default params at full fidelity (`W=32'd32`/default `8`/
  override; `OP` localparam), port types from slang (`logic [31:0]`);
- declared ranges on ports and nets, the generate block's `g[0].gr`
  included;
- deterministic: `elaborate` twice -> equal `Design`s;
- cache: warm hit round-trips the same `Design`.

A second tracked fixture, `rtl/ranges.sv`, holds the ranges the demo cannot
carry - `[7:1]`, `[0:7]`, `[15:8]` - because the drives assert the demo's
line numbers. Its acceptance test asserts the IR *and* the leaf text the
hierarchy paints, so a range that stops reaching the label fails here.

This layer is the thing that pins **tool-version behaviour**: if yosys/slang
change something, this test fails loudly instead of silently disturbing every
finding downstream.

### Layer 3 - headless UI drives (`test_drive.py`)

Drives the actual `TrenApp` in-process with textual's `Pilot`, both in a
fixture (no tools) and real-IR mode. Two flavours:

- `make drive` - `test_drive.py` (fixture): tree nav, pane routing, yank,
  l/h expand/collapse, ctrl+u, enter, `z` pane zoom, pageup/pagedown paging
  per pane; fast, no tools.
- `make drive-real` - `test_drive.py --manifest rtl/demo.manifest.json`:
  the *real* elaboration, source-pane focus/auto-follow, trace crossing with
  positional matching down to `$xor`, black-box stops, `X` cuts, yank format
  rendering + OSC payload, editor target.

Both assert **observable app state** (labels, focus, traces, yanked strings)
rather than screenshots, so they are deterministic and CI-safe. Their blind
spot: they don't prove *rendering* - see layer 4.

### Layer 4 - real terminal (`make roundtrip`, `make pane-cue`)

Two tools, both on a real ptty inside a **private** tmux server (never
touching the user's tmux). Each run uses its own socket path under the run's
temporary directory, so a stale or concurrent run cannot share the server.
Both start the session with `tmux -f /dev/null` and an explicit
`bash --noprofile --norc`. The gate therefore does not depend on
`~/.tmux.conf` or the user's interactive shell configuration, so a local zsh
setup cannot turn the gate red. `HOME` is still inherited, but no current tren
code reads it.

#### `make roundtrip` (`-S <tmp>/tmux.sock`)

Pipe-panes the raw output bytes and asserts:

- the OSC 52 clipboard sequence actually leaves the process and encodes the
  expected yank path (`top.u_core`);
- `e` invokes a recorder `$EDITOR` with `+77 rtl/demo.sv`, and `E` (cards
  241) with `+45 rtl/demo.sv` - the module definition.

Why pipe-pane instead of `set-clipboard`/`show-buffer`: a client-less tmux
server relays OSC 52 to nothing (no attached `Ms`-capable terminal), so the
buffer stays empty; `pipe-pane` captures what a terminal really receives.

#### `make pane-cue` (`-S <tmp>/tmux.sock`)

`capture-pane -e` after each of `1`/`2`/`3`, decoding the SGR stream to the
background colour in effect at a column inside each pane. Asserts exactly one
pane at `PANE_BG_ACTIVE` and the other two idle, **on rows carrying tree
nodes** - the two bugs it pins both hid on precisely those rows while
`widget.styles.background` read correct:

- textual 8.2 `Tree` caches rendered line *strips* keyed on
  (line, cursor, hover, width, updates, pseudo-classes); the background is not
  in the key, so a cue change repainted only the blank region below the nodes;
- `can_focus = False` does not blur an already-focused widget, and a real
  terminal has auto-focused the Tree before `on_mount` runs - a focused Tree
  picks up `Tree:focus { background-tint: $foreground 5% }` and reads as
  highlighted even when idle.

It also asserts the top status line renders the selection's full
path on row 0 of a real terminal - the drives assert the *value*, this asserts
the terminal actually paints it.

This layer is what caught the render-only bugs (tree root never expanded;
tree auto-focus swallowing Enter; the stale/tinted pane cue above) that layers
1-3 could not.

### Layer 5 - scale / cache profile (`make profile`)

A synthetic 1100-scope IR answers the two scale questions:

- **Lazy materialisation** - how many tree nodes exist at mount. Lazy: 100
  (9%); eager: 1100 (100%). The "does not materialize the graph" acceptance.
- **Cache** - cold elaboration invokes 3 tool subprocesses; warm invokes 0.

These numbers are recorded in the script's docstring rather than asserted (a
profile, not a test), so machine differences don't turn into false failures.

### Layer 6 - importers on real artefacts

Importers are the interface to the *outside* (Vivado, HLS, vendor
filelists), so their verification runs against real exported RTL rather than
hand-made fixtures:

- `make import` regenerates `rtl/manifest.json` from the demo source tree;
- `make hls-import`/`hls-elaborate BLOCK=...` read the committed
  `test/hls_examples/hls/<block>/rtl_output` sets - `test_adder_2p`,
  `test_axis_interfaces`, and `fft_wrapper` (140 scopes / ~50k bit-links /
  1763 boundaries) elaborate end-to-end with no Xilinx install;
- the Vivado import was validated against the reference Vitis HLS 2025.2
  workspace (`project.xpr` -> 50-source manifest, `.vh` include-files
  excluded, `ipshared` duplicates collapsed).

These reference workspaces live outside CI, so layer 6 is a manual gate
documented in the Makefile; the HLS workload is also what surfaced the yosys
constant/x-bit quirk now pinned by unit tests.

## 2. What each layer cannot catch

| Layer | Cannot catch | Guarded by |
|---|---|---|
| 1 | tool-version drift; global refactors; real rendering | layers 2-4; search |
| 2 | any TUI concern; **state written by an older tren** - every cache test builds its cache in a fresh `TemporaryDirectory`, so a blob from *previous code* was unreachable by construction | `ir.schema_id()` makes the IR shape part of the key; `SchemaInvalidationTests` writes blobs from "another schema" on purpose |
| 3 | *rendered* terminal output; OSC semantics; autofocus/Enter routing to the real widget tree | SVG export is post-hoc; layer 4 exists for this |
| 4 | scale; determinism across re-runs | layers 2, 5 |
| 5 | correctness (numbers only) | layers 1-4 |

Worth saying twice: **layer 3 passes while layer 4 fails** in this project
(twice, so far). Pilot asserts on `_visible()` which reads tree state
directly, not on what the terminal draws. Any change to tree state /
expansion visibility / key routing should run `make roundtrip` - the drive
tests are necessary but *not sufficient*.

## What the strategy has actually caught

A sample of real failures the layers fired on, as evidence they are not
ceremony:

- **layer 4**: the tree root was never expanded - a real terminal rendered a
  bare `design` line while every drive passed; the same layer caught the
  Tree auto-focus swallowing Enter, and the active-pane cue leaving the tree
  column lit (stale `Tree` line cache + a `Tree:focus` background-tint that
  headless Pilot cannot even produce, since it never focuses the Tree).
- **layer 2**: slang 11 erroring on a single `.f`-style construct it
  rejects, and (as a negative result) not exposing parameter defaults, which
  drove the source-text `_decl_default` extraction.
- **no layer at all**: the slang overlay reached the IR and was asserted at
  layers 2 and 3, while the repo's own `.cache/tren` still answered every port
  with `type=None` - a blob written before `Port.type` existed, kept alive by
  a key that knew the inputs but not the IR. Found by reading the cache on
  disk, not by a gate. Every layer tested the *code*; nothing tested what was
  already on the machine.
- **layer 1**: the yosys constant/`x`-bit token that only a real 140-scope
  HLS workload could produce.
- **layer 6**: HLS-generated `.v` include-files (`.vh`) and `ipshared`
  duplicates are import hazards - now excluded/collapsed by design.

## 3. Making changes - the checklist

| Change | To do | Gate |
|---|---|---|
| pure logic / IR | unit test | `make test` |
| a new IR field | nothing to remember - `ir.schema_id()` invalidates caches from its layout. Changing what an *existing* field means is the exception: bump `ir.SCHEMA_VERSION` | `make test` |
| binding / navigation / pane | update Pilot drives, user guide, AGENTS gotchas; a key map change also runs `make guide` so the README table and the `?` cheatsheet (one list in `tren/keys.py`) cannot drift | `make check` + `make roundtrip` (tree/OSC) |
| pane styling / anything the Tree renders | assert the style in the drives **and** capture a real terminal | `make check` + `make pane-cue` |
| elaborator / CLI / importers | extend acceptance, re-run on demo + an HLS block | `make check` + `make hls-elaborate BLOCK=...` |
| a new `slang_flags` / `yosys_flags` in the manifest | update argv-construction tests, re-run on the zybo reference design | `make check` + elaborate the zybo manifest under `.#tren-yosys-src` |
| backend flag / format claim | **verify against the tool first** (AGENTS section 4) | run tool, then encode as test |
| flake / tool versions | re-verify everything | full `make check` + update cache/version notes |

Every such change must also touch `README.md` (section User guide) in the same
commit - that is the user-facing contract for keys/CLI.