# TREN — TUI RTL Explorer & Navigator

A terminal RTL browser that treats a design hierarchy like a filesystem.
Three panes: **scope tree** | **focused RTL source** | **module I/O**.
`1`/`2`/`3` select the pane; `j`/`k` move, `l`/`h` descend/ascend, `y` yanks
a hierarchical path, `e` jumps to the declaration in `$EDITOR`.

Status: **spike phase, built on textual 8.2**. The backend pair is verified
and pinned (see *Sources of truth*): yosys `write_json` for bit-level
connectivity, `slang-hier` / `slang --ast-json` for the scope tree and
resolved parameters. Rust + ratatui is the earmarked rewrite target,
revisited when a real 100k-instance IR shows the ceiling matters.

## Quick start

```sh
nix develop .                                # python + textual + yosys + slang
nix develop . -c python test_drive.py        # headless Pilot tests (CI target)
nix develop . -c textual run src.tren.tui    # the TUI (once it exists)
```

## Layout (intended)

```
src/tren/            package
  ir.py              widget-agnostic IR (scopes, nets, params, bit map)
  elaborate.py       yosys + slang-hier/slang --ast-json -> IR (+ cache)
  importers/         manifest producers (plain .f, verilator obj_dir, vivado…)
  formats.py         yank format registry + templates
  tui.py             the textual app (implements the screen + keys only)
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
  `1503454` (cat `TREN`).
- Verified exploration + reports: the sibling **`tren_testing`** directory
  (`report.md` for backends, `report-tui.md` for the framework verdict,
  `t1_yosys/` `t3_slang/` `tui_textual/` for reproductions).
- Task breakdown: Nextcloud Deck board **"TREN"**.
- Pinned versions: Python 3.14 · textual 8.2.8 · rust 1.98 · ratatui 0.30.2 ·
  yosys 0.69 · sv-lang 11.0.