<!--
  MIRROR of a Nextcloud note — NOT the source of truth.
  source     : Nextcloud Notes app, category "TREN"
  note_id    : 1503469
  title      : TREN — backend API exploration & recommendations
  etag       : 11406c0439b03d3ce8773bcc47d1b775
  mirrored   : 2026-09-13
  The Nextcloud note is canonical; this file may drift. Refresh by re-fetching
  the note (the Nextcloud editor reformats markdown on save).
-->
# TREN — backend API exploration & recommendations

Date 2026-09-13 · toolchain: nix dev shell (`nix develop .`), flake pinned to
nixpkgs `nixos-unstable` @ `eaad0894` → **yosys 0.69 · verilator 5.052 ·
sv-lang 11.0.0** (older versions in README are stale; README updated).

Scope: which programmatic API each tool actually exposes today, and what to
base TREN's elaborator on. Everything below was run against a real installed
tool over the shared fixture `rtl/demo.sv` (params, generate, black box,
cross-boundary part-selects). Per-test detail + reproductions in
`t1_yosys/`, `t2_verilator/`, `t3_slang/` (`findings.md` + `run.sh` + `out/`).

---

## Recommendation (TL;DR)

1. **Bit-level connectivity: plain `yosys` JSON — not a plugin.** Run
   `read_verilog -sv; hierarchy -top <top>; proc; write_json out.json`.
   `proc` is *required* by the JSON backend and is *enough*; **never run
   `opt`** for TREN purposes — it prunes unobservable cells and nets (in this
   fixture it deleted the black-box cell, both generate instances and their
   nets from the JSON). All of TREN's bit-map/connected-driver machinery
   works off this one file.
2. **Scope tree + declarations + resolved parameters: `slang-hier` /
   `slang --ast-json`.** `slang-hier --params --custom-format
   "{inst}\t{module}\t{file}"` is the scope tree (generate included);
   `--ast-json --ast-json-source-info --ast-json-detailed-types` is the
   elaborated AST (ports, types, per-instance parameter values as `32'd32`,
   port-connection expressions, source spans). Use it for naming, structure,
   parameters, and pretty declarations.
3. **Verilator: use only as an optional third overlay.** Its modern API is
   `--json-only` (V3 AST), which is *not* the `--xml-only` the design note
   describes (removed in 5.052). It has the best declarations + per-instance
   connection expressions and per-pin part-select widths, but no bit graph,
   an un-materialised instance tree, fatal-by-default warnings, and it rejects
   genvar-indexed instance arrays in generate. Not a primary backend.
4. **Driving mechanism:** shell out to `yosys`/`slang` and parse JSON. All
   three outputs are deterministic, byte-comparable interchange formats;
   TREN's IR (scope tree + per-net-bit driver map + boundary links + source
   locs) must be one or two transforms away. The in-process `pyosys` (yosys's
   Python binding) and the Tcl driver both work and produce byte-identical
   JSON — keep them as an option for a single-process spike, not the shipped
   architecture.

This matches the note's original leaning (AST for what-you-wrote, netlist for
what-is-connected) — now verified against the installed tools, with the
operational caveats below.

---

## The three APIs at a glance

| | yosys | verilator | sv-lang (slang) |
|---|---|---|---|
| API for TREN | `write_json` netlist JSON | `--json-only` `.tree.json` + `.meta.json` | `--ast-json` + `slang-hier` |
| Connectivity graph | **bit-level (cells/connections/net bits)** | none (wiring as pin exprs) | none (connections as exprs) |
| Scope/instance tree | per-module cell objects, unflattened (`g[0].u_alu_g`) | re-derivable only | **explicit** (`top.u_core.g[0].u_alu_g`) |
| Resolved params | in `$paramod` name (un-mangle) | in mangled modName (`core__W20` hex) | **per-instance value strings** |
| Ports/directions | yes (netnames, blackbox ports incl. direction) | yes (VAR direction) | yes (Port direction) |
| Source locs | cell+net `src` attrs | file,line:col per node | file/line/column on every node |
| Required passes | `proc` mandatory for JSON | none | none |
| Black-box support | **strong** (`blackbox` attr + port list) | CELL with modName (no ports at box) | Instance with empty body |
| Programmatic extras | `pyosys` (SWIG C++ API), Tcl driver | — (sim headers only) | C API headers + `libsvlang.a` |
| Gotchas | needs `-sv`; `opt` destructive | warnings fatal; generate array pattern errors | hard-errors on multi-driver outputs |

## What each test established

### t1_yosys — the connectivity API is real and sufficient

- JSON schema confirmed: modules → `ports`/`netnames`/`cells`; cells →
  `type`/`connections`(bit ids)/`parameters`/`attributes.src`/`port_directions`;
  netnames → ordered `bits` + `attributes`.
- **`proc` is enforced**: an unprocessed module ("contains processes") is
  rejected by the JSON backend — so the note's "use pre-proc processes for
  source fidelity" is impossible via JSON; the ilang text (`dump`) is the
  source-faithful view. For TREN this is fine: proc-ed cells still carry
  source attrs, and connectivity is what we actually want.
- **Bit IDs module-local + positional-only cross-boundary, re-confirmed and
  hardened**: top's `u_core.a` = bits 34–65 vs core's internal `a` = bits
  2–33; and within net `o` the serialised bit list is *not monotonic*
  (`[34..65, 2..33]`). Only "k-th listed bit of the port" is sound linkage.
- **Unflattened generate/instance names**: `g[0].u_alu_g`, `u_alu0`, `u_sram`
  all live as cells in the parent module. `always_comb` becomes `$pmux`+`$eq`;
  localparam `OP=3` become constant connection bits `["1","1","0","0"]`.
- **Black boxes serialize completely**: body-less `sram_stub` emits
  `ports{direction,bits}` + `blackbox=1` attribute; instantiating cell carries
  `port_directions` — TREN's opaque-node port lists are in the JSON already.
- `opt` deletes unobservable cells/nets from the JSON (black box + generate
  instances dropped here) — don't use it.
- Driving: CLI (`-p`), Tcl (`-c`), and in-process Python (`pyosys`, via
  `-y`) all produce byte-identical JSON. pyosys duplicates the full C++ RTLIL
  API (`Design/Module/Cell/...`). Some SWIG quirks (no `Globals()` ctor,
  `Design.modules()` range uniterable, `dir(pyosys)` broken by `__dir__`)
  — documented in `t1_yosys/findings.md`.

### t2_verilator — `--xml-only` is gone; `--json-only` is the AST

- `verilator --xml-only` → `%Error: Invalid option` (5.052).
- `--json-only` → `Vtop.tree.json` (V3 AST: `modulesp`, `CELL` instances with
  `modName`/`pinsp`, `PIN.exprp` connection expressions incl. `SEL` part-select
  widths, `VAR` ports with `direction`, `GENBLOCK`, `ALWAYS`/`ASSIGN`/`CASE`)
  + `.tree.meta.json` (`files`, `pointers`).
- No bit graph; instance tree not materialised (`topScopep:"UNLINKED"`).
- Params only in mangled module names (`core__W20`, hex).
- Operationally: warnings are fatal by default (`-Wno-*` needed); the common
  `for (genvar) begin … u_inst[i] …` pattern is rejected ("Size of range is
  '[0]'") and had to be removed from the shared fixture.
- Worth remembering: the per-pin `.exprp` trees (with `widthConst`) are the
  most explicit wiring expression in any of the three ASCII formats.

### t3_slang — the structural half TREN needs, ready to consume

- `slang --ast-json --ast-json-source-info --ast-json-detailed-types` gives an
  elaborated AST: instance tree with `InstanceBody` per scope; Port+Variable;
  Parameter with **resolved `value`** (`"32'd32"`) and source initializer;
  connection expressions per instance port (RangeSelect/NamedValue/Integer
  literal + spans); `ContinuousAssign`/`ProceduralBlock` statements;
  `GenerateBlockArray [Genvar, GenerateBlock, ...]`.
- `--ast-json-scope <path>` slices the dump to one subtree — a cache-friendly
  option for lazy loading.
- `slang-hier`: tree + `--params` (**`W=32'd32, OP=4'b11`** per instance —
  the cleanest param answer of the three) + `--custom-format` for
  machine-readable output + `--inst-prefix`/`--inst-regex`/`--max-depth`.
- SV conformance enforced (nice validation layer), C API headers + static lib
  shipped; `pyslang` still absent.

## Implications for the note's open questions

- **Q1 (which backend survives contact):** precedent set — all three
  elaborate this fixture (after making it SV-conformant); slang-hier +
  yosys JSON are the pair to build on. Real-RTL scale check
  (`zybo_sampler`) is still the follow-up.
- **Q2 (where on the proc spectrum):** the JSON backend decides it — `proc`
  is required, `opt` is banned. `proc`-only keeps src attributes and full
  hierarchy.
- **Q3 (name mangling):** confirmed + now has a countermeasure story
  (`$paramod` un-mangling; slang-hier for clean names).
- New: the `--xml-only` claim is wrong on current Verilator (use
  `--json-only`), and `pyslang` is still not packaged but a C API is.

## Suggested next step (spike)

Single pass over `rtl/demo.sv`:

1. `slang-hier --params` → scope tree + params (+ port lists from
   `slang --ast-json`).
2. `yosys -p "read_verilog -sv …; hierarchy -top top; proc; write_json …"` →
   bit map, inverted to (cell, port, bit) drivers/loads.
3. Join across the boundary positionally, using the mangled module name to
   key back to slang's clean module names.
4. That is the note's entire spike, with every step now verified against real
   APIs and pinned tool versions.