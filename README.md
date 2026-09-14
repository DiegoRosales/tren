# TREN — TUI RTL Explorer & Navigator

[![CI](https://github.com/DiegoRosales/tren/actions/workflows/ci.yml/badge.svg)](https://github.com/DiegoRosales/tren/actions/workflows/ci.yml)

A terminal RTL browser that treats a design hierarchy like a filesystem.
Three panes: **scope tree** | **focused RTL source** | **module I/O**.
`1`/`2`/`3` select the pane; `j`/`k` move, `l`/`h` descend/ascend, `y` yanks
the selection through the current path format as a raw OSC 52 clipboard write,
`Y` cycles the format (SV hierarchical / Vivado `get_pins` / yosys `select` /
VCD dot-path), `e` jumps to the declaration in `$EDITOR` (`+line file` style,
suspending the app).

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
nix develop . -c textual run src.tren.tui    # the TUI (once it exists)
```

The spike (`python -m tren.spike rtl/demo.sv`) runs yosys, builds the bit map
and prints drivers/loads of `top.out`, descending across the core boundary
down to the `$xor` that computes it — every row carries its `src` attribute.
Use `--keep` to keep the generated netlist under `.cache/tren/` for
inspection. Note: `assign o = {hi, lo}` produces **no cell** in yosys 0.69 —
the concatenation collapses to pure net aliasing, so `o` shows no driver
(its bit list literally overlays `hi` and `lo`); the loads are all there.

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