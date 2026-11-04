# TREN examples

Each directory is a small, openable design. Run the commands from the
repository root.

| Example | Command | Shows |
|---|---|---|
| `one-file/` | `tren open docs/examples/one-file/counter.sv --top counter` | One source file with a top module and one child instance. |
| `multi-file/` | `tren open docs/examples/multi-file --top alu_top` | A directory scan of two source files. |
| `multi-dir/` | `tren open docs/examples/multi-dir/design.f --top alu_top` | A filelist with sources in two directories and a header in a third. |
| `define/` | `tren open docs/examples/define/design.f --top define_top` | A filelist with a macro define. |
| `generate/` | `tren open docs/examples/generate/design.f --top generate_top` | A filelist whose top-level parameter selects the generated lanes. |
| `parameter/` | `tren open docs/examples/parameter/design.f --top param_top` | A filelist with a top-level parameter override. |
| `vivado/` | `tren open docs/examples/vivado/project.xpr` | A Vivado project with a DesignSrcs FileSet and a TopModule. |

Every source example passes `--top` because the default top module name `top`
is rare. The Vivado project records `alu_top` in its `TopModule`, so the
importer reads it from the `.xpr`. The define example sets `TOPW=16` with a
`-D` line in its filelist. The generate example sets `LANES=4` with a `-G`
line, which generates four lanes and the wide branch. The parameter example
sets `WIDTH=16` with a `-G` line in its filelist.

See [Quick start](../quick_start.md) for a walkthrough and
[Advanced usage](../advanced_usage.md) for the cache, the multi-directory
filelist, the define example, the generate example, and the parameter
override.
