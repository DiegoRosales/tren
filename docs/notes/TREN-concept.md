<!--
  MIRROR of a Nextcloud note — NOT the source of truth.
  source     : Nextcloud Notes app, category "TREN"
  note_id    : 1503454
  title      : TREN — RTL explorer TUI (concept)
  etag       : f7b5a0aa40295ae5d2032c845f740fef
  mirrored   : 2026-09-13
  The Nextcloud note is canonical; this file may drift. Refresh by re-fetching
  the note (the Nextcloud editor reformats markdown on save).
-->
# TREN — RTL explorer TUI (concept)

**Written 2026-09-13.** Status: **idea exploration**, nothing built, no code
written.

**Name: TREN** — TUI RTL Explorer & Navigator. Also *tren*, Spanish for *train*:
a real word, four letters, trivially typeable as a shell command, and it fixes
the unpronounceability of a bare "TRN". See *Naming* at the end for what was
rejected and why.

As of 2026-09-13 the FOSS toolchain (yosys 0.69, verilator 5.052, sv-lang 11.0,
surelog 1.86, verible 0.0.4148) is installed in the `tren_testing` nix dev
shell, and the backend-capability claims below have been verified against them
on a small parameterised design; findings, per-test detail and a backend
recommendation live in `tren_testing/report.md` (per-tool notes in
`tren_testing/t1_yosys/`, `t2_verilator/`, `t3_slang/`). Vendor
build-directory layout claims are still from memory ⚠. Claims I am least sure
of are marked ⚠.

---

## The problem

Reading unfamiliar RTL means answering a small set of questions over and over:

- What drives this signal, and through what?
- Where does this port go once it leaves the module?
- Who instantiates this module, and with what parameters?
- What is the full hierarchical path of this thing, so I can force/probe it?
- Which of the four `genblk` copies am I actually looking at?

Today the tools that answer these are GUI schematic viewers — Vivado's
*Elaborated Design* / *Schematic*, Verdi, DVE, SpyGlass. They are:

- licence-gated and often the reason you need a remote workstation at all,
- slow to launch (minutes to elaborate before you can click anything),
- painful over SSH / X-forwarding,
- outside the terminal + `$EDITOR` + git workflow entirely.

And the irony: **you almost never want the picture.** The schematic is a means to
an end. What you want is *navigation* — and navigation is a text problem.

## The pitch

> A terminal RTL browser that treats the design hierarchy like a filesystem.
> `yazi` keybindings, `h`/`l` to go in and out of scopes, `Enter` on a signal to
> jump to its driver — even when the driver is three levels up through a port.
> Elaboration comes from FOSS frontends (yosys / slang / Verilator); `tren` is
> only the navigator.

Explicitly **not** a block diagram renderer. No ASCII schematics, no auto-layout.
That is a different (harder, less useful) project.

## Why the file-explorer metaphor fits

The mapping is almost suspiciously clean:

| Filesystem     | RTL                                                         |
|----------------|-------------------------------------------------------------|
| directory      | scope — module instance, generate block, interface instance |
| file           | leaf object — net, port, parameter, register, process       |
| path           | hierarchical path `tb.dut.core.alu`                         |
| symlink        | port connection — the same electrical net under two names   |
| `cd` / `..`    | descend into an instance / pop to parent                    |
| preview pane   | port map, declaration, drivers/loads, source snippet        |
| `find`         | cross-design net/module search with globbing                |
| broken symlink | **black box** — a scope whose contents we cannot see        |

Miller columns (yazi/ranger/nnn) give you *parent scope | current scope |
preview* for free, which is exactly the amount of context you want when walking a
hierarchy. The path bar at the top is already the hierarchical path, which is the
string you wanted to copy to the clipboard anyway.
The one place the metaphor breaks: **a net is not contained by one scope the way
a file is contained by one directory.** A net that crosses a module boundary is a
single electrical thing with two names in two scopes. That is the interesting
part of the problem and it is where the design work actually lives — see
*Crossing the boundary* below.

---

# Getting the design in

**This is the feature that decides whether anyone ever opens the tool.** Building
a filelist by hand to go poke at some RTL for five minutes is annoying enough
that you just don't bother — you grep instead, badly. If the tool needs a curated
`.f` file before it starts, it has already lost to `rg`.

But the filelist almost always *already exists*, somewhere, in a form somebody's
build flow produced. The job is to go find it.

## The manifest

One normalised structure that everything else is derived from. Importers write
it, the elaborator backends consume it, and the user can hand-edit it:

```
sources    [{path, language, library, is_blackbox_stub}]
incdirs    []
defines    {NAME: value}
params     {NAME: value}       # top-level generic/parameter overrides
top        "tb_top"
libraries  {logical: physical}  # VHDL / mixed-language
blackboxes ["*_sram_wrapper", "unisim.*"]   # user-declared cuts
```

The important architectural property: **the manifest decouples where the filelist
came from from which elaborator runs.** You can import from a Vivado project and
elaborate with slang. That is what makes the tool usable inside a proprietary
shop — the vendor flow stays the source of truth about *what the design is*, FOSS
tools do the reading.

Corollaries worth having:

- `tren export -f` writes a plain `.f` filelist out of any importer. Independently useful even if you never open the TUI — *"just get the filelist out of this Vivado project"* is a request people have constantly.
- The manifest is a checkable-in file. Auto-detect is a **bootstrap, not a dependency**: run it once, commit the result, hand-fix the three things it got wrong.

## Importers

Run `tren` with no arguments and it walks up from `$PWD` looking for recognisable
build artefacts, then presents a picker:

```
  found 3 designs
> vivado    zybo_sampler.xpr          top zybo_top      142 files
  questa    sim/work/                 12 libs, 380 units
  verilator obj_dir/Vtb_top.mk        top Vtb_top        44 files
```

Candidate sources, roughly in order of how reliably they can be parsed:

| Flow              | Artefact                                                                                                                                                       | Confidence |
|-------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------|------------|
| Verilator         | `obj_dir/V<top>.mk` + `.d` depfiles — full source list, plainly                                                                                                | high       |
| Vivado (project)  | `.xpr` is XML: `<File Path=…>`, `TopModule`, `Generics`, include dirs, per-file library + type                                                                 | high       |
| Vivado (any mode) | `vivado.jou` — a replay log of every `read_verilog` / `set_property` / `synth_design`. Generic and underrated                                                  | high       |
| FuseSoC / Edalize | generates an explicit filelist per backend in the build dir                                                                                                    | high       |
| Bender            | `bender script` emits filelists directly                                                                                                                       | high       |
| Questa / ModelSim | `work/_info` records source path + compile options per design unit; `modelsim.ini` has the lib mappings. Shell out to `vdir` rather than parsing `_info` ⚠     | medium     |
| VCS               | recover the compile command line from the `-l` log / `csrc/` artefacts; then parse `-f`, `+incdir+`, `-y +libext+`, `-pvalue+`, `-top`. Exact artefact names ⚠ | medium     |
| Xcelium           | `xrun.log` command line; `cds.lib` + `hdl.var` for lib mapping; `xcelium.d/` snapshot ⚠                                                                        | medium     |
| Plain `.f`        | with `-f`/`-F` recursion, `+incdir+`, `+define+`, `-y`/`+libext+`                                                                                              | high       |
| yosys / iverilog  | the `.ys` script or `-f` command file                                                                                                                          | high       |

**Generalisation worth noticing:** for the big three simulators, the most robust
source is not a structured database at all — it is **the recorded command line**
(`vivado.jou`, `xrun.log`, the VCS compile log). Every one of these flows leaves a
log with the full invocation in it. A decent generic "scrape a compile command
line out of a log and normalise the switches" importer probably covers more
ground than any single vendor-specific parser, and degrades gracefully.

Second-order: `-y` library directories and `+libext+` mean the source list is
*implicit*. Either resolve them eagerly (glob the dirs) or pass them through to a
backend that understands them. slang and Verilator both do ⚠.

---

# Backends — who does the elaboration

TREN should parse nothing itself. Survey of candidates (backend rows verified
2026-09-13 in the `tren_testing` nix dev shell):

| Tool                 | Output                      | Fidelity                               | SV support        | Notes                                                                                                                                                         |
|----------------------|-----------------------------|----------------------------------------|-------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------|
| yosys                | `write_json` netlist        | **connectivity, bit-level**            | weak natively     | the only one that hands you a real graph; JSON backend requires `proc`; do not `opt`                                                                          |
| yosys-slang (plugin) | same as yosys               | same                                   | good              | slang frontend into RTLIL — likely the sweet spot, but NOT packaged in nixpkgs; would need `yosys.withPlugins` + a manual derivation, so treat as future work |
| slang                | `--ast-json` elaborated AST | source-faithful, full param resolution | **best in class** | `pyslang` still not packaged; the package ships a C API (`libsvlang.a`); `slang-hier` dumps the scope tree incl. generate                                     |
| Verilator            | `--json-only` V3 AST        | post-elaboration AST                   | good              | `--xml-only` is gone since 5.052; `--json-only` emits `.tree.json` + `.tree.meta.json` (pointer-linked V3 AST)                                                |
| Surelog              | UHDM database               | full elaboration                       | good              | heavier dependency, C++/Python API                                                                                                                            |
| Verible              | `--export_json` CST         | **syntax only, no elaboration**        | good              | useful for source-faithful views, cannot resolve params                                                                                                       |
| pyverilog            | dataflow graph              | has a real dataflow analyser           | Verilog-2001 only | closest prior art for signal tracing; likely too limited                                                                                                      |

## The central tension: AST vs netlist

These two are not the same product and the choice shapes everything.

**AST (slang / Verilator)** keeps `always_ff` blocks, generate structure,
expressions and source correspondence intact. It tells you *what the designer
wrote*. But there is no driver/load graph — you would have to build it yourself
by walking assignment statements, and part-selects, `for` loops inside generate,
and procedural assignment across branches make that genuinely hard.

**Netlist (yosys)** hands you the connectivity graph you actually want. Concrete
mechanism in the `write_json` format:

- `netnames` maps each signal name to a list of **bit IDs**;
- each cell's `connections` maps port name → list of those same bit IDs;
- so drivers/loads = invert the bit → (cell, port, index) map. **Part-selects and bit-level fan-out fall out for free**, which is exactly what the GUI tools are good at and grep is terrible at.

The catch is a second tension inside yosys itself — resolved 2026-09-13:

- the JSON backend refuses unprocessed designs ("contains processes ... run `proc` first"), so the pre-proc source-faithful view is only available as ilang text (`dump` / `write_rtlil`), not JSON;
- `proc` alone is the sweet spot: every cell, net, hierarchy and `src` attribute survives, and an `always_comb` becomes a `$pmux` graph you can trace;
- `opt` is destructive for this use — on the test design it pruned the black-box instance, both generate instances and their nets out of the JSON.

**Do not flatten.** Unflattened, a cell of type `\submodule` keeps the hierarchy:
the cell's connections are parent-scope bits, the submodule's ports are
child-scope bits, and position `i` of a port corresponds in both. *That
correspondence is the hierarchy-crossing rule*, and it is the whole feature.

**Leaning (now verified):** plain yosys JSON as the connectivity source of truth,
with slang (`slang-hier` / `--ast-json`) providing the scope tree, resolved
parameters and pretty declarations. Design the IR so either can be absent.

---

# Black boxes

Not an edge case — **the normal case**. Encrypted vendor IP, `.xci` cores,
unisim/simprim primitives, SRAM-compiler macros, analog blocks, stdcells,
not-yet-written modules, and the 50k-instance subsystem you deliberately do not
want to descend into. A tool that refuses to open a design because three modules
are missing is useless in exactly the situations you most need it.

**Stated product requirement: TREN always opens something.** Partial elaboration
is the expected mode, not an error mode. Unresolved modules degrade to opaque
nodes; everything around them stays navigable.

## Where the port list comes from

A black box is only useful if you know its boundary — with ports known you can
still trace *up to* the boundary and resume on the other side. Sources, best
first:

1. **A stub file.** yosys already has the exact mechanism: `read_verilog -lib stubs.v` reads a file as declarations only — ports, no body — and the `(* blackbox *)` attribute marks it explicitly. This is the paved road. Verified bonus: the JSON netlist serialises body-less modules with a `ports` map (direction, bit range) and a `blackbox=1` attribute, and the instantiating cell carries `port_directions` — so a TREN black box gets its port list straight from the netlist.
2. **IP-XACT component.xml.** Ships with `.xci` cores and with anything packaged. Full port list with directions and widths. Direct overlap with the IPYFuse work — the component parser there is the same job.
3. **Liberty .lib.** `read_liberty -lib` gives pins *and directions*. Better still, see the timing-arc idea below.
4. **The instantiation site.** With no stub at all, named port connections give you port names and widths-as-connected. Directions unknown — **render them as unknown rather than guessing**; a wrong direction silently corrupts every trace that passes through.

## Tracing through a box

Default: a black box is opaque, and a trace that reaches it stops there and says
so, clearly, naming *which* box and *why* it is one.

But there is a good opportunistic trick: **Liberty timing arcs encode real
input-to-output connectivity.** Each output pin's `related_pin` list is precisely
a fan-in relation. Where a `.lib` exists (ASIC flows, hard macros), TREN can
trace *through* a black box with real information rather than guessing. The same
idea from an IP-XACT file is weaker but sometimes present.

## User-declared cuts

Same machinery, opposite motivation: mark modules as black boxes *even when
source exists*.

```
blackboxes = ["unisim.*", "*_sram_wrapper", "big_subsystem"]
```

Massively faster elaboration, much less clutter, and it makes the tool usable on
an SoC where you only care about one corner. Toggleable live — `X` on a scope to
cut it, and reopening it re-elaborates just that subtree.

## Display

Black-box scopes render distinctly in the tree — the "broken symlink" of this
filesystem. Descending into one shows the port list, where each port goes in the
parent, the provenance of the port info (stub / IP-XACT / liberty / inferred),
and the reason it is a box (source missing / encrypted / user cut / vendor
primitive). *Knowing why* matters: "encrypted, nothing to be done" and "you
forgot a `-y` directory" are very different situations and the tool should say
which one it is. The second one is a fixable manifest bug and TREN is well-placed
to suggest the fix.

---

# The IR

One backend-agnostic intermediate model, so the TUI never sees a yosys or slang
type. Roughly:

- **Scope tree** — instances, generate blocks, interfaces; parent/child links; each scope names its module definition; black-box flag + provenance.
- **Objects per scope** — ports (with direction, possibly *unknown*), nets/vars (width, packed/unpacked dims), parameters (**resolved values**, plus the default, so you can see the override), processes, sub-instances.
- **Connectivity** — per net *bit*, the set of drivers and the set of loads, each being (scope, cell/process, port, bit index).
- **Boundary links** — parent net bit ↔ child port bit, the edges that let you walk up and down.
- **Source locations** — `file:line:col` on everything that has one. Non-negotiable; the jump-to-editor is half the value of the tool.

## Crossing the boundary

The one genuinely novel navigation primitive, and the reason this beats grep:

1. Cursor on net `alu_result` in scope `tb.dut.core`.
2. `d` → drivers. One of them is the output port of instance `u_alu`.
3. `Enter` → TREN descends into `tb.dut.core.u_alu`, lands on the *internal* net bound to that port, and shows *its* drivers.
4. Repeat until you hit a flop, a constant, a primary input, or a black box.

Same in reverse for loads, and upward through a parent port. The tool keeps a
**trace stack** of where you have been — visible as a breadcrumb, poppable, and
dumpable to text so you can paste it into a bug report. That artefact —
*"here is the path from the X to the flop that caused it"* — is something the GUI
tools make you reconstruct by hand from screenshots.

Variants worth having: fan-in **logic cone** to depth N, **stopping at sequential
boundaries** (the actual unit of RTL reasoning), and "shortest path between these
two signals".

---

# Navigation model

Three panes (the screen that stuck after prototyping): **scope tree** |
**focused RTL source** | **module I/O**. `1`/`2`/`3` select the pane; `j`/`k`
move, `l`/`h` descend/ascend, `y` yank, `e` editor, `d`/`L` drivers/loads.
Sketch:

```
┌ tree ─────────┬ source (focused) ───────┬ module I/O ─┐
│ hier1_0       │ my_module #(            │ clk          │
│ `hier2_0      │  .some_param(32)        │ rst_n        │
│ ||`hier3_0    │ ) hier_1 (              │ some_input   │
│ ||`hier3_1    │  .clk(clk),             │ some_output  │
│ `hier2_1 <<   │  .rst_n(rst_n),         │              │
│               │  ...                    │              │
└───────────────┴─────────────────────────┴──────────────┘
```

- `1` / `2` / `3` — active pane; j/k act on it (tree: move/descend; source:
  scroll with the focus band; I/O: select ports).
- `l` / `h` — in / out of a scope (tree pane).
- `d` / `L` — fan-in / fan-out of the selected net (trace stack + breadcrumb).
- `z` — fuzzy-jump over every scope path (fzf-style).
- `/` — filter within the current scope; `g/` — global net search with globbing.
- `e` — open `$EDITOR` at the declaration's exact line.
- `y` — yank submenu, see below.
- `X` — cut this scope to a black box / restore it.

## Views that are not the tree

The tree is the spine; these are where it stops being a file explorer:

- **Module-centric** — "who instantiates `fifo`?", listing all N instances with their parameter overrides side by side. **Parameter diff across instances of the same module** is a recurring real-world bug hunt and nothing does it well.
- **Flat net search** across the whole design with globbing.
- **Clock/reset domain listing** — which scopes are on which clock, where the domains touch. Needs the signal-role reasoning; see *Open questions*.
- **Unconnected / dangling / tied-off report** — cheap to compute off the bit map, immediately useful.
- **Black-box inventory** — every unresolved module, why, and what would fix it.

---

# Yanking paths — the format problem

You find the signal, and then you need its path *somewhere else*: a Vivado Tcl
console, a VCS force, a Questa `add wave`, a UVM config set, a waveform viewer.
**Every one of those wants a different spelling of the same path**, and retyping
a 60-character hierarchical path by hand is exactly the kind of thing that makes
people give up and use the GUI.

`y` opens a submenu; the preview pane shows the rendered string live before you
commit. Formats are **data, not code** — a small declarative spec so users can
add their own without patching the tool:

```
separator        "." | "/" | ":"
leading_sep      bool          # Questa wants /tb/dut/...
include_top      bool          # Vivado netlist paths are relative to the top cell
array_notation   "[i]" | ".i" | "(i)" | "<i>"
bit_select       "[n]" | "(n)" | "<n>"
escape           none | tcl | shell | regex | glob
```

Built-in presets to start from (**every one of these needs checking against the
real tool** ⚠ — this is from memory and the details are precisely where it
matters):

| Target                                               | Shape                                     |
|------------------------------------------------------|-------------------------------------------|
| SV hierarchical (`$display`, force, `uvm_hdl_force`) | `tb.dut.core.u_alu.result`                |
| Vivado `get_cells` / `get_nets`                      | `dut/core/u_alu` — `/`, **top excluded**  |
| Vivado `get_pins`                                    | `dut/core/u_alu/result`                   |
| Questa / ModelSim                                    | `/tb/dut/core/u_alu/result` — leading `/` |
| VCS UCLI / Verdi                                     | `tb.dut.core.u_alu.result`                |
| VCD / GTKWave / Surfer / wavy                        | dot-joined scope path                     |
| yosys `select`                                       | `module/net`                              |
| VHDL external name                                   | `<< signal .tb.dut.sig : std_logic >>`    |

Details that look small and are not:

- **Tcl bracket escaping.** `genblk1[0]` in a Tcl command is command substitution. It has to come out as `genblk1\[0\]` or the paste silently does something insane. This alone justifies the feature.
- **Escaped SV identifiers** — backslash-prefixed, with a significant trailing space.
- **Generate/array index spelling** differs between tools even within one vendor.
- **Top inclusion** — Vivado netlist object paths are relative to the top cell, SV hierarchical names are not. Getting this wrong is a silent empty result.
- **Relative yank** — path relative to the current scope, or to a user-set anchor, not just from the root.

## Templates, not just paths

Natural extension once paths are a formatting problem: emit the whole *command*.

```
add wave -r /tb/dut/core/u_alu/*
force -freeze /tb/dut/rst_n 1 0
$display("%0t alu.result=%h", $time, tb.dut.core.u_alu.result);
set_property MARK_DEBUG true [get_nets dut/core/alu_result]
```

With multi-select (`<space>` to mark several signals), this becomes *"build me
the wave-dump script / the force sequence for these twelve signals"* — a
genuinely tedious task that the tool is perfectly positioned to do.

## Clipboard mechanics

**OSC 52.** The whole premise is that you are on a remote workstation over SSH;
if `y` only fills a local tmux buffer the feature half-works in the exact
scenario it was built for. OSC 52 escape sequence, with `wl-copy`/`xclip`
fallback locally. Worth getting right early — it is annoying to retrofit.

---

# Interop

- **Editor jump** — `$EDITOR` at `file:line`. Table stakes.
- **Waveform handoff** — send selected signals to a viewer's save-file, or read a VCD/FST and annotate the tree with values at a timestamp. That turns TREN from a browser into a debug tool. `~/proj/wavy` (vim-keys VCD viewer, already packaged with nix here) is the obvious first target; GTKWave and Surfer after.
- **Headless query mode** — `tren query drivers tb.dut.alu_result`, JSON out. Makes it scriptable, CI-usable, and trivially consumable by an LLM agent, which is arguably a first-class use case now.
- **Filelist export** — `tren export -f`, out of any importer, useful on its own.
- **Nix flake** from day one, given how these tool stacks are usually a mess.

# Implementation notes

- **Framework decision (2026-09-13): Python + textual (pinned 8.2.8) for the spike/MVP** — built-in Tree widget, native async workers and the Pilot test harness, all verified in the `tren_testing/tui_textual` demo; **ratatui (pinned 0.30.2) is the earmarked rewrite target**, to be decided on a real 100k-instance measurement. Implementation repo: `github.com/DiegoRosales/tren`; task breakdown on the Nextcloud Deck board `TREN`.
- **Elaboration is slow** — minutes on a real SoC. So: elaborate once, serialise the IR to an on-disk cache keyed by a hash of the manifest + source mtimes, and start instantly on subsequent runs.
- **Scale** — 100k+ instances means no materialising the full graph in memory. Lazy-load scope contents on descent; keep the bit-to-cell index on disk (sqlite is probably enough and gives you the queries for free).

---

# Phasing

**Spike (prove the idea is real).** Yosys JSON of a small design, unflattened.
Build the bit map, compute drivers/loads for one net, print the answer. No TUI.
If this is awkward, the whole concept is in trouble — do this first.

**MVP.** Three-pane screen (scope tree | focused source | module I/O) on
textual, yosys backend only, drivers/loads with hierarchy crossing, `e` to
editor, yank with 3–4 formats and OSC 52, black boxes as opaque nodes with
ports from stubs. Manifest exists as a hand-written file; **one** importer
(Verilator `obj_dir` or plain `.f` — whichever is least work) to prove the
shape.

**v1.** The importers that matter — Vivado `.xpr` + journal, Questa `work/`,
command-line scraping. Full yank format registry + templates + multi-select.
slang/Verilator AST overlay for source fidelity and real parameters. Fuzzy global
jump, module-centric view, the cache.

**Later.** Liberty arcs through black boxes, waveform annotation, trace-stack
export, domain views, headless query.

# Open questions

1. **Which backend actually survives contact with real code?** Resolved in outline 2026-09-13: all three elaborate a small parameterised design, and the pairing to build on is slang-hier + yosys JSON. Still open at scale: run them over `~/proj/zybo_sampler` RTL and check behaviour at 100k instances. Two Verilator limits surfaced meanwhile: genvar-indexed instance arrays in generate are rejected ("Size of range is '[0]'"), and warnings are fatal by default (`-Wno-*` needed).
2. **Where on the** `proc` **spectrum do we run yosys?** Resolved: `proc` is mandatory (the JSON backend rejects unprocessed designs) and sufficient; `opt` must not be run because it deletes unobservable cells and nets. Source correspondence survives at `proc`-only stage via cell/net `src` attributes.
3. **How badly does synthesis mangle names?** Confirmed 2026-09-13: `hierarchy` + `proc` mangle *module* names to `$paramod\name\param=value` (values recoverable by un-mangling), while instance names and net names survive; `opt` additionally drops unobservable nets/cells. `slang-hier` reports the clean names and resolved parameter values for the same design.
4. **How much of the yank format table is actually right?** It is from memory. Each preset needs one round-trip test against the real tool — paste it in, see if the object resolves. Consider shipping a `tren doctor` self-check.
5. **Do the vendor work dirs really give up their filelists?** Vivado `.xpr` and Verilator `obj_dir` I am confident about. Questa `_info` and the VCS/Xcelium artefacts are guesses. **Go look at a real one** before designing importers — there is a real chance the command-line-scraping approach is the only one that generalises, which would simplify the design a lot.
6. **Signal roles** — any way to know "this is a clock" without an SDC/XDC? May need to optionally read constraints. (Same class of problem as the signal-role work in IPYFuse.)
7. **SV scope.** Interfaces, modports, packages, classes. Interfaces in particular are *hierarchy-shaped* and need first-class treatment, not flattening.
8. **Stretch idea worth not forgetting: FUSE.** Expose the hierarchy as an actual mounted filesystem — then `cd tb/dut/core`, `ls`, `cat clk`, and yazi/ranger/fzf all work on RTL *for free*, with zero TUI code. Possibly the whole product; possibly a gimmick that falls apart on the net-is-not-a-file problem. Cheap to test.
9. **Real-scale economics.** The three tools were verified on a five-line-module design. Nothing yet measures elapsed time, memory, or output size on a real design — nor whether slang's `--ast-json-scope` and `slang-hier --inst-prefix` actually make lazy *partial* elaboration cheap enough for the "open anything" requirement.

# Prior art to check before building anything

Must not reinvent these. netlistsvg verified 2026-09-13 to consume yosys JSON
directly (`netlistsvg input.json`), confirming the JSON is a workable source of
truth; the rest are checked as needed:

- **netlistsvg** — yosys JSON → SVG schematic. Solves the picture problem, not the navigation problem.
- **Surfer** — modern waveform viewer, Rust; check whether its hierarchy browser already does part of this and whether the VCD/FST layer is reusable.
- **pyverilog's dataflow analyser** — closest existing thing to the tracing feature. Read it even if the Verilog-2001 limit rules it out.
- **Verilog-Perl vhier** — the old-school hierarchy dumper. Check whether the functionality is already there.
- **Edalize / FuseSoC** — already normalise "a design" across N backends. The manifest above may be reinventing `edam`; if so, *use it* rather than rebuild it.
- **Vivado's Tcl object model** (`get_pins` / `get_nets` / `get_cells`) — a proven query vocabulary for exactly these questions. Worth stealing the naming.

---

# Naming

**Chosen: TREN** — TUI RTL Explorer & Navigator. Spanish for *train*. Four
characters, pronounceable, a real word, and not (to my knowledge) claimed by
anything in the EDA or terminal-tooling space. **Not verified by search — do that
before registering anything.**

Sibling names available if components or subcommands ever need them, all Spanish
and all on-theme: **árbol** (tree) for the hierarchy view, **rama** (branch),
**hoja** (leaf) for leaf cells, **nido** (nest), **vía** or **ruta** for a trace
path, **estación** for a bookmark.

### Considered and rejected

- **cone** (*logic cone*) — the original working name, and the recursive expansion *CONE Only Navigates Elaborations* nicely encoded the "not a renderer" constraint. Too generic a word to search for.
- **hier** (*hierarchy*; also German for *here*) — good, but adjacent to Verilog-Perl's existing `vhier`, and opaque to anyone who doesn't already know the domain.
- **TREE** — *TUI RTL Elaboration Explorer* was the cleanest acronym of all, and the word is literally the data structure. Killed by `tree(1)`: same name, same genre, on every machine this would run on.
- **TIER**, **TRIE**, **SCOPE**, **PLUMB**, **FATHOM**, **NAV** (*NAV Ain't Vivado*), **DAVE** (*DAVE Ain't Verdi Either*) — all workable, all lost to TREN on brevity, pronounceability or collision risk.

---

## Follow-up

1. Toolchain installed and backend APIs verified (2026-09-13) on a small parameterised design — findings, recommendation and reproductions in `tren_testing/report.md`, `t1_yosys/`, `t2_verilator/`, `t3_slang/`. Remaining: run the chosen pair (slang-hier + yosys JSON) over real RTL (`zybo_sampler`) to check scale limits and the slicing flags.
2. Write the spike: yosys JSON → bit map → drivers of one net, keyed back to the slang-hier tree via `$paramod` un-mangling.
3. Go **look inside** a real Questa `work/`, a VCS build dir and a Vivado `.runs/` before designing importers (question 5). Cheap, and it may collapse the design.
4. Check whether `edam` / Edalize already is the manifest.
5. Survey the prior-art list; kill the project if Surfer or something else already does it.
6. Search for TREN collisions (crates.io, PyPI, GitHub, EDA tooling) before the name is load-bearing.
7. Decide the fate of `yosys-slang`: it is not packaged and is the only route to bit-level connectivity with first-class SystemVerilog. Revisit only if plain yosys's native SV frontend proves limiting on real RTL.
8. **Implementation has moved to its own repo** (`github.com/DiegoRosales/tren`; dev flake; textual 8.2). Tasks are broken down on the Nextcloud Deck board `TREN` (Spike / MVP / v1 / Later stacks; every card carries context + acceptance criteria for an agent or a human). Start with the Spike stack; the framework verdict and verified gotchas are in `tren_testing/report-tui.md`.