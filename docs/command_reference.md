# Command reference

This page lists the TREN commands, options, exit states, and environment
variables. The source is `src/tren/__main__.py`; when this page and the
program disagree, the program is correct.

## Command summary

| Command | Purpose |
|---|---|
| `tren open <target>` | Import the target unless it is a manifest, elaborate it, and open the TUI. |
| `tren import <target>` | Write a design manifest. Do not open the TUI. |
| `tren elaborate <manifest>` | Build the IR from a manifest and print a summary. Do not open the TUI. |
| `tren config` | Show the effective configuration. |
| `tren config export` | Write a complete TOML template. |

Run `tren --help` for the short usage text. Run `tren <command> --help` for
the options of one command. A target alone is not a command: `tren rtl` stops
with an error, and `tren open rtl` opens the design.

## Global options

Use these options as the first argument. They do not open the TUI.

| Option | Effect |
|---|---|
| `-h`, `--help` | Print the usage text and exit 0. |
| `-V`, `--version` | Print the version, the Python version, and the source SHA in a checkout. |
| `--version-debug` | Print the version line, then the interpreter, platform, package path, Git state, dependency versions, and configuration paths. |
| `--debug-config` | Print every effective configuration value as TOML and exit 0. |
| `--debug-config-origins` | Print only the values that a TOML file set, grouped by file, and exit 0. |

`--version-debug` does not read the configuration files. Use `--debug-config`
for the values. See [Configuration](configuration.md) for the configuration
keys.

## `tren open`

```text
tren open <target> [--root DIR] [--cache DIR] [--refresh]
                   [--out PATH] [--name NAME] [--top TOP]
                   [--format auto|f|vivado]
```

`<target>` is a manifest JSON, a `.f` filelist, a single `.sv` or `.v` file,
a source directory, or a Vivado `.xpr`.

| Option | Default | Effect |
|---|---|---|
| `--root DIR` | current directory | Anchor for stored relative paths and for the backend tools. |
| `--cache DIR` | `cache.dir` | IR cache directory. A relative path resolves from the current directory. |
| `--refresh` | off | Rebuild the IR even when the cache has an entry. |
| `--out PATH` | none | Write the imported manifest. An existing directory receives `PATH/manifest.json`. |
| `--name NAME` | target name, or the Vivado project name | Design name in the manifest. |
| `--top TOP` | `top`, or the Vivado `TopModule` | Top module. |
| `--format FORMAT` | `auto` | Importer: `auto`, `f`, or `vivado`. |

`--out`, `--name`, `--top`, and `--format` apply only to a source target. The
command stops with an error when the target is a manifest.

A source target is imported in memory. The manifest is written only when
`--out` is given. A manifest target is read as it is; use `tren elaborate`
when you want to check it without the TUI.

The default `top` is a fallback. Pass `--top` when the root module has
another name. A Vivado project may record the root in its `TopModule`
option; the importer uses that value when `--top` is absent.

## `tren import`

```text
tren import <target> [--out PATH] [--name NAME] [--top TOP]
                     [--format auto|f|vivado]
```

`tren import` writes a manifest and exits. It does not elaborate the design
or open the TUI.

| Option | Default | Effect |
|---|---|---|
| `--out PATH` | target directory | Manifest file path. A directory target writes `<target>/manifest.json`; a file target writes `<target parent>/manifest.json`. |
| `--name NAME` | target name, or the Vivado project name | Design name in the manifest. |
| `--top TOP` | `top`, or the Vivado `TopModule` | Top module. |
| `--format FORMAT` | `auto` | Importer: `auto`, `f`, or `vivado`. |

The importer stores source paths relative to the current directory. Run the
command from the project root, or move the manifest and sources together.
The command prints the source count, the top module, the incdirs, the defines,
and the slang flags. Importer warnings go to standard error.

The plain importer has these target rules:

- A `.f` filelist uses the filelist grammar and resolves its paths from the
  filelist directory.
- A single `.sv` or `.v` file becomes one source.
- A directory with one `.f` file uses that filelist. A directory with more
  than one `.f` file stops with an error.
- A directory without a `.f` file is scanned for `*.sv` and `*.v` files.
- A directory that contains `project.xpr`, or a `.xpr` file, selects the
  Vivado importer.

## `tren elaborate`

```text
tren elaborate <manifest> [--root DIR] [--cache DIR] [--refresh]
```

| Option | Default | Effect |
|---|---|---|
| `--root DIR` | current directory | Anchor for the manifest's relative source paths. |
| `--cache DIR` | `cache.dir` | IR cache directory. |
| `--refresh` | off | Rebuild the IR even when the cache has an entry. |

The command runs yosys, `slang-hier`, and `slang --ast-json`, then prints the
scope count, the connectivity count, and the boundary count. Use it to check
a manifest in a script. The [advanced usage](advanced_usage.md) page shows
the intermediate steps.

## `tren config`

```text
tren config [show]
tren config export [--out PATH]
```

`tren config` and `tren config show` print every effective value and the file
that set it. `tren config export` prints a complete TOML file at the current
values.

| Option | Default | Effect |
|---|---|---|
| `--out PATH` | standard output | `tren config export` writes the export to a file. An existing directory receives `PATH/config.toml`. |

The export is a template and a snapshot. A value that a file set keeps its
origin in `show`; the export writes only values. See
[Configuration](configuration.md) for the layers and the keys.

## Exit states

| State | Meaning |
|---|---|
| 0 | The command completed. |
| 1 | A backend tool failed. An uncaught importer error also exits 1. |
| 2 | TREN rejected a command, an argument, or a configuration value. |

`tren open` and `tren elaborate` print the failing tool's stderr and the full
command line. They do not print a Python traceback for a backend failure.

## Environment variables

| Variable | Effect |
|---|---|
| `TREN_EDITOR` | Editor command for `e` and `E`. It wins over the configuration file, `EDITOR`, and `VISUAL`. |
| `EDITOR` | Editor command when `TREN_EDITOR` is unset. |
| `VISUAL` | Editor command when `TREN_EDITOR` and `EDITOR` are unset. |
| `XDG_CONFIG_HOME` | Base for the user configuration file. A relative value is ignored. |
| `HOME` | Base for `~/.config/tren/config.toml` when `XDG_CONFIG_HOME` is unset. |
| `TREN_SYSTEM_CONFIG` | Override the system configuration path. Tests and unusual installs use it. |
| `TREN_USER_CONFIG` | Override the user configuration path. |
| `TREN_PROJECT_CONFIG` | Override the project configuration path. |

## Manifest fields

The manifest is a JSON file. Every path is relative to `--root`, or to the
current directory when `--root` is absent.

| Field | Type | Meaning |
|---|---|---|
| `name` | string | Design name. |
| `sources` | list of strings | Source files. |
| `incdirs` | list of strings | Include directories. |
| `defines` | list of pairs | Preprocessor defines as `[name, value]`. |
| `params` | list of pairs | Reserved parameter entries. The normal flow leaves this empty. |
| `top` | string | Top module. |
| `blackboxes` | list of strings | Reserved black-box entries. The normal flow leaves this empty. |
| `slang_flags` | list of strings | Extra arguments for `slang-hier` and `slang --ast-json`. |
| `yosys_flags` | list of strings | Extra arguments for yosys `read_verilog`. |

Every importer writes the slang tolerances that Vivado and yosys accept. The
[README manifest section](../README.md#manifest-schema-and-backend-tolerance-flags)
explains each flag. Remove a flag when the design must stay slang-clean.
