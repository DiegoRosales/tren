#!/usr/bin/env python
"""Regenerate the README "Keys" block from the key map: `make guide`.

The user guide's key table and the `?` cheatsheet both render from the same
declarative list in `tren/keys.py` (deck card 248), so they cannot drift.
Run this after you change a binding, then commit the README change together
with the code. `tests/test_keys.py` asserts the committed README still
matches, so forgetting to run it fails `make test`.

Run (as the Makefile does):
    nix develop . -c python tools/guide.py      # rewrites README.md in place
    nix develop . -c python tools/guide.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tren.keys import KEYS_BEGIN, KEYS_END, render_readme_block  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
README = REPO / "README.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the would-be block instead of writing README.md",
    )
    args = parser.parse_args(argv)

    current = README.read_text()
    start = current.index(KEYS_BEGIN)
    end = current.index(KEYS_END) + len(KEYS_END)
    block = render_readme_block()
    if args.dry_run:
        print(block)
        return 0
    README.write_text(current[:start] + block + current[end:])
    print(f"regenerated the README keys block in {README}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())