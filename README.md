# TREN - TUI RTL Explorer & Navigator

[![CI](https://github.com/DiegoRosales/tren/actions/workflows/ci.yml/badge.svg)](https://github.com/DiegoRosales/tren/actions/workflows/ci.yml)

A terminal RTL browser that treats a design hierarchy like a filesystem.
Three panes: **scope tree** | **focused RTL source** | **module I/O**.
`1`/`2`/`3` select the pane and `z` zooms it to the full width; `j`/`k` move,
`l`/`h` expand/collapse the
highlighted scope, `enter` shows that module in the centre RTL pane, `ctrl+u`
jumps up a hierarchy level, `y` yanks the selection through the current path
format as a raw OSC 52 clipboard write, `Y` cycles the format (SV
hierarchical / Vivado `get_pins` / yosys `select` / VCD dot-path), `e` jumps
to the selection's declaration in `$EDITOR` (`+line file` style, suspending
the app), `E` to the module definition.

Status: **spike phase, built on textual 8.2**. The backend pair is verified
and pinned (see *Sources of truth*): yosys `write_json` for bit-level
connectivity, `slang-hier` / `slang --ast-json` for the scope tree and
resolved parameters. Rust + ratatui is the earmarked rewrite target,
revisited when a real 100k-instance IR shows the ceiling matters.

## Quick start

```sh
nix develop .                                # python + textual + yosys + slang
nix develop . -c tren import rtl             # -> rtl/manifest.json (first importer: .f filelist / dir scan)
nix develop . -c tren import rtl/some.xpr    # Vivado project.xpr (auto-detected)
nix develop . -c tren elaborate rtl/manifest.json      # elaborate a manifest (yosys + slang-hier + slang -> IR)
nix develop . -c bash -c "PYTHONPATH=src python -m tren.spike"                    # spike: drivers/loads of one net
nix develop . -c bash -c "PYTHONPATH=src python -m unittest discover -s tests"     # unit tests
nix develop . -c python test_drive.py        # headless Pilot tests (CI target)
nix develop . -c bash tools/osc52_roundtrip.sh    # real-terminal OSC 52 + $EDITOR round trip (tmux, private server)
nix develop . -c bash tools/pane_cue_capture.sh   # real-terminal check: active-pane cue + top status line (tmux, private server)
nix develop . -c python tools/profile_1k_scopes.py # lazy-load + cache profile (1 100-scope synthetic IR, card 215)
nix develop . -c make bom                   # regenerate docs/sw-bom.md from the dev shell (card 263)
nix develop .#xilinx                        # FHS sandbox for Xilinx tools (interactive; then: make fft_wrapper_HLS)
nix develop . -c make run                   # start the TUI (keys: see 'User guide' below)
```

### Dev shells

Five shells, all carrying the same backends (yosys, slang, verilator, rust,
tmux) so they cannot drift apart on tool versions. They differ in how
`tren` itself reaches the interpreter, and in which yosys build they carry:

| Shell | tren comes from | yosys build | Use it for |
|---|---|---|---|
| `nix develop` | the checkout's `src/`, via `PYTHONPATH` | nixpkgs (0.69) | day-to-day work. Edits are live on the next command; every gate in `docs/verification.md` assumes this shell |
| `nix develop .#tren` | the nix store (`buildPythonPackage`) | nixpkgs (0.69) | checking the *package*: `import tren` and the `tren` / `tren-tui` console scripts resolve into the store, from any directory |
| `nix develop .#tren-venv` | `.venv/`, `pip install -e .` | nixpkgs (0.69) | the textbook Python flow on nix's interpreter - an editable install, so edits are live too |
| `nix develop .#tren-yosys-src` | the checkout's `src/` | yosys git (`github:YosysHQ/yosys`, flake pin) | verifying against yosys main. The first entry builds the whole tool from source |
| `nix develop .#xilinx` | the checkout's `src/` | nixpkgs (0.69) | the FHS sandbox for Vivado / Vitis HLS (x86_64-linux only) |

Every `make` target runs under the shell named by `TREN_SHELL` (default: the
default shell). Point the gates at the source build with
`make check TREN_SHELL=.#tren-yosys-src`.

`.#tren` is the one that catches what the others hide: nothing on its path
reads the checkout, so a module that only ever imported because `src/` was on
`PYTHONPATH`, or a dependency the code uses but `pyproject.toml` does not
declare, fails there and only there. It is also what `nix build .#tren`
produces.

`.#tren-venv` creates `.venv/` in the working directory on first entry and
pip-installs the project editable from `.` - so enter it from the project
root. First entry needs network (pip fetches `textual` and `rich` from PyPI);
later entries just activate. `TREN_VENV_DIR` overrides the location; reset
with `rm -rf .venv/` and re-enter.

`.#tren-yosys-src` is `nix develop` with a yosys built from upstream source
instead of the nixpkgs release. The `YosysHQ/yosys` flake input pins the
revision and fetches the tool's git submodules (needs Nix >= 2.27); bump
with `nix flake lock --update-input yosys`. The first entry compiles the
whole tool, so allow some time. Verify a bumped source against the tests
(`docs/verification.md`, layer 2) before relying on it. The pinned source
carries the macro-argument token-pasting fix (card 262); nixpkgs 0.69 does
not, so an RTL design that hits the multi-line macro-argument bug elaborates
here and not in the default shell. The `slang_flags` / `yosys_flags`
manifest fields work identically in both shells.

Dependencies are declared once, in `pyproject.toml`, and read by both the nix
build (through `pyproject.nix`) and pip.

The spike (`python -m tren.spike rtl/demo.sv`) runs yosys, builds the bit map
and prints drivers/loads of `top.out`, descending across the core boundary
down to the `$xor` that computes it - every row carries its `src` attribute.
Use `--keep` to keep the generated netlist under `.cache/tren/` for
inspection. Note: `assign o = {hi, lo}` produces **no cell** in yosys 0.69 -
the concatenation collapses to pure net aliasing, so `o` shows no driver
(its bit list literally overlays `hi` and `lo`); the loads are all there.

## User guide

### Starting the TUI

```sh
nix develop . -c python -m tren.tui --manifest rtl/demo.manifest.json   # real elaboration
nix develop . -c python -m tren.tui                                     # vendored demo fixture
# or: nix develop . -c make run
```

The screen is three panes - **tree** (the scope hierarchy, like a filesystem)
| **source** (the focused module's RTL) | **I/O** (parameters + ports). `q`
quits.

Across the top, a yazi-style **status line** shows the full hierarchical path
of the current selection - a scope row reads `top.u_core.g[0].u_alu_g`, a
port/net leaf appends the item (`top.u_core.g[0].u_alu_g.result`). It updates
live on `j`/`k`/`l`/`h`/`ctrl+u`/`enter`, the final segment highlighted.

The pane `1`/`2`/`3` selected - the one `j`/`k`/`y` act on - is lifted out of
the background; the other two stay dark. Exactly one pane is ever lit.

The first run elaborates (yosys + slang) and caches the IR under
`.cache/tren/`; later runs start instantly. The cache is keyed on the
manifest, the sources' mtimes **and** the shape of the IR itself - so editing
RTL *or* upgrading to a tren whose IR carries new fields re-elaborates once,
instead of serving a blob that predates them.

### The tree pane

A scope row carries the instance name; below it come the scope's ports, then
its nets:

```
u_alu0/
  port in    a[31:0]
  port in    op[3:0]
  port in    clk
  port out   result[31:0]
  net x[31:0]
  net we
```

A port leaf reads `port <direction> <name>[msb:lsb]`. The direction sits in a
fixed column, so `in`, `out` and `inout` all start the name at the same
offset. A net leaf reads `net <name>[msb:lsb]`, with the same range spelling
and no direction. A scalar (one bit) carries no range.

The range is the one the source declares, from the slang overlay: `[7:1]`
and `[0:7]` read as they are written, and a parametrised bound shows the
value this instance resolves. A signal the overlay never reached (a module
outside the slang pass) keeps the range derived from the yosys bit count,
`[width-1:0]`.

### The I/O pane

Parameters first, then the ports:

```
✎ W = 32'd32  [default 8]
· OP = 4'b11  [default 3]
▸ a  logic [31:0]  in[32]
  b  logic [31:0]  in[32]
```

A parameter shows the value **resolved for this instance** with its declared
default beside it: `✎` marks one the instantiation overrides, `·` one that
takes the default (a `localparam` can never be overridden). A port shows its
type as written in the source - `logic [31:0]`, from slang - then direction
and bit width.

The two sides of a parameter come from different layers and spell one number
differently: slang resolves the source text `3'b000` to `3'b0`. TREN
compares them as numbers, so only a real override gets the `✎`. A default
written as an expression - `parameter B = (32 / 8)` - is shown as written,
and never counts as an override: the resolved `4` beside it proves nothing
either way.

Types, defaults and declaration lines come from the slang overlay; widths and
connectivity come from yosys. A port belonging to a module the slang pass
never reached still shows the netlist's direction and width, just without a
type.

While the I/O pane is active, the source pane highlights the selected port's
declaration and follows `j`/`k` through the list. `enter` re-centres the
band. A port with no source location falls back to the module view, as a
tree leaf does.

### Zooming a pane

`z` zooms the active pane: it takes the full width and the other two panes go
away. `z` again restores the three columns at their usual widths. While the
zoom is on, `1`/`2`/`3` move it - the pane you select becomes the zoomed one,
so exactly one pane is ever on screen. Every other key keeps its effect: `j`
still walks the tree, scrolls the source or selects a port, by the active
pane. `z` does nothing while the trace modal is open, because the modal covers
the panes.

### Keys

All bindings work from any pane unless noted; `x` below means the active
pane.

<!-- keys:begin -->

| Key | Effect |
|---|---|
| `1` `2` `3` | select the tree / source / I/O pane |
| `z` | zoom the active pane to the full width, or restore the three columns |
| `j` `k` | move down/up: in the tree, through the visible nodes; in source, scroll; in I/O, select a port |
| `pageup` `pagedown` | move a screen up/down in the active pane: in the tree, jump to the next/previous page of nodes; in source, scroll a screen; in I/O, select a port a page away (card 283) |
| `l` | tree: expand the highlighted scope (reveal its children) |
| `h` | tree: collapse the highlighted scope (hide its children) |
| `arrows` | aliases: down/up = j/k, right/left = l/h |
| `enter` | show the selection in the centre RTL pane with context - tree, the highlighted module; I/O, the selected port's declaration |
| `ctrl+u` | tree: step up one level, current scope first - a port/net leaf jumps to its own module, a module row to its parent |
| `/` | open the global search: fuzzy find over modules, instances, ports, nets and files (type to filter, enter to commit, n/N next/prev match, tab switches results/matched-lines pane, ctrl+enter to jump) |
| `y` | yank the selection through the current path format (raw OSC 52 -> the terminal's clipboard) |
| `Y` | cycle the yank format: sv -> vivado -> yosys -> vcd |
| `d` `L` | trace drivers / loads of the selected net (opens the trace modal) |
| `X` | cut/restore the highlighted scope (treat it as a black box) |
| `;` | dump the current trace path as text |
| `e` | open $EDITOR at the selection's declaration - a scope's instantiation in its parent, a port/net's own declaration |
| `E` | open $EDITOR at the module definition (the module's declaration line); on a top module both e and E land there |
| `?` | open this navigation cheatsheet |
| `q` | quit |

In the **trace modal**: `enter` or `right` crosses a boundary into the child scope, `backspace` or `left` pops the trace stack, `down/up` move through the rows, `escape` closes it.

<!-- keys:end -->

### Navigating

`j`/`k` move through the tree **without unfolding** anything - only `l`
expands the highlighted scope to reveal its children (file-manager model),
and `h` collapses it again. The arrow keys do the same four things: `down`
and `up` move, `right` expands, `left` collapses. `ctrl+u` steps up one granularity level, current
scope first: from a port/net leaf it jumps to that module's row (not past it
to the parent); pressed again it moves up to the parent module. `enter` puts
the highlighted module into the centre. With the I/O pane active, `enter`
re-centres the selected port's declaration instead. The center pane shows
the **real RTL
file** at the module's *instantiation in its parent* (or its declaration at
the top), with the focused range highlighted and kept in view - `e` opens
the editor at exactly that line, `E` at the module's own definition.

Selecting a **port** or **net** leaf focuses that signal's own *declaration*
line rather than the enclosing module - `port a` under `top/u_core` lands on
`input logic [W-1:0] a,`, `net x` on `logic [W-1:0] x, y, rd, r0;`. A leaf
whose backend carried no `src` falls back to the module-level focus, and a
`src` naming a file that is not on disk (fixtures, moved sources) falls back
to the inline RTL snippet. The focus band follows the selection; scrolling
with `j`/`k` inside one scope is left alone, and only a new focus re-centres
the pane.

### Searching

`/` opens a fuzzy finder over **everything**: modules (every scope path),
instances, ports, nets and files (the source files the scopes' `src` spans
name). Type a substring of `alu_result` and the net rows across every scope
narrow to it - fuzzy, so `alr` finds it too. The results list shows the kind
first: `net top.u_core.alu_result`, `port alu.op`, `module core`,
`instance top.u_core.u_sram -> core`. Up/down (the j/k aliases) move the
cursor, and the right column previews the source at that result's
declaration. The query box holds focus, so every letter types - the letters
`j`/`k` move the list only when you let the arrows do it for you.

The walk is vim-style. `enter` commits the query and keeps the finder open:
`n`/`N` then step to the next / previous match through the whole result set,
wrap-arounds included. A bottom pane then lists one matched source line per
result (like vim's `:g//p`); `tab` switches which pane is active and
up/down always move the same highlighted match, so scrolling the lines walks
the result list. `enter` or `/` returns to the query box to refine.
`ctrl+enter` drops you back into the tree with the highlighted result
selected, `escape` dismisses without jumping. While the finder is committed
no other key reaches the tree behind it, so `q`, `y`, `X` and friends are
safe to hit.

The list renders the top 200 matches in rank order; walking past the edge
slides the window and the counter always shows how many matched in full, so
a broad query like a single letter counts tens of thousands of results while
the list stays responsive.

### Yanking paths

`y` writes the path of the current selection to the clipboard as a raw OSC 52
sequence (works in any terminal that relays it, e.g. tmux with
`set-clipboard`, most terminals). The formats are declarative (see
`tren/formats.py`), one per target tool:

| Format | Selection `top.u_core.g[0].u_alu_g.result` |
|---|---|
| `sv` | `top.u_core.g[0].u_alu_g.result` |
| `vivado` | `u_core/g\[0\]/u_alu_g/result` - top excluded, `/`, Tcl-escaped brackets |
| `yosys` | `top/u_core/g[0]/u_alu_g/result` |
| `vcd` | `top.u_core.g[0].u_alu_g.result` |

### Tracing drivers/loads

Select a net to the tree, then `d` (drivers) or `L` (loads). The modal lists
the touching cells; `enter` on a boundary row crosses **positionally** into
the child scope's net (bits are module-local - matching by ID across a
boundary is wrong by design). `;` dumps the breadcrumb, e.g.
`top.out ▶ top.u_core.alu_result`.

### Black boxes and cuts

Body-less modules (`sram_stub`-style) render as opaque scopes with their
ports and a `!` marker. `X` adds an explicit cut to any scope - the trace
stops at it with the reason shown - and `X` again restores it.

### Headless / CI

`make check` runs the unit suite plus both Pilot drives - the same gates the
CI workflow runs. `make test` / `make drive` / `make drive-real` run them
individually; `make roundtrip` verifies the OSC 52 + editor path and
`make pane-cue` the rendered active-pane highlight, both against a real
terminal on a private tmux server (Pilot asserts style *values*, not what the
terminal paints - see `docs/verification.md`). `make bom` regenerates the
auto-generated software bill of materials (`docs/sw-bom.md`) from whatever the
dev shell resolves.

### Reading Vitis HLS RTL output (no Xilinx needed)

The committed `test/hls_examples/hls/<block>/rtl_output` exports are read
directly: `make hls-import`, `make hls-elaborate` and `make hls-run` import /
elaborate / browse one block (`BLOCK` defaults to `fft_wrapper`). The
elaborator handles the generated Verilog-2005 including yosys's constant / x
bits. Example: `fft_wrapper` bundles 140 scopes, ~50k bit-links and 1763
boundaries.

## Manifest schema and backend tolerance flags

`tren import` emits a manifest (`sources`, `incdirs`, `defines`, `params`,
`top`, `blackboxes`). Two optional list fields carry backend tolerance flags
(card 259) - real third-party RTL needs them, and they travel with the
design, not the tool:

| Field | Reaches | Example zybo_sampler value |
|---|---|---|
| `slang_flags` | `slang-hier --params` and `slang --ast-json` | `["--timescale=1ns/1ps", "--allow-use-before-declare", "-Wno-redefinition"]` |
| `yosys_flags` | `read_verilog` before the sources | `["-nolatches"]` |

Always on, no manifest needed: slang gets `--ignore-unknown-modules` (an
unresolved module is a normal black box in TREN) and yosys gets `-nolatches`
(TREN wants the net graph, not a synthesis result). The zybo_sampler design
(39 scopes, 13566 bit-links) elaborates with only the two manifest edits
above - and, where the source yosys is required, under `.#tren-yosys-src`.

When a backend fails, `tren elaborate` prints that tool's own stderr
(trimmed to 40 lines) followed by the full command line, then exits 1 - no
Python traceback.

## Importers

Manifest producers - bootstrap, not a dependency (`tren import`).

- **Plain filelist** (`.f`, slang `-F` grammar) or a bare source directory.
- **Vivado** (`project.xpr`, auto-detected): parse `<FileSets>` DesignSrcs
  (+`TopModule`) and resolve Vivado's `$PSRCDIR`-style variables; complete
  the sources with a directory scan because batch/HLS projects carry the RTL
  outside the FileSets. `.vh` files are `\`include`\`-only (excluded),
  generated-dir duplicates are dropped, and non-Verilog FileSet entries
  (`.bd`) warn. Verified against a Vitis HLS 2025.2 export: yosys
  `read_verilog` parses the generated Verilog-2005 cleanly; `slang-hier`
  additionally needs Vivado's `vivado_interfaces.svh` interface library for
  the block-level `bd_0_sv.sv`, which the workspace does not ship - pass
  `--top fft_wrapper` (or add the lib dir) to elaborate the HLS core.

**incdirs default to the source directories.** Every importer records the
directory holding each source as an incdir, so a `` `include `` next to its
source (or in a sibling source dir) resolves without a manual entry. The
reason is a backend mismatch: slang and Vivado resolve a relative
`` `include `` against the including file's directory, while yosys
`read_verilog` searches only its `-I` dirs (verified - a same-directory
include resolves anyway, a cross-directory one does not). Explicit `+incdir+`
filelist entries and Vivado header-typed FileSet entries (`.vh`/`.svh`) add
their dirs too - the default is a floor, not a replacement. A manifest that
only hand-writes sources carries the same floor for free.

## Vitis HLS examples and the #xilinx sandbox

`test/hls_examples/` mirrors ipyfuse's structure: `cpp/` (the C/C++ sources),
`dat/` (golden/input data), and `hls/<block>/{config.tcl,directives.tcl,
rtl_output/}` plus a shared `hls/run_hls.tcl`. Each `rtl_output/` is a
committed snapshot of the exported `impl/verilog` (RTL + `component.xml`),
so it works without a Xilinx install:

    nix develop . -c tren import test/hls_examples/hls/fft_wrapper/rtl_output --top fft_wrapper

Regenerating an example needs a Vitis/HLS install. Xilinx binaries expect a
conventional `/usr/lib` layout that NixOS lacks, so the flake ships a
`#xilinx` FHS sandbox (ported from ipyfuse) for x86_64-linux. The env's
runScript shell is interactive-only (`nix develop -c` does not reach it), so
enter it first, then run the Makefile:

    nix develop .#xilinx                          # interactive FHS shell (first build is slow)
    make setup_xilinx     # XILINX_VITIS=/path/to/Vitis ./settings64.sh path -> .xilinx_env
    make fft_wrapper_HLS  # vitis_hls -f run_hls.tcl fft_wrapper; regenerates rtl_output/
    make hls_examples     # rebuild every block
    exit

`.xilinx_env` and the per-block `solution_1/` synthesis workspace are
gitignored; `rtl_output/` snapshots are committed.

## Layout

```
src/tren/            package
  ir.py              widget-agnostic IR (scopes, nets, params, bit map)
  elaborate.py       yosys + slang-hier/slang --ast-json -> IR (+ cache)
  importer.py        manifest producers (.f filelist; dir/single file; Vivado via vivado.py)
  vivado.py          Vivado project.xpr parser (FileSets, TopModule, $PSRCDIR vars)
  formats.py         yank format registry + templates
  tui.py             the textual app (implements the screen + keys only)
  __main__.py        subcommand front door (tren import / tren elaborate)
rtl/                 small SystemVerilog fixtures: demo.sv (the drives) and
                     ranges.sv (declared ranges: [7:1], [0:7])
tests/               PILOT_WORKDIR for textual Pilot tests + unit tests
manifest.json        hand-checkable design manifest (sources/incdirs/top/...)
docs/                verification.md + notes/ mirrors; tech-concepts.md and
                     sw-bom.md (the technical-concepts / SW BOM documents, card 263)
tools/               osc52_roundtrip.sh, pane_cue_capture.sh, profile_1k_scopes.py,
                     bom.py (SW BOM generator, card 263)
pyproject.toml       PEP 621 metadata + deps - read by both nix and pip
flake.nix            dev shells (default / #tren / #tren-venv / #tren-yosys-src / #xilinx)
```

## Decisions already made (verified - do not re-derive)

- **Backends**: yosys `write_json` after `proc` (never `opt`; bit IDs are
  positional-only across scopes); `slang-hier --params` for scope tree +
  resolved parameter values. Do not `flatten`.
- **UI**: textual 8.2 for the spike/MVP; ratatui 0.30 pinned as the rewrite
  target. The IR and navigation logic are kept framework-agnostic.
- **Screen**: tree | source (focused on the module's instantiation in its
  parent) | I/O; keys as above; OSC 52 yank.
- **Scale**: lazy scope population on expand; never materialize the whole
  graph; IR cache keyed by manifest + source mtimes **and the IR's own shape**
  (`ir.schema_id()`), so a tren whose IR has changed rebuilds instead of
  serving a blob that predates the new fields.

## Sources of truth

- Design note: Nextcloud **"TREN - RTL explorer TUI (concept)"**, note id
  `1503454` (cat `TREN`). **Mirrored in this repo** for offline reading:
  - `docs/notes/TREN-concept.md` - the concept/design note
  - `docs/notes/TREN-backend-api-exploration.md` - yosys/slang/verilator report
  - `docs/notes/TREN-tui-exploration.md` - framework verdict + how TUIs work
  (Nextcloud stays canonical; each mirror carries its note id + etag.)
- Verified exploration + reports: the sibling **`tren_testing`** directory
  (`report.md` for backends, `report-tui.md` for the framework verdict,
  `t1_yosys/` `t3_slang/` `tui_textual/` for reproductions).
- Task breakdown: Nextcloud Deck board **"TREN"**.
- Pinned versions: Python 3.14, textual 8.2.8, rust 1.98, ratatui 0.30.2,
  yosys 0.69, sv-lang 11.0.
- How TREN is verified: `docs/verification.md` - the layered strategy
  (unit -> elaboration acceptance -> Pilot drives -> real-terminal round trip ->
  scale/cache profile -> real-artefact importers) and the change checklist.
- Component map: `docs/tech-concepts.md` (technical concepts -> the component
  that uses each) and `docs/sw-bom.md` (the auto-generated software BOM).