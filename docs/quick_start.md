# Quick start

TREN opens a design as three panes: the scope tree, the focused RTL source,
and the module I/O. This page opens three of the small designs under
`docs/examples/`. Run every command from the repository root.

## Requirements

TREN needs Python 3.11 or newer, `yosys` 0.70 and `slang` 12.0 on `PATH`.
The [README](../README.md) lists the supported install methods. Check that
the command is available:

```sh
tren --version
```

Run every command in this guide from the repository root.

## Open one source file

```sh
tren open docs/examples/one-file/counter.sv --top counter
```

TREN imports the target, elaborates it, prints a short summary, and opens the
TUI. The first run takes a few seconds. Later runs use the IR cache and start
at once.

The target is one SystemVerilog file. The top module is `counter`, so the
command passes `--top counter`. The importer records the file. The tree shows
`counter/` with the child scope `u_count/`. The source pane opens on the
`counter` module.

TREN uses `top` as the default top module name. Most designs name the root
module something else, so pass `--top` with the real name.

The file also defines `nibble_counter`. The instance `u_count` is the link
between the two modules.

The same form works for any source file:

```sh
tren open path/to/design.sv --top my_top
```

## Open several source files

```sh
tren open docs/examples/multi-file --top alu_top
```

A directory target is scanned for `*.sv` and `*.v` files. This example holds
`alu_top.sv` and `alu.sv`. The top module is `alu_top`, so the command passes
`--top alu_top`. The tree shows `alu_top/` with `u_alu/` below it.

You can also list the files directly with a repeated `-f`/`--file` option:

```sh
tren open -f docs/examples/multi-file/alu_top.sv \
          -f docs/examples/multi-file/alu.sv --top alu_top
```

`-f` replaces the directory target. The order is kept, so put a file that
defines a macro before the file that uses it. The importer stores each path
relative to the current directory and adds the source directories to the
include path.

## Open a Vivado project

```sh
tren open docs/examples/vivado/project.xpr
```

The `.xpr` suffix selects the Vivado importer. The importer reads the
`DesignSrcs` FileSet and the `TopModule` option. The example records `alu_top`
as the top module, so the command does not need `--top`. The project has two
sources under `project.srcs/sources_1/new/`.

A directory that contains `project.xpr` works too:

```sh
tren open docs/examples/vivado
```

The importer also scans the project directory for Verilog sources. This
covers batch and HLS projects whose RTL is outside the FileSet list. See
[Advanced usage](advanced_usage.md) for the details.

## Open an existing design

The target can be any of these forms:

```sh
tren open path/to/rtl --top my_top
tren open path/to/design.sv --top my_top
tren open -f path/to/a.sv -f path/to/b.sv --top my_top
tren open path/to/design.f --top my_top
tren open path/to/project.xpr
tren open path/to/manifest.json
```

For a source target, use `--out PATH` to keep the imported manifest. Without
`--out`, the importer keeps the manifest in memory. A manifest target is read
as it is; `--out`, `--name`, `--top`, and `--format` do not apply.

## Use the screen

The screen has three panes. The active pane is lighter than the other two.

| Key | Effect |
|---|---|
| `j` `k` | move down/up in the active pane |
| `l` `h` | expand/collapse the highlighted scope in the tree |
| `enter` | show the highlighted module in the source pane |
| `1` `2` `3` | select the tree, source, or I/O pane |
| `z` | zoom the active pane, or restore the three panes |
| `q` | quit |

The full key map, the trace view, the search, the yank formats, and the black
box cuts are in the [README user guide](../README.md#user-guide).

## Next steps

- [Command reference](command_reference.md) lists every command and option.
- [Advanced usage](advanced_usage.md) covers the cache, filelists, multiple
  source directories, and hand-written manifests.
- [Examples](examples/README.md) lists the example directories and their
  commands.
