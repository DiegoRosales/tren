# Advanced usage

This page covers the intermediate steps: the IR cache, filelists, sources in
several directories, hand-written manifests, and the Vivado importer. Run the
commands from the repository root.

## Use the cache

`tren open` elaborates the design once and writes the IR to a cache. The
default directory is `.cache/tren`, from the `cache.dir` setting.

```sh
tren open docs/examples/one-file/counter.sv --top counter
tren open docs/examples/one-file/counter.sv --top counter
```

The first command imports and elaborates. The second command finds the cache
entry and opens at once. It does not run yosys or slang.

The cache key contains three inputs:

- the manifest,
- each source file's path, modification time, and size,
- the IR schema of the running TREN.

An edit to a source, a change to the manifest, or a new IR shape invalidates
the entry. A corrupt entry is ignored and rebuilt.

Select another cache directory with `--cache`:

```sh
tren open docs/examples/one-file/counter.sv --top counter --cache .cache/example-ir
```

Force a rebuild with `--refresh`:

```sh
tren open docs/examples/one-file/counter.sv --top counter --refresh
```

### Run the steps by hand

The one-command form is convenient. The separate commands show each step and
let a script stop between them.

```sh
mkdir -p .cache/one-file
tren import docs/examples/one-file/counter.sv --top counter \
    --out .cache/one-file/manifest.json
tren elaborate .cache/one-file/manifest.json \
    --cache .cache/one-file/ir
tren open .cache/one-file/manifest.json \
    --cache .cache/one-file/ir
```

`tren import` writes the manifest. `tren elaborate` builds the IR and prints
the summary. `tren open` reads the manifest and reuses the IR. The second
`tren elaborate` with the same inputs is a cache hit.

The manifest stores source paths relative to the directory where you ran
`tren import`. Keep the manifest and the sources in the same relative layout,
or pass the right `--root` to `tren elaborate` and `tren open`.

## Use a filelist

A `.f` filelist lists sources and options. The importer reads paths relative
to the filelist, the same way the `-F` form of the slang command file works.

`docs/examples/multi-dir/design.f`:

```text
// Paths are relative to this filelist. The header lives in include/.
+incdir+include
rtl/alu_top.sv
ip/alu.sv
```

Open it:

```sh
tren open docs/examples/multi-dir/design.f --top alu_top
```

The importer records three include directories:

- `docs/examples/multi-dir/include`, from `+incdir+`,
- `docs/examples/multi-dir/rtl`, from the top-level source,
- `docs/examples/multi-dir/ip`, from the ALU source.

The source directory floor matters. `slang` and Vivado resolve a relative
`` `include `` from the including file's directory. yosys also searches the
source directory and the current directory before its `-I` list. TREN adds
every source directory to the incdirs, so a header next to its source
resolves with no hand edit.

The filelist grammar supports these forms:

| Form | Effect |
|---|---|
| a path | A source file. |
| `+incdir+DIR` | An include directory. |
| `+incdir DIR`, `-I DIR`, `--include-directory DIR` | An include directory. |
| `+define+NAME`, `+define+NAME=VALUE` | A preprocessor define. |
| `-D NAME`, `-D NAME=VALUE`, `--define-macro NAME=VALUE` | A preprocessor define. |
| `// comment` | A line comment. |
| `"path with spaces.sv"` | A quoted path. |

The importer does not follow a nested `-f` filelist. It warns about an option
it does not carry into the manifest. A flag in the manifest reaches slang,
and slang stops there with its own message.

## Use several source directories

A filelist can name sources in any number of directories. This example uses
two:

```sh
tren open docs/examples/multi-dir/design.f --top alu_top
```

The resulting manifest contains these source entries, in filelist order:

```text
docs/examples/multi-dir/rtl/alu_top.sv
docs/examples/multi-dir/ip/alu.sv
```

To keep the imported manifest, add `--out`:

```sh
tren open docs/examples/multi-dir/design.f --top alu_top \
    --out .cache/multi-dir/manifest.json
```

Use `--root` when you run a command from another directory, or when the
manifest is stored apart from the sources:

```sh
tren elaborate .cache/multi-dir/manifest.json --root .
```

`--root` is the anchor for every relative path in the manifest. It is also
the working directory for yosys and slang.

## Write a manifest by hand

A manifest is JSON. This one describes the one-file example:

```json
{
  "name": "counter",
  "sources": ["docs/examples/one-file/counter.sv"],
  "incdirs": ["docs/examples/one-file"],
  "defines": [],
  "params": [],
  "top": "counter",
  "blackboxes": [],
  "slang_flags": [
    "--allow-use-before-declare",
    "--timescale=1ns/1ps",
    "-Wno-redefinition"
  ],
  "yosys_flags": []
}
```

Save it as `.cache/one-file/manifest.json` and open it:

```sh
tren open .cache/one-file/manifest.json --cache .cache/one-file/ir
```

A hand-written manifest needs no importer step. The elaborator adds the
source directories to the incdirs, so the list above may omit them. Keep
`slang_flags` when the design uses a construct that Vivado or yosys accepts
and slang rejects by default. Remove a flag when the design must stay
slang-clean.

## Use a Vivado project

The Vivado importer reads the `project.xpr` FileSets and the `TopModule`
option. It also scans the project directory for `*.v` and `*.sv` files,
because a batch or HLS project can carry its RTL outside the FileSet list.

```sh
tren open docs/examples/vivado/project.xpr
tren open docs/examples/vivado
```

Both forms select the Vivado importer. The second form uses the
`project.xpr` inside the directory. Neither form needs `--top`, because the
project records `alu_top` in its `TopModule`.

The importer understands the Vivado path variables:

| Variable | Directory under the project |
|---|---|
| `$PPRDIR` | `.` |
| `$PSRCDIR` | `project.srcs` |
| `$PGENDIR` | `project.gen` |
| `$PRUNDIR` | `project.runs` |
| `$PCACHEDIR` | `project.cache` |
| `$PIPUSERFILESDIR` | `project.ip_user_files` |

A `.vh` or `.svh` FileSet entry adds its directory to the incdirs. It is not a
source. A `.bd` entry is a block design, not Verilog, so the importer drops it
with a warning. Missing FileSet files also warn.

The directory scan skips `project.runs`, `project.cache`, `project.gen`,
`project.ip_user_files`, `.Xil`, and `.git`. It keeps one copy of each file
name, the shallowest one, so generated duplicates do not appear twice.

Pass `--top` when the project has no `TopModule` and no recorded top, or when
you want to override them:

```sh
tren open path/to/project.xpr --top my_top
```

## Import HLS output

A Vitis HLS export is a directory of generated Verilog. Import it as a normal
source directory:

```sh
tren import tests/hls_examples/hls/fft_wrapper/rtl_output \
    --top fft_wrapper --out .cache/hls/fft_wrapper.json
tren elaborate .cache/hls/fft_wrapper.json
```

The committed `rtl_output/` exports need no Xilinx install. The
[README HLS section](../README.md#reading-vitis-hls-rtl-output-no-xilinx-needed)
lists the `make hls-*` shortcuts and the full Vitis rebuild flow.

## Choose a clipboard provider

`yank.provider` selects how `y` writes the path:

- `auto` (default) uses tmux inside a tmux session, otherwise OSC 52;
- `tmux` uses `tmux load-buffer -w -`;
- `osc52` writes a raw OSC 52 sequence.

The tmux provider works with `set-clipboard external`, where tmux ignores
application OSC 52 writes. Use an explicit provider when the automatic
choice is wrong for the terminal:

```toml
[yank]
provider = "tmux"
```

## Set the configuration

TREN reads built-in defaults, a system file, a user file, and the nearest
project `.tren.toml`. A later layer replaces the same key.

```sh
tren config
tren config export
tren config export --out .tren.toml
```

A project file may set the cache directory, the theme, the line numbers, the
hierarchy colours, the yank format, and the clipboard provider. It may not
set a non-empty `editor.command`; keep that in the user or system file. The
[configuration page](configuration.md) lists every key.
