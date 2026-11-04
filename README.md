# TREN - TUI RTL Explorer & Navigator

[![CI](https://github.com/DiegoRosales/tren/actions/workflows/ci.yml/badge.svg)](https://github.com/DiegoRosales/tren/actions/workflows/ci.yml)

## Introduction

TREN is a TUI to explore RTL designs in an easy way. It allows you to explore
the RTL like you do in simulator frontends like DVE/Verdi, GTKWave, etc.
As you navigate the RTL hierarchy, the RTL view follows you. There you can
do useful things such as tracing drivers/loads, copying the current hierarchy
path in a few different formats (sv, Vivado, yosys) and fuzzy find RTL elements

Now there's just one thing which is quite annoying. 
Most projects have their code spread across different directories or even different repositories.
Many projects employ a collection of scripts to generate filelists to use for synthesis and simulation.
Why not leverage that?
The goal of this project is ease of use. The idea is that you simply point it
at a codebase and with little to no interaction it pulls the filelist, determines
the RTL-top and elaborates the design. It also doesn't throw a fuzz if it cannot resolve a module
Right now the project supports only a few of ways to automatically gather a filelist,
but the idea is to expand it to support as many presets as possible.

How can you open a desgin?

 - You can point it at a single file (if multiple modules are declared in the file, then provide the RTL top)
 - You can point it at a directory and give it an RTL top
 - You can provide a filelist with the RTL top
 - You can point it to a Vivado .xpr file and it will auto-derive the filelist and RTL top

To be implemented

 - Lazy loading (interactively find and add the files)
 - Custom pre-processor script that generates a filelist
 - Support to gather filelists from prebuilt workspaces from other EDA tools: vcs, questa, quartus, etc.

Now, how does it actually elaborate the design? This project uses Yosys and slang as backend
to elaborate the design and to correlate the elaboration with the RTL code

Status: **spike phase, built on textual 8.2**. The backend pair is verified
and pinned (see *Sources of truth*): yosys `write_json` for bit-level
connectivity, `slang-hier` / `slang --ast-json` for the scope tree and
resolved parameters. Rust + ratatui is the earmarked rewrite target,
revisited when a real 100k-instance IR shows the ceiling matters.

## Documentation

- [Quick start](docs/quick_start.md) - open the example designs and learn
  the screen.
- [Command reference](docs/command_reference.md) - every command, option,
  exit state, and manifest field.
- [Advanced usage](docs/advanced_usage.md) - the cache, filelists, sources
  in several directories, hand-written manifests, and the Vivado and HLS
  imports.
- [Configuration](docs/configuration.md) - the config layers, every TOML key,
  and the project-file safety rule.
- [User guide](#user-guide) below - the screen, keys, search, tracing, and
  black boxes.

## Quick start with Nix

```sh
## Using nix run
nix run github:DiegoRosales/tren -- <args>

## For example:
nix run github:DiegoRosales/tren -- --version

####
## Using nix shell
nix shell github:DiegoRosales/tren
tren --version
```

General usage

```sh
tren tests/rtl                         # import + elaborate + open the TUI (one command)
tren open tests/rtl                    # the same, with the explicit verb
tren tests/rtl/demo.manifest.json      # the same, from an existing manifest
tren import tests/rtl                  # -> tests/rtl/manifest.json (first importer: .f filelist / dir scan)
tren import tests/rtl/some.xpr         # Vivado project.xpr (auto-detected)
tren elaborate tests/rtl/manifest.json # elaborate a manifest (yosys + slang-hier + slang -> IR)
tren config                            # show the effective settings and their origins
```

## Installation using pip

WARNING: TREN is not on PyPI yet. Point pip at the current release tag:

```sh
pip install "tren @ git+https://github.com/DiegoRosales/tren@v0.0.1"
```

The tag installs the same code every time. A later release changes the tag in
this command and in the source install below.

TREN needs Python 3.11 or newer and two external tools on `PATH`:

| Tool | Version | Source |
|---|---|---|
| `yosys` | 0.70 | <https://github.com/YosysHQ/yosys> |
| `slang` | 12.0 | <https://github.com/MikePopoloski/slang> |

Install each tool with your distribution package manager, or build it from
source at the linked repository. pip installs the Python dependencies
(`textual` and `rich`). Then start the TUI:

```sh
tren rtl
```

## Installation from source

Clone the repository and install the checkout in editable mode. The two
backends must be on `PATH`; the [pip installation](#installation-using-pip)
lists them with source links.

```sh
git clone https://github.com/DiegoRosales/tren
cd tren
git checkout v0.0.1
python -m venv .venv
. .venv/bin/activate
pip install -e .
tren --version
```

The checkout is detached at the release tag. The editable install reads
`src/` directly, so an edit is live on the next command. Contributors work
from the repository default branch instead of the release tag.

## User guide

### Starting the TUI

Open a design by giving TREN its source directory, filelist, RTL file, Vivado
project, or manifest. For example:

```sh
tren open path/to/rtl --top my_top
```

You can leave out `open` and run `tren path/to/rtl --top my_top`. To try the
included demo without installing yosys or slang, run `tren-tui`. See the
[command reference](docs/command_reference.md) for all target types and
options.

The screen is three panes - **tree** (the scope hierarchy, like a filesystem)
| **source** (the focused module's RTL) | **I/O** (parameters + ports). `q`
quits.

Across the top, a yazi-style **status line** shows the full hierarchical path
of the current selection in the current yank format. In `sv` and `vcd` a scope
row reads `top.u_core.g[0].u_alu_g`; `yosys` reads
`top/u_core/g[0]/u_alu_g`; `vivado` drops the top and escapes brackets
(`u_core/g\[0\]/u_alu_g`). A port/net leaf appends the item. The line updates
live on `j`/`k`/`l`/`h`/`ctrl+u`/`enter` and on `Y`, the final segment
highlighted. A yank appends `[yanked]` for a moment; the marker clears on its
own or when the selection moves.

The pane `1`/`2`/`3` selected - the one `j`/`k`/`y` act on - is lifted out of
the background; the other two stay dark. Exactly one pane is ever lit.

The first run elaborates (yosys + slang) and caches the IR (`.cache/tren/` by
default); later runs start instantly. The key covers the manifest, the
sources' mtimes and the shape of the IR, so an RTL edit or an IR change
re-elaborates once. The [advanced usage](docs/advanced_usage.md#use-the-cache)
page lists the inputs and the flags.

### Settings

TREN reads optional TOML files - a system file, a user file and the nearest
project `.tren.toml` - and merges them in that order. The keys cover the cache
directory, the editor command, the theme, the line numbers, the hierarchy
colours, and the yank format and clipboard provider.

`tren config` shows every effective value and the file that set it. The
[configuration](docs/configuration.md) page lists the layers, every key and
the rule that stops a project file from choosing the editor.

### The tree pane

The tree is rooted at the design's top module. The root row reads `top/`,
expanded at startup, and the cursor starts on it - so the source pane opens
on the top module with no keypress. A source set with extra root modules no
single top reaches keeps the top as the root row; those modules sit under one
unselectable `other roots/` row below it.

Every scope reads the same way: its child scopes first, then its ports, then
its nets, so the hierarchy stays the quickest thing to walk into:

```
u_alu0/
  port in    logic [31:0] a
  port in    logic [3:0] op
  port in    logic clk
  port out   logic [31:0] result
  logic [31:0] x
  wire we
```

A port leaf reads `port <direction> <type> <name>`; the type carries the
declared range, so the name needs no suffix. The direction sits in a fixed
column, so `in`, `out` and `inout` all start the type at the same offset. A
net leaf reads as its declaration, `<type> <name>`: `logic [31:0] x` for a
variable and `wire we` for a net. The type is the keyword the source writes,
from the slang overlay.

The netlist lists a port a second time as a net, because yosys names every
port net. TREN shows such a signal once, as the port row: the direction is
the fact a net row lacks. Tracing and source jumps still use that net.

A signal the overlay never reached (a module outside the slang pass) has no
type. A port row then reads `port <direction> <name>[msb:lsb]` and a net row
reads `net <name>[msb:lsb]`, with the range derived from the yosys bit
count.

Otherwise the range is the one the source declares: `[7:1]` and `[0:7]` read
as they are written, and a parametrised bound shows the value this instance
resolves.

The tree paints one colour per row kind: module instances, module definitions
and generate blocks, black boxes and user cuts, input ports, inout ports,
output ports and nets. By default every colour follows the active
`tui.theme`. The `!` marker, the cut marker, the direction word and the type
text stay, so colour is never the only cue. Set
`tui.hierarchy.enabled = false` for a monochrome tree, or override one kind
with a colour name or hex value. The settings table above lists the keys.

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

### Source line numbers

The source pane prefixes every RTL line with its file line number, so a
selection maps directly to the line the editor opens with `e`. The numbers
stay absolute while the view scrolls, and the column is wide enough for the
last line of the file. `n` hides the gutter and `n` again restores it. The
file header and the scroll markers never carry a number.

### Zooming a pane

`z` zooms the active pane: it takes the full width and the other two panes go
away. `z` again restores the three columns at their usual widths. While the
zoom is on, `1`/`2`/`3` move it - the pane you select becomes the zoomed one,
so exactly one pane is ever on screen. Every other key keeps its effect: `j`
still walks the tree, scrolls the source or selects a port, by the active
pane. While the trace split is open, `z` zooms the centre column to the full
width: the source keeps the top half and the trace pane keeps the bottom.

### Keys

All bindings work from any pane unless noted; `x` below means the active
pane.

<!-- keys:begin -->

| Key | Effect |
|---|---|
| `1` `2` `3` | select the tree / source / I/O pane |
| `z` | zoom the active pane to the full width, or restore the three columns |
| `n` | toggle line numbers in the RTL view |
| `j` `k` | move down/up: in the tree, through the visible nodes; in source, scroll; in I/O, select a port |
| `pageup` `pagedown` | move a screen up/down in the active pane: in the tree, jump to the next/previous page of nodes; in source, scroll a screen; in I/O, select a port a page away |
| `l` | tree: expand the highlighted scope (reveal its children) |
| `h` | tree: collapse the highlighted scope (hide its children) |
| `arrows` | aliases: down/up = j/k, right/left = l/h |
| `enter` | show the selection in the centre RTL pane with context - tree, the highlighted module; I/O, the selected port's declaration |
| `ctrl+u` | tree: step up one level, current scope first - a port/net leaf jumps to its own module, a module row to its parent |
| `/` | open the global search: fuzzy find over modules, instances, ports, nets and files (type to filter, enter to commit, n/N next/prev match, tab switches results/matched-lines pane, ctrl+enter to jump) |
| `y` | yank the selection through the current path format and clipboard provider |
| `Y` | cycle the yank format (sv -> vivado -> yosys -> vcd) and the status path preview |
| `d` `L` | trace drivers / loads of the selected signal (opens the trace split; in the trace pane, switch direction) |
| `X` | cut/restore the highlighted scope (treat it as a black box) |
| `;` | dump the current trace path as text |
| `e` | open $EDITOR at the selection's declaration - a scope's instantiation in its parent, a port/net's own declaration |
| `E` | open $EDITOR at the module definition (the module's declaration line); on a top module both e and E land there |
| `?` | open this navigation cheatsheet |
| `q` | quit |

In the **trace split**: `enter` or `l` expands the row: into the far net, or a cell into its pins, `backspace` or `h` collapses one trace level (at the root, closes the split), `j` `k` move down/up through the trace tree, `d` `L` switch the trace between drivers and loads, `escape` closes the split. The arrows alias j/k and l/h. The split is a bordered pane below the source view. The pane title names the net whose rows hold the cursor, with the visible row range.

<!-- keys:end -->

### Navigating

The tree works like a file manager: `j`/`k` move between visible rows
**without unfolding** anything, and `l`/`h` reveal or hide a scope's children.
`ctrl+u` steps up one granularity level, current scope first: from a port/net
leaf it jumps to that module's row (not past it to the parent); pressed again
it moves up to the parent module. With the I/O pane active, `enter` re-centres
the selected port's declaration instead.

The center pane shows the **real RTL file** at the module's *instantiation in
its parent* (or its declaration at the top), with the focused range
highlighted and kept in view - `e` opens the editor at exactly that line, `E`
at the module's own definition.

Selecting a **port** or **net** leaf focuses that signal's own *declaration*
line rather than the enclosing module - the `a` port leaf under `top/u_core`
lands on `input logic [W-1:0] a,`, the `x` net leaf on
`logic [W-1:0] x, y, rd, r0;`. A leaf
whose backend carried no `src` falls back to the module-level focus, and a
`src` naming a file that is not on disk (fixtures, moved sources) falls back
to the inline RTL snippet. The focus band follows the selection; scrolling
with `j`/`k` inside one scope is left alone, and only a new focus re-centres
the pane.

### Searching

`/` opens a fuzzy finder over **everything**: modules (every scope path),
instances, ports, nets and files (the source files the scopes' `src` spans
name). Type a substring of `alu_result` and the matching rows across every
scope narrow to it - fuzzy, so `alr` finds it too. The results list shows the
kind first: `port top.u_core.alu_result`, `net top.u_core.x`, `module core`,
`instance top.u_core.u_sram -> core`. A port the netlist also lists gives one
result, the port. Up/down (the j/k aliases) move the cursor, and the right
column previews the source at that result's declaration. The query box holds
focus, so every letter types - the letters `j`/`k` move the list only when
you let the arrows do it for you.

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

`y` writes the path of the current selection to the clipboard. The
`yank.provider` setting selects the route: `auto` uses tmux inside a tmux
session, otherwise OSC 52; `tmux` and `osc52` force one. The tmux
`set-clipboard external` case is in
[advanced usage](docs/advanced_usage.md#choose-a-clipboard-provider).

The status line uses the same format, so `Y` previews the path before `y`
writes it. It appends `[yanked]` after a successful write, then clears the
marker. The formats are declarative (see `tren/formats.py`), one per target
tool:

| Format | Selection `top.u_core.g[0].u_alu_g.result` |
|---|---|
| `sv` | `top.u_core.g[0].u_alu_g.result` |
| `vivado` | `u_core/g\[0\]/u_alu_g/result` - top excluded, `/`, Tcl-escaped brackets |
| `yosys` | `top/u_core/g[0]/u_alu_g/result` |
| `vcd` | `top.u_core.g[0].u_alu_g.result` |

### Tracing drivers/loads

Select a port or a net in the tree, then `d` (drivers) or `L` (loads). The
split pane shows the touching cells as an assignment tree. `enter` on a
boundary row expands it in place: the far net's rows nest under the row and
the rows above stay visible. Bits are matched **positionally** across the
boundary - matching by ID is wrong by design.

A cell row expands too. It shows the pins on its other side: the inputs of a
driver, the outputs of a load. Each pin names the net it connects to
(`A <- r0[31:0]`, `Y -> alu_result[31:0]`), and that net expands to the cells
driving or loading it. A yosys intermediate wire with no source-level name
shows the pin alone (`A`): its rows are still the logic behind it. This is
how the tree walks a logic cone to the ports and registers that feed it.

A cell row leads with the net it sits on, then the yosys cell and pin:
`alu_result  $xor.Y  demo.sv:64`. The net is the source variable the user
knows; the yosys type stays after it as the cell kind. An anonymous yosys
intermediate has no name of its own, so its rows inherit the nearest named
net above: a mux chain under a register reads as that register.

The RTL view follows the highlighted trace line. A cell row highlights the
statement that implements it, so the source leaves the last port and shows
the logic as the cone goes deeper. A pin row highlights the net declaration
it names, and a cell copied from several source locations shows the first
(the expression that became the cell).

A cell whose module the source set does not define - an IP with no stub, so
yosys has no port directions for it - has no known direction. Its rows appear
in both drivers and loads, at the instantiation site and marked
`(direction unknown)`. TREN never guesses the direction: a wrong guess drops
a driver from every trace through that instance.

A row with a `+` marker hides lines below it; `-` shows them. A line with no
marker has nothing below it - a black box, a cut, an unknown-direction
instance, or a cell whose other pins carry only constants. `j`/`k` walk every
visible line of the tree, not only the current level. `h` closes one level.

A port also has an edge on the outside. The drivers of an input and the loads
of an output cross to the parent net that connects the port: `o[31:0] ↑ top`.
Two net names that carry the same bits alias one wire and show the same
select, and a net the netlist aliased to a port reaches the same parent edge.
A cell pin that reaches an alias shows the local name and notes the other,
as `B <- x[31:0] (alias a[31:0])`. The design top has no parent, so its ports
report that the driver or the load is outside the design.

While the trace pane holds the cursor, `d` and `L` switch the direction of
the whole tree. From another pane they start a new trace at the tree
selection. `;` dumps the path of the net under the cursor, e.g.
`top.out ▶ top.u_core.alu_result`.

### Black boxes and cuts

Body-less modules (`sram_stub`-style) render as opaque scopes with their
ports and a `!` marker. `X` adds an explicit cut to any scope - the trace
stops at it with the reason shown - and `X` again restores it.

## Development

### Activating the dev shell

Work from the project root and enter the default shell once:

```sh
cd /path/to/tren
nix develop
exit                                         # leave the shell
```

The shell pins Python 3.14 + textual 8.2.8, yosys, slang, verilator, rust,
make and tmux. `tren` and `tren-tui` read the checkout's `src/` through
`PYTHONPATH`, so an edit is live on the next command. Every gate in
`docs/verification.md` assumes this shell.

Run the checks and tools inside the shell:

```sh
make check                                   # unit + both drives
make test                                    # unit suite
make drive                                   # headless Pilot tests (CI target)
make drive-real                              # Pilot drives on the demo manifest
make roundtrip                               # real-terminal OSC 52 + $EDITOR round trip (tmux, private server)
make pane-cue                                # real-terminal pane cue + status formats + yank marker + tmux provider
make profile                                 # lazy-load + cache profile (1 100-scope synthetic IR)
make bom                                     # regenerate docs/sw-bom.md
make run                                     # start the TUI on the vendored demo
PYTHONPATH=src python -m tren.spike          # spike: drivers/loads of one net
nix develop .#xilinx                         # FHS sandbox for Xilinx tools (interactive; then: make fft_wrapper_HLS)
```

### Dev shells

Five shells, all carrying the same backends (yosys, slang, verilator, rust,
make, tmux) so they cannot drift apart on tool versions. They differ in how
`tren` itself reaches the interpreter, and in which yosys build they carry:

| Shell | tren comes from | yosys build | Use it for |
|---|---|---|---|
| `nix develop` | the checkout's `src/`, via `PYTHONPATH` | nixpkgs (0.70) | day-to-day work. Edits are live on the next command; every gate in `docs/verification.md` assumes this shell |
| `nix develop .#tren` | the nix store (`buildPythonPackage`) | nixpkgs (0.70) | checking the *package*: `import tren` and the `tren` / `tren-tui` console scripts resolve into the store, from any directory |
| `nix develop .#tren-venv` | `.venv/`, `pip install -e .` | nixpkgs (0.70) | the textbook Python flow on nix's interpreter - an editable install, so edits are live too |
| `nix develop .#tren-yosys-src` | the checkout's `src/` | yosys git (`github:YosysHQ/yosys`, flake pin) | verifying against yosys main. The first entry builds the whole tool from source |
| `nix develop .#xilinx` | the checkout's `src/` | nixpkgs (0.70) | the FHS sandbox for Vivado / Vitis HLS (x86_64-linux only) |

Every `make` target runs in the shell you are already in, and calls `tren` from
`PATH`. Every shell above puts `tren` and `tren-tui` there (verified in all but
`.#xilinx`, which cannot run `-c` commands; WARNING: it lists the same
wrapper). Enter a shell once (see above), then run the targets directly.

The targets spawn no shell of their own, so a target costs one command, not a
nested `nix develop`. From a host shell, set `NIX_DEV=1` to wrap each command
in `nix develop $(TREN_SHELL) -c ...`: `NIX_DEV=1 make check`. `TREN_SHELL`
(default `.`) picks the shell the wrapper enters. Point the gates at the source
build with `NIX_DEV=1 make check TREN_SHELL=.#tren-yosys-src`.

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
carries the macro-argument token-pasting fix that nixpkgs 0.69 lacked. The
default shell (nixpkgs 0.70) elaborates the zybo_sampler design, so this
shell now serves as the check against yosys main, not as the only way past
that bug. The `slang_flags` / `yosys_flags` manifest fields work identically
in both shells.

Dependencies are declared once, in `pyproject.toml`, and read by both the nix
build (through `pyproject.nix`) and pip.

The spike (`python -m tren.spike tests/rtl/demo.sv`) runs yosys, builds the bit map
and prints drivers/loads of `top.out`, descending across the core boundary
down to the `$xor` that computes it - every row carries its `src` attribute.
Use `--keep` to keep the generated netlist under `.cache/tren/` for
inspection. Note: `assign o = {hi, lo}` produces **no cell** in yosys 0.70 -
the concatenation collapses to pure net aliasing, so `o` shows no driver
(its bit list literally overlays `hi` and `lo`); the loads are all there.

### Headless / CI

`make check` runs the unit suite plus both Pilot drives - the same gates the
CI workflow runs. `make roundtrip` verifies the OSC 52 + editor path and
`make pane-cue` the rendered active-pane highlight, status line formats, yank
marker and tmux clipboard provider, both against a real terminal on a private
tmux server (Pilot asserts style *values*, not what the terminal paints). See
`docs/verification.md` for what each layer catches.

### Reading Vitis HLS RTL output (no Xilinx needed)

The committed `tests/hls_examples/hls/<block>/rtl_output` exports are read
directly: `make hls-import`, `make hls-elaborate` and `make hls-run` import /
elaborate / browse one block (`BLOCK` defaults to `fft_wrapper`). Each
`rtl_output/` is a snapshot of the exported `impl/verilog` (RTL +
`component.xml`), so it needs no Xilinx install. The elaborator handles the
generated Verilog-2005 including yosys's constant / x bits. Example:
`fft_wrapper` bundles 140 scopes, ~72k bit-links and 1763 boundaries.

`tests/hls_examples/` mirrors ipyfuse's structure: `cpp/` (the C/C++ sources),
`dat/` (golden/input data), and `hls/<block>/{config.tcl,directives.tcl,
rtl_output/}` plus a shared `hls/run_hls.tcl`. Regenerating an example needs a
Vitis/HLS install. Xilinx binaries expect a conventional `/usr/lib` layout
that NixOS lacks, so the flake ships a `#xilinx` FHS sandbox (ported from
ipyfuse) for x86_64-linux. The env's runScript shell is interactive-only
(`nix develop -c` does not reach it), so enter it first, then run the Makefile:

    nix develop .#xilinx                          # interactive FHS shell (first build is slow)
    make setup_xilinx     # XILINX_VITIS=/path/to/Vitis ./settings64.sh path -> .xilinx_env
    make fft_wrapper_HLS  # vitis_hls -f run_hls.tcl fft_wrapper; regenerates rtl_output/
    make hls_examples     # rebuild every block
    exit

`.xilinx_env` and the per-block `solution_1/` synthesis workspace are
gitignored; `rtl_output/` snapshots are committed.

### Manifest schema and backend tolerance flags

`tren import` emits a manifest; the [command
reference](docs/command_reference.md#manifest-fields) lists its fields. Two
optional list fields carry backend tolerance flags - real third-party RTL
needs them, and they travel with the design, not the tool:

| Field | Reaches | Value an import writes |
|---|---|---|
| `slang_flags` | `slang-hier --params` and `slang --ast-json` | `["--allow-use-before-declare", "--timescale=1ns/1ps", "-Wno-redefinition"]` |
| `yosys_flags` | `read_verilog` before the sources | `[]` |

Every importer writes the `slang_flags` row, so an imported design opens with
no hand edit. The flags are slang tolerances for constructs that Vivado and
yosys accept and slang rejects by default:

- `--allow-use-before-declare` - an implicit net used before the named port
  connection that makes it,
- `--timescale` - files with a `` `timescale `` next to files without one,
- `-Wno-redefinition` - an ANSI port declared again as a body wire.

The manifest is editable: remove a flag where the design must stay
slang-clean.

Always on, no manifest needed: slang gets `--ignore-unknown-modules` (an
unresolved module is a normal black box in TREN) and yosys gets `-nolatches`
(TREN wants the net graph, not a synthesis result). The zybo_sampler design
(39 scopes, 20984 bit-links) is the reference case: `tren open` on its `.xpr`
elaborates with no hand-written manifest.

### Importers

Manifest producers - bootstrap, not a dependency (`tren import`). The importer
set is a plain filelist (`.f`, or a bare source directory) and Vivado
(`project.xpr`, auto-detected). The filelist grammar, the Vivado variables and
the directory scan are in [advanced usage](docs/advanced_usage.md#use-a-filelist).

Verified against a Vitis HLS 2025.2 export: yosys `read_verilog` parses the
generated Verilog-2005 cleanly; `slang-hier` additionally needs Vivado's
`vivado_interfaces.svh` interface library for the block-level `bd_0_sv.sv`,
which the workspace does not ship - pass `--top fft_wrapper` (or add the lib
dir) to elaborate the HLS core.

**incdirs default to the source directories.** Every importer records the
directory holding each source as an incdir, so a `` `include `` next to its
source, or in a sibling source directory, resolves with no manual entry.
Explicit `+incdir+` filelist entries and Vivado header-typed FileSet entries
(`.vh`/`.svh`) add their dirs too - the default is a floor, not a replacement.
`tren elaborate` applies the same source-dir floor to any manifest, so a
manifest that only hand-writes sources needs no manual incdir either.

### Layout

```
src/tren/            package
  ir.py              widget-agnostic IR (scopes, nets, params, bit map)
  elaborate.py       yosys + slang-hier/slang --ast-json -> IR (+ cache)
  importer.py        manifest producers (.f filelist; dir/single file; Vivado via vivado.py)
  vivado.py          Vivado project.xpr parser (FileSets, TopModule, $PSRCDIR vars)
  bringup.py         one-command target -> manifest -> IR -> TUI
  config.py          layered configuration files + the `tren config` command
  formats.py         yank format registry + templates
  tui.py             the textual app (implements the screen + keys only)
  __main__.py        subcommand front door (import / elaborate / open / config / bare target)
tests/               unit tests, the Pilot workdir, and the executable
                     fixtures: rtl/ (demo.sv for the drives and ranges.sv for
                     declared ranges), fixtures/ (the demo-tree.json fixture
                     IR) and hls_examples/ (Vitis HLS flows and their
                     committed rtl_output/ exports)
manifest.json        hand-checkable design manifest (sources/incdirs/top/...)
docs/                verification.md, release.md, quick_start.md,
                     command_reference.md, advanced_usage.md,
                     configuration.md, examples/
                     + notes/ mirrors; tech-concepts.md and sw-bom.md
tools/               osc52_roundtrip.sh, pane_cue_capture.sh, profile_1k_scopes.py,
                     bom.py (SW BOM generator)
pyproject.toml       PEP 621 metadata + deps - read by both nix and pip
flake.nix            dev shells (default / #tren / #tren-venv / #tren-yosys-src / #xilinx)
```

### Decisions already made (verified - do not re-derive)

- **Backends**: yosys `write_json` after `proc` (never `opt`; bit IDs are
  positional-only across scopes); `slang-hier --params` for scope tree +
  resolved parameter values. Do not `flatten`.
- **UI**: textual 8.2 for the spike/MVP; ratatui 0.30 pinned as the rewrite
  target. The IR and navigation logic are kept framework-agnostic.
- **Screen**: tree | source (focused on the module's instantiation in its
  parent) | I/O; keys as above; clipboard provider yank (tmux or OSC 52).
- **Scale**: lazy scope population on expand; never materialize the whole
  graph; IR cache keyed by manifest + source mtimes **and the IR's own shape**
  (`ir.schema_id()`), so a tren whose IR has changed rebuilds instead of
  serving a blob that predates the new fields.

### Sources of truth

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
- Pinned versions: Python 3.14, textual 8.2.8, rust 1.99, ratatui 0.30.2,
  yosys 0.70, sv-lang 12.0.
- How TREN is verified: `docs/verification.md` - the layered strategy
  (unit -> elaboration acceptance -> Pilot drives -> real-terminal round trip ->
  scale/cache profile -> real-artefact importers) and the change checklist.
- Component map: `docs/tech-concepts.md` (technical concepts -> the component
  that uses each) and `docs/sw-bom.md` (the auto-generated software BOM).
