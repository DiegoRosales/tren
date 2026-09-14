"""``python -m tren`` — the subcommand front door (card 211).

Commands:

    import <target> [--out PATH] [--name NAME] [--top TOP]
                emit a design manifest for a .f filelist, a .sv file, or a
                directory of sources (tren.importer)
    elaborate <manifest> [--root DIR] [--cache DIR] [--refresh]
                elaborate a manifest into the TREN IR (tren.elaborate)

A bare ``tren <manifest>`` (no subcommand) also elaborates, for the previous
one-command form.
"""

from __future__ import annotations

import sys

_USAGE = """\
usage: python -m tren <command> [args]

commands:
  import <target> [--out PATH] [--name NAME] [--top TOP]
                emit a design manifest for a .f filelist, a .sv file, or a
                directory of sources
  elaborate <manifest> [--root DIR] [--cache DIR] [--refresh]
                elaborate a manifest into the TREN IR

run 'python -m tren <command> --help' for command help.
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
    if cmd.startswith("-"):
        print(f"unknown option: {cmd}", file=sys.stderr)
        print(_USAGE, file=sys.stderr)
        return 2
    # legacy one-command form: a bare manifest path elaborates
    from .elaborate import main as elaborate_main
    return elaborate_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())