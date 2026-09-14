# TREN — TUI RTL Explorer & Navigator

[![CI](https://github.com/DiegoRosales/tren/actions/workflows/ci.yml/badge.svg)](https://github.com/DiegoRosales/tren/actions/workflows/ci.yml)

A terminal RTL browser that treats a design hierarchy like a filesystem.
Three panes: **scope tree** | **focused RTL source** | **module I/O**.
`1`/`2`/`3` select the pane; `j`/`k` move, `l`/`h` expand/collapse the
highlighted scope, `enter` shows that module in the centre RTL pane, `ctrl+u`
jumps up a hierarchy level, `y` yanks the selection through the current path
format as a raw OSC 52 clipboard write, `Y` cycles the format (SV
hierarchical / Vivado `get_pins` / yosys `select` / VCD dot-path), `e` jumps
to the declaration in `$EDITOR` (`+line file` style, suspending the app).

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
nix develop . -c python tools/profile_1k_scopes.py # lazy-load + cache profile (1 100-scope synthetic IR, card 215)
nix develop .#xilinx                        # FHS sandbox for Xilinx tools (interactive; then: make fft_wrapper_HLS)
nix develop . -c make run                   # start the TUI (keys: see 'User guide' below)
```

The spike (`python -m tren.spike rtl/demo.sv`) runs yosys, builds the bit map
and prints drivers/loads of `top.out`, descending across the core boundary
down to the `$xor` that computes it — every row carries its `src` attribute.
Use `--keep` to keep the generated netlist under `.cache/tren/` for
inspection. Note: `assign o = {hi, lo}` produces **no cell** in yosys 0.69 —
the concatenation collapses to pure net aliasing, so `o` shows no driver
(its bit list literally overlays `hi` and `lo`); the loads are all there.

## User guide

### Starting the TUI

```sh
nix develop . -c python -m tren.tui --manifest rtl/demo.manifest.json   # real elaboration
nix develop . -c python -m tren.tui                                     # vendored demo fixture
# or: nix develop . -c make run
```

The screen is three panes — **tree** (the scope hierarchy, like a filesystem)
| **source** (the focused module's RTL) | **I/O** (parameters + ports). `q`
quits.

### Keys

All bindings work from any pane unless noted; `x` below means the active
pane.

| Key | Effect |
|---|---|
| `1` `2` `3` | select the tree / source / I/O pane |
| `j` `k` | move down/up: in the tree, through the visible nodes; in source, scroll; in I/O, select a port |
| `l` | tree: expand the highlighted scope (reveal its children) |
| `h` | tree: collapse the highlighted scope (hide its children) |
| `enter` | tree: show the highlighted module in the centre RTL pane, with context |
| `ctrl+u` | tree: step up one level, current scope first — a port/net leaf jumps to its own module, a module row to its parent |
| `y` | yank the selection through the current path format (raw OSC 52 → the terminal's clipboard) |
| `Y` | cycle the yank format: `sv` → `vivado` → `yosys` → `vcd` |
| `d` `L` | trace **drivers** / **loads** of the selected net (opens the trace modal) |
| `X` | cut/restore the highlighted scope (treat it as a black box) |
| `;` | dump the current trace path as text |
| `e` | open `$EDITOR` at the focused `file:line` (app suspends and returns) |
| `q` | quit |

In the **trace modal**: `enter` crosses a boundary into the child scope,
`backspace` pops the trace stack, `escape` closes it.

### Navigating

`j`/`k` move through the tree **without unfolding** anything — only `l`
expands the highlighted scope to reveal its children (file-manager model),
and `h` collapses it again. `ctrl+u` steps up one granularity level, current
scope first: from a port/net leaf it jumps to that module's row (not past it
to the parent); pressed again it moves up to the parent module. `enter` puts
the highlighted module into the centre. The center pane shows the **real RTL
file** at the module's *instantiation in its parent* (or its declaration at
the top), with the focused range highlighted and kept in view — `e` opens
the editor at exactly that line.

### Yanking paths

`y` writes the path of the current selection to the clipboard as a raw OSC 52
sequence (works in any terminal that relays it, e.g. tmux with
`set-clipboard`, most terminals). The formats are declarative (see
`tren/formats.py`), one per target tool:

| Format | Selection `top.u_core.g[0].u_alu_g.result` |
|---|---|
| `sv` | `top.u_core.g[0].u_alu_g.result` |
| `vivado` | `u_core/g\[0\]/u_alu_g/result` — top excluded, `/`, Tcl-escaped brackets |
| `yosys` | `top/u_core/g[0]/u_alu_g/result` |
| `vcd` | `top.u_core.g[0].u_alu_g.result` |

### Tracing drivers/loads

Select a net to the tree, then `d` (drivers) or `L` (loads). The modal lists
the touching cells; `enter` on a boundary row crosses **positionally** into
the child scope's net (bits are module-local — matching by ID across a
boundary is wrong by design). `;` dumps the breadcrumb, e.g.
`top.out ▶ top.u_core.alu_result`.

### Black boxes and cuts

Body-less modules (`sram_stub`-style) render as opaque scopes with their
ports and a `!` marker. `X` adds an explicit cut to any scope — the trace
stops at it with the reason shown — and `X` again restores it.

### Headless / CI

`make check` runs the unit suite plus both Pilot drives — the same gates the
CI workflow runs. `make test` / `make drive` / `make drive-real` run them
individually; `make roundtrip` verifies the OSC 52 + editor path against a
real terminal on a private tmux server.

### Reading Vitis HLS RTL output (no Xilinx needed)

The committed `test/hls_examples/hls/<block>/rtl_output` exports are read
directly: `make hls-import`, `make hls-elaborate` and `make hls-run` import /
elaborate / browse one block (`BLOCK` defaults to `fft_wrapper`). The
elaborator handles the generated Verilog-2005 including yosys's constant / x
bits. Example: `fft_wrapper` bundles 140 scopes, ~50k bit-links and 1763
boundaries.

## Importers

Manifest producers — bootstrap, not a dependency (`tren import`).

- **Plain filelist** (`.f`, slang `-F` grammar) or a bare source directory.
- **Vivado** (`project.xpr`, auto-detected): parse `<FileSets>` DesignSrcs
  (+`TopModule`) and resolve Vivado's `$PSRCDIR`-style variables; complete
  the sources with a directory scan because batch/HLS projects carry the RTL
  outside the FileSets. `.vh` files are `\`include`\`-only (excluded),
  generated-dir duplicates are dropped, and non-Verilog FileSet entries
  (`.bd`) warn. Verified against a Vitis HLS 2025.2 export: yosys
  `read_verilog` parses the generated Verilog-2005 cleanly; `slang-hier`
  additionally needs Vivado's `vivado_interfaces.svh` interface library for
  the block-level `bd_0_sv.sv`, which the workspace does not ship — pass
  `--top fft_wrapper` (or add the lib dir) to elaborate the HLS core.

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
rtl/                 small SystemVerilog fixture for tests
tests/               PILOT_WORKDIR for textual Pilot tests + unit tests
manifest.json        hand-checkable design manifest (sources/incdirs/top/…)
```

## Decisions already made (verified — do not re-derive)

- **Backends**: yosys `write_json` after `proc` (never `opt`; bit IDs are
  positional-only across scopes); `slang-hier --params` for scope tree +
  resolved parameter values. Do not `flatten`.
- **UI**: textual 8.2 for the spike/MVP; ratatui 0.30 pinned as the rewrite
  target. The IR and navigation logic are kept framework-agnostic.
- **Screen**: tree | source (focused on the module's instantiation in its
  parent) | I/O; keys as above; OSC 52 yank.
- **Scale**: lazy scope population on expand; never materialize the whole
  graph; IR cache keyed by manifest + source mtimes.

## Sources of truth

- Design note: Nextcloud **"TREN — RTL explorer TUI (concept)"**, note id
  `1503454` (cat `TREN`). **Mirrored in this repo** for offline reading:
  - `docs/notes/TREN-concept.md` — the concept/design note
  - `docs/notes/TREN-backend-api-exploration.md` — yosys/slang/verilator report
  - `docs/notes/TREN-tui-exploration.md` — framework verdict + how TUIs work
  (Nextcloud stays canonical; each mirror carries its note id + etag.)
- Verified exploration + reports: the sibling **`tren_testing`** directory
  (`report.md` for backends, `report-tui.md` for the framework verdict,
  `t1_yosys/` `t3_slang/` `tui_textual/` for reproductions).
- Task breakdown: Nextcloud Deck board **"TREN"**.
- Pinned versions: Python 3.14 · textual 8.2.8 · rust 1.98 · ratatui 0.30.2 ·
  yosys 0.69 · sv-lang 11.0.
- How TREN is verified: `docs/verification.md` — the layered strategy
  (unit → elaboration acceptance → Pilot drives → real-terminal round trip →
  scale/cache profile → real-artefact importers) and the change checklist.