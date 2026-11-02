"""Clipboard providers for yank.

TREN can write the path in two ways:

``osc52``
    Write an OSC 52 escape sequence through the terminal. This works when the
    terminal or its multiplexer accepts application clipboard writes.

``tmux``
    Run ``tmux load-buffer -w -`` with the payload on standard input. This
    uses tmux's own clipboard path, so it works with ``set-clipboard
    external``, where tmux ignores application OSC 52 writes.

``auto``
    Use ``tmux`` when ``$TMUX`` is set and the ``tmux`` command is available.
    Use ``osc52`` otherwise.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Callable, Mapping

PROVIDERS = ("auto", "tmux", "osc52")


class ClipboardError(Exception):
    """A clipboard provider could not write the payload."""


def resolve_provider(
    configured: str,
    *,
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> str:
    """Return the provider to use for one configured value.

    ``auto`` chooses tmux only inside a tmux session with the command on
    ``PATH``. An explicit provider is returned as it is.
    """
    if configured not in PROVIDERS:
        names = ", ".join(PROVIDERS)
        raise ValueError(f"unknown clipboard provider: {configured!r} ({names})")
    if configured != "auto":
        return configured
    env = os.environ if env is None else env
    if env.get("TMUX") and which("tmux"):
        return "tmux"
    return "osc52"


def write_tmux(
    payload: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    """Write ``payload`` through tmux's clipboard path."""
    try:
        done = runner(
            ["tmux", "load-buffer", "-w", "-"],
            input=payload,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise ClipboardError(f"cannot run tmux: {exc}") from exc
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip()
        if not detail:
            detail = f"tmux load-buffer failed (exit {done.returncode})"
        raise ClipboardError(detail)
