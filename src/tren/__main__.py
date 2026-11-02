"""``python -m tren`` - the subcommand front door.

Commands:

    import <target> [--out PATH] [--name NAME] [--top TOP]
                emit a design manifest for a .f filelist, a .sv file, or a
                directory of sources (tren.importer)
    elaborate <manifest> [--root DIR] [--cache DIR] [--refresh]
                elaborate a manifest into the TREN IR (tren.elaborate)
    open <target> [--root DIR] [--cache DIR] [--refresh] [--out PATH]
                import the target unless it is a manifest, elaborate it and
                open the TUI (tren.bringup)
    config [show|export] [--out PATH]
                show the effective settings with their origins, or export a
                complete TOML template (tren.config)

Debug flags:

    --debug-config
                print the effective configuration as TOML and exit
    --debug-config-origins
                print the values a TOML file set, grouped by file path, and exit

A bare ``tren <target>`` (no subcommand) is the same as ``tren open <target>``.
"""

from __future__ import annotations

import sys

_USAGE = """\
usage: tren <command> [args]
       tren [options] <target>

commands:
  import <target> [--out PATH] [--name NAME] [--top TOP]
                emit a design manifest for a .f filelist, a .sv file, or a
                directory of sources
  elaborate <manifest> [--root DIR] [--cache DIR] [--refresh]
                elaborate a manifest into the TREN IR
  open <target> [--root DIR] [--cache DIR] [--refresh] [--out PATH]
                import the target unless it is a manifest, elaborate it and
                open the TUI
  config [show|export] [--out PATH]
                show the effective settings with their origins, or export a
                complete TOML template

debug flags:
  --debug-config
                print the effective configuration as TOML and exit
  --debug-config-origins
                print the values a TOML file set, grouped by file path, and exit

A bare 'tren <target>' is the same as 'tren open <target>'. Run
'tren open --help' for the full option list.
"""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(_USAGE)
        return 0
    if argv[0] in ("--debug-config", "--debug-config-origins"):
        if len(argv) != 1:
            print(f"tren: {argv[0]} takes no arguments", file=sys.stderr)
            return 2
        from .config import ConfigError, load_config, render_export, render_origins

        try:
            loaded = load_config()
        except ConfigError as exc:
            print(f"tren: {exc}", file=sys.stderr)
            return 2
        text = (
            render_export(loaded)
            if argv[0] == "--debug-config"
            else render_origins(loaded)
        )
        sys.stdout.write(text)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "import":
        from .importer import main as import_main
        return import_main(rest)
    if cmd == "elaborate":
        from .elaborate import main as elaborate_main
        return elaborate_main(rest)
    if cmd == "open":
        from .bringup import main as bringup_main
        return bringup_main(rest, prog="tren open")
    if cmd == "config":
        from .config import main as config_main
        return config_main(rest)
    from .bringup import main as bringup_main
    return bringup_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
