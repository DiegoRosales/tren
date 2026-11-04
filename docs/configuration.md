# Configuration

TREN reads optional TOML files and merges them. This page lists the layers,
every key, and the rule that protects the editor command. The
[README](../README.md#settings) gives the short version.

## Layers

TREN reads four layers and merges them in a fixed order. A key in a later layer
replaces the same key in an earlier layer:

| Layer | Path |
|---|---|
| built-in defaults | - |
| system | `/etc/tren/config.toml` |
| user | `$XDG_CONFIG_HOME/tren/config.toml`, or `~/.config/tren/config.toml` |
| project | `.tren.toml`, the nearest file from the current directory up to the repository root |

Discovery is deterministic. The user layer uses `XDG_CONFIG_HOME` only when it
holds an absolute path. Outside a git repository only the current directory is
searched for `.tren.toml`. Inside a repository the search stops at the root,
and the nearest `.tren.toml` wins. The project layer never stacks two files.

Every key is optional. An unknown key, a wrong type or an invalid value stops
the command and names the file and the key.

## Keys

| Key | Default | Effect |
|---|---|---|
| `cache.dir` | `.cache/tren` | IR cache directory. A relative path resolves from the current directory. |
| `editor.command` | empty | Editor command for `e` and `E`. `TREN_EDITOR` wins; otherwise this value, then `EDITOR`, then `VISUAL`. System and user layers only; a project file may set it empty. |
| `tui.theme` | `textual-dark` | Textual theme name for the TUI chrome. |
| `tui.line_numbers` | `true` | Show the line-number gutter at startup. `n` still toggles it. |
| `tui.hierarchy.enabled` | `true` | Colour hierarchy rows by kind. `false` gives a monochrome tree. |
| `tui.hierarchy.scope` | `theme` | Module-instance rows. |
| `tui.hierarchy.module` | `theme` | Module definitions and generate blocks. |
| `tui.hierarchy.blackbox` | `theme` | Black boxes and user cuts. |
| `tui.hierarchy.port` | `theme` | Input and output port leaves. The theme role follows the direction. |
| `tui.hierarchy.inout` | `theme` | Inout port leaves. |
| `tui.hierarchy.net` | `theme` | Net leaves. |
| `yank.format` | `sv` | Initial format for `y`, `Y` and the status path: `sv`, `vivado`, `yosys` or `vcd`. |
| `yank.provider` | `auto` | Clipboard provider for `y`: `auto`, `tmux` or `osc52`. `auto` uses tmux inside a tmux session, otherwise OSC 52. |

Each `tui.hierarchy` colour key is `theme` (the default), `none` (leave that
kind uncoloured), or a Textual colour name or hex value. `theme` takes the
role from the active Textual theme: `text-primary` for instances,
`foreground-lighten-1` for module definitions, `text-warning` for black boxes,
`text-secondary` for input ports, `text-error` for inout ports,
`text-success` for output ports and `foreground-darken-2` for nets. A theme
colour is lightened when needed to stay legible on the pane surfaces; an
explicit colour is used as written. While hierarchy colouring is enabled, the
I/O pane uses the same port colours.

A project file that changes the yank format, the clipboard provider, the line
numbers and the hierarchy colours:

```toml
[yank]
format = "yosys"
provider = "tmux"

[tui]
line_numbers = false

[tui.hierarchy]
net = "none"
port = "#a6c9ff"
inout = "#d17e92"
```

## Write a configuration file

`tren config` shows every effective value and the file that set it. `tren
config export` writes a complete TOML template at those values, so one file
works as a template and as a report:

```sh
tren config                              # effective values + origins
tren config export > .tren.toml          # full template at the current values
tren config export --out .tren.toml      # the same, written to a file
tren config export --out config-dir      # writes config-dir/config.toml
```

The `--debug-config` and `--debug-config-origins` flags print the values
without opening the TUI. The `TREN_SYSTEM_CONFIG`, `TREN_USER_CONFIG` and
`TREN_PROJECT_CONFIG` variables override the three file paths; tests and
unusual installs use them. The
[command reference](command_reference.md) lists both.

## Configuration safety

A project file is not trusted to choose a command. `editor.command` is
accepted only from the system and user layers; a `.tren.toml` that sets a
non-empty value is an error. This stops a cloned repository from choosing the
executable that `e` runs. The other settings only select paths, colours and
display options, so a project may set them.

`TREN_EDITOR`, `EDITOR`, and `VISUAL` are process environment variables. They
remain explicit user choices and are not part of the project layer.

An exported file used as a project `.tren.toml` must leave `editor.command`
empty. Keep a non-empty command in the user or system file.
