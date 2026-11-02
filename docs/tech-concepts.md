# TREN - Technical concepts and the components that use them

This document lists the technical concepts behind TREN and the component
that uses each concept. A component is a file in `src/tren/`, a tool in the
dev shell, or a document. The table groups concepts by subsystem.

The source of truth for the design is the concept note
(`docs/notes/TREN-concept.md`, Nextcloud note 1503454). The verified
behaviour of the backends lives in `docs/verification.md` and the sibling
`tren_testing` reports. This document is the map: concept -> component.

## 1. The intermediate representation (IR)

| Concept | What it does | Component | Location |
|---|---|---|---|
| Widget-agnostic IR | A backend-agnostic model of scopes, ports, nets, params, connectivity and boundary links. The TUI never sees a yosys or slang type. | `ir` module | `src/tren/ir.py` |
| Frozen dataclasses | Immutable `Scope`/`Port`/`Net`/`Param`/`Instance`/`Cell`/`BitLink` records carry the IR through cache, backends and UI. | `ir` module | `src/tren/ir.py` |
| Lossless JSON round-trip | `Design.to_json`/`from_json` preserve every field, so the on-disk cache reloads without drift. | `ir` module | `src/tren/ir.py` |
| Positional boundary link | A parent-side connection to a child port maps to the child net by bit position, never by bit number. This is the hierarchy-crossing rule. | `ir`, `bits`, `elaborate`, `tui` | `ir.py:BoundaryLink`, `bits.py:cross_into`, `tui.py:_trace_expand` |
| Netlist cell | An internal yosys cell (`$xor`, `$pmux`, ...) with its pin directions. It is not a submodule instance: the trace steps through a cell to the nets on its other side. | `ir`, `elaborate`, `tui` | `ir.py:Cell`, `ir.py:CellPin`, `elaborate.py:_cells_for`, `tui.py:_cell_pin_nets` |
| Canonical literal compare | `.overridden` compares a resolved value with a declared default as numbers, because slang and source text spell one literal differently. | `ir` module | `ir.py:literal_int`, `ir.py:Param.overridden` |

## 2. Elaboration backends

| Concept | What it does | Component | Location |
|---|---|---|---|
| yosys write_json netlist | The bit-level connectivity source of truth: `netnames`, cell `connections` and per-module `bit ids`. | yosys (dev shell), `bits` | `elaborate.py:run_yosys`, `bits.py:load_netlist` |
| proc, never opt, never flatten | `proc` is required by the JSON backend; `opt` prunes undriven cells; `flatten` removes the hierarchy. | yosys invocation | `elaborate.py:run_yosys`, `spike.py:run_yosys` |
| Bit map | Invert `netnames`+`connections` into a per-bit map of drivers and loads. | `bits` module | `bits.py:rows_hitting_bits`, `drivers_of`, `loads_of` |
| Module-local bit ids | Bit ids are valid only inside one module; constants (`"0"`, `"x"`) are not ids. | `bits`, `elaborate` | `bits.py:cell_connections`, `elaborate.py:_bit_key` |
| `$paramod` unmangling | yosys mangles module keys to `$paramod\name\PARAM=val`; the base name and resolved params are recovered. | `bits` | `bits.py:unmangle` |
| slang-hier scope tree | Every instance path, generate scopes included, with per-instance resolved params. | sv-lang (dev shell), `elaborate` | `elaborate.py:run_slang_hier` |
| slang --ast-json declarations | Source-faithful types, declared ranges, param kinds and source locations. | sv-lang (dev shell), `elaborate` | `elaborate.py:run_slang_ast`, `_parse_ast_decls` |
| CLI wrapper | A pinned argv is built and subprocessed per backend; the output feeds the IR assembly. | `elaborate` | `elaborate.py:_run`, `run_yosys`, `run_slang_hier`, `run_slang_ast` |
| Partial elaboration | Missing modules stay opaque as black boxes; the design always opens. | `elaborate`, `tui` | `elaborate.py:build_design`, `tui.py:action_toggle_cut` |

## 3. Persistence and the cache

| Concept | What it does | Component | Location |
|---|---|---|---|
| Manifest | One normalised structure that decouples where the filelist came from from which elaborator runs. Importers write it, the elaborator reads it, a user can edit it. | `manifest` | `src/tren/manifest.py` |
| On-disk elaboration cache | Elaborate once, serialise to `.cache/tren`, start instantly on a warm cache. | `cache`, `ir` | `src/tren/cache.py`, `elaborate.py:elaborate` |
| Input-keyed cache key | A sha256 of the canonical manifest plus each source's mtime and size invalidates on any edit. | `cache` | `cache.py:inputs_key`, `cache_key` |
| IR schema fingerprint | The cache key includes a digest of the IR dataclass layout, so a shape change re-elaborates once. | `cache`, `ir` | `ir.py:schema_id`, `SCHEMA_VERSION` |
| Cache sweep | Dead blobs from an older IR schema are removed after a write. | `cache` | `cache.py:_sweep` |

## 4. Importers

| Concept | What it does | Component | Location |
|---|---|---|---|
| Importer as bootstrap | Importers produce a manifest once; the result is hand-fixed and checked in, not regenerated every run. | `importer`, `vivado` | `src/tren/importer.py`, `src/tren/vivado.py` |
| Filelist grammar | A plain `.f`/directory importer reads `-I`, `-D`, `+incdir+` and sources, mirroring slang's command-file grammar. | `importer` | `importer.py:parse_filelist`, `import_manifest` |
| Vivado project parser | `.xpr` FileSets, `TopModule`, and the `$PSRCDIR`-style variable scheme resolve to a source set. | `vivado` | `vivado.py:parse_vivado`, `_resolve` |
| Recorded reality | `vivado.jou`/`settings.tcl` supply the top when the XML has none; a directory scan completes the sources. | `vivado` | `vivado.py:_recorded_top`, `_scan_verilog` |

## 5. The TUI

| Concept | What it does | Component | Location |
|---|---|---|---|
| Three-pane screen | Scope tree, focused RTL source and module I/O share one row. | textual (dev shell), `tui` | `src/tren/tui.py`, `TrenApp` |
| Lazy scope population | Tree nodes are added on demand when a scope expands; the whole graph is never materialised. | `tui` | `tui.py:_ensure_children`, `_visible`, `_find_scope_node` |
| Source-span parsing | Reads both backend spellings (`file:line.col` from yosys, `file:line:col` from slang) into one `(file, lo, hi)`. | `tui` | `tui.py:_parse_src` |
| Focus band and auto-follow | The source pane keeps the focused range visible and re-centres only on a new focus. | `tui` | `tui.py:_follow`, `_render_source` |
| Trace split | `d`/`L` open a pane below the source with the drivers/loads as a tree. `enter` expands a row (a boundary into the child net, a cell into its pins), `backspace` collapses one level, `d`/`L` switch direction. | `tui` | `tui.py:_open_trace`, `TraceNode`, `_trace_expand`, `_walk_trace` |
| Black boxes and user cuts | Body-less modules render opaque with their ports; `X` cuts/restores any scope at runtime. | `tui` | `tui.py:action_toggle_cut`, `_trace_rows` |
| Status line | A yazi-style path bar shows the selection's full hierarchical path. | `tui` | `tui.py:_selection_status` |
| Pane cue and zoom | The active pane is lifted out of the background; `z` gives it the full width. | `tui` | `tui.py:_render_pane_cue`, `action_toggle_zoom` |
| Declarative yank formats | Path formats are data, not code; one renderer applies separator, top, array and escape settings. | `formats` | `src/tren/formats.py`, `tui.py:action_yank` |
| Tcl/shell escaping | Brackets and `$` are escaped so a pasted Vivado path is not command substitution. | `formats` | `formats.py:_ESCAPERS` |
| Clipboard provider | `y` writes through OSC 52 or `tmux load-buffer -w`; `auto` uses tmux inside tmux and OSC 52 elsewhere. | `clipboard`, `tui` | `src/tren/clipboard.py`, `tui.py:action_yank` |
| Editor jump | `e`/`E` suspend the app and open `$EDITOR` at `+line file`. | `tui` | `tui.py:_open_editor` |

## 6. Verification and tooling

| Concept | What it does | Component | Location |
|---|---|---|---|
| Headless Pilot drives | The real app is driven in-process by textual's `Pilot`; state is asserted, not screenshots. | textual, `test_drive.py` | `test_drive.py`, `docs/verification.md` |
| Layered verification | Unit, elaboration acceptance, drive, real-terminal, profile and importer gates run in that order. | Makefile, docs | `docs/verification.md`, `Makefile` |
| Real-terminal round trip | A private tmux server captures the OSC 52 bytes and the `$EDITOR` argv. | tmux (dev shell), tools | `tools/osc52_roundtrip.sh`, `Makefile` (`roundtrip`) |
| Pane-cue capture | A real terminal is decoded to prove exactly one pane is lit on rows that carry tree nodes. | tmux (dev shell), tools | `tools/pane_cue_capture.sh`, `Makefile` (`pane-cue`) |
| Scale and cache profile | A synthetic 1100-scope IR records lazy materialisation and cold/warm tool-call counts. | tools | `tools/profile_1k_scopes.py`, `Makefile` (`profile`) |
| Nix dev shells | The flake pins Python, textual, yosys, sv-lang, rust and tmux so every shell cannot drift. | `flake.nix` | `flake.nix`, `pyproject.toml` |
| FHS sandbox | `buildFHSEnv` gives Xilinx tools a conventional `/usr/lib` to run in. | `flake.nix` | `flake.nix` (`#xilinx`) |

## 7. Document map

| Document | What it is | Used by |
|---|---|---|
| `docs/notes/TREN-concept.md` | The design note mirror (canonical: Nextcloud note 1503454). | architecture decisions |
| `docs/notes/TREN-backend-api-exploration.md` | Verified yosys/slang/verilator API report. | backend choices |
| `docs/notes/TREN-tui-exploration.md` | Framework verdict and how TUIs work. | UI choice |
| `docs/verification.md` | The layered verification strategy and change checklist. | acceptance |
| `docs/sw-bom.md` | The auto-generated software bill of materials. | dependency audit |
