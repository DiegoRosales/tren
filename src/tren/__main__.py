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

A bare 'tren <target>' is the same as 'tren open <target>'. Run
'tren open --help' for the full option list.
"""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(_USAGE)
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
    from .bringup import main as bringup_main
    return bringup_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
