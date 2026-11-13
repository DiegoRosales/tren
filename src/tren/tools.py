"""Checks for the external programs TREN runs.

TREN needs MikePopoloski's SystemVerilog slang. Other programs also carry
the name ``slang``: Shader Slang ships a ``slang`` interpreter, and kitty
puts its bin directory on PATH for custom shaders. The SystemVerilog package
installs ``slang-hier`` beside ``slang``; the others do not. That sibling is
the check.
"""

from __future__ import annotations

import shutil
from pathlib import Path


def slang_path() -> Path | None:
    """The ``slang`` that PATH resolves, or None when there is none."""
    found = shutil.which("slang")
    return Path(found) if found else None


def slang_warning(path: Path | None = None) -> str | None:
    """Warn when ``slang`` is not the SystemVerilog compiler.

    Return None when the tool is missing (the caller already warns about
    that) or when it is the right one. The check does not run the binary:
    ``slang-hier`` sits beside the SystemVerilog ``slang`` and is absent
    from the Shader Slang package.
    """
    if path is None:
        path = slang_path()
    if path is None:
        return None
    if (path.parent / "slang-hier").exists():
        return None
    if "shader-slang" in str(path):
        return (
            f"slang at {path} is the Shader Slang compiler, not the "
            "SystemVerilog slang TREN needs; put sv-lang's bin directory "
            "before it on PATH"
        )
    return (
        f"slang at {path} is not a complete SystemVerilog slang install "
        "(no slang-hier beside it); put sv-lang's bin directory on PATH"
    )
