"""Run log: the backend commands, their output, and the steps TREN took.

TREN builds the IR with yosys and slang and says little on the terminal.
``tren open``, ``tren elaborate`` and ``tren stub`` write the full story to
``tren.log`` in the current directory, so a slow or surprising elaboration
can be read back after the TUI is gone.

``--log PATH`` writes the log elsewhere. ``--nolog`` writes no log. Each run
replaces the file: one run, one trace.
"""

from __future__ import annotations

import shlex
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

DEFAULT_PATH = "tren.log"


def command_text(cmd: object) -> str:
    """One command line with shell quoting; accepts a string or argument list."""
    if isinstance(cmd, (list, tuple)):
        return shlex.join(str(part) for part in cmd)
    return str(cmd)


def cli_command(prog: str, argv: Sequence[str] | None) -> str:
    """The ``tren`` command line of this run."""
    if argv is None:
        return shlex.join(str(part) for part in sys.argv)
    return shlex.join([*prog.split(), *(str(part) for part in argv)])


def _stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Logger:
    """One open ``tren.log``, written by one command."""

    def __init__(
        self,
        path: str | Path,
        *,
        command: str = "",
        cwd: str | Path | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A file name can hold bytes that are not valid UTF-8, which Python
        # exposes as surrogates. Escape those instead of failing the run.
        self._file = self.path.open(
            "w", encoding="utf-8", errors="backslashreplace"
        )
        self._file.write(f"TREN log\nstarted: {_stamp()}\n")
        if command:
            self._file.write(f"command: {command}\n")
        if cwd is not None:
            self._file.write(f"cwd: {Path(cwd).resolve()}\n")
        self._file.write("\n")
        self._file.flush()

    def note(self, message: str) -> None:
        """One step of the run."""
        self._write(f"[{_stamp()}] {message}\n")

    def backend(
        self,
        cmd: object,
        *,
        cwd: str | Path,
        returncode: int,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        """One backend process: its command, exit code and output."""
        self._write(f"[{_stamp()}] run: {command_text(cmd)}\n")
        self._write(f"[{_stamp()}] cwd: {Path(cwd).resolve()}\n")
        self._write(f"[{_stamp()}] exit: {returncode}\n")
        for label, text in (("stdout", stdout), ("stderr", stderr)):
            if text:
                self._write(f"[{_stamp()}] {label}:\n")
                self._write(text if text.endswith("\n") else text + "\n")

    def close(self) -> None:
        """Flush and close the file; safe to call twice."""
        if self._file is None:
            return
        try:
            self._file.close()
        except OSError:
            pass
        self._file = None

    def _write(self, text: str) -> None:
        # A full disk must not stop an elaboration that would otherwise work.
        if self._file is None:
            return
        try:
            self._file.write(text)
            self._file.flush()
        except OSError as exc:
            failed = self._file
            self._file = None
            try:
                failed.close()
            except OSError:
                pass
            print(f"  warning: the log stopped: {exc}", file=sys.stderr)


# The backend wrappers run far below the CLI, so the open log travels as the
# active logger. One process runs one command.
_ACTIVE: Logger | None = None


def active() -> Logger | None:
    """The open log, or None when this run writes no log."""
    return _ACTIVE


def set_active(logger: Logger | None) -> Logger | None:
    """Make ``logger`` the log for this run; return the previous one."""
    global _ACTIVE
    previous = _ACTIVE
    _ACTIVE = logger
    return previous


def note(message: str) -> None:
    """Write one step to the open log, if there is one."""
    logger = _ACTIVE
    if logger is not None:
        logger.note(message)


def add_arguments(parser: Any) -> None:
    """Add ``--log PATH`` and ``--nolog`` to a command parser."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--log", type=Path, default=None, metavar="PATH",
        help=f"write the run log here (default: {DEFAULT_PATH})",
    )
    group.add_argument(
        "--nolog", action="store_true", help="write no run log",
    )


def from_args(
    args: Any,
    *,
    command: str = "",
    cwd: str | Path | None = None,
) -> Logger | None:
    """Open the log the parsed arguments select; None when it is off.

    The header records ``cwd``, or the process working directory when the
    caller gives none. A log that cannot be written warns and leaves the run
    without one.
    """
    if args.nolog:
        return None
    path = DEFAULT_PATH if args.log is None else args.log
    try:
        return Logger(
            path,
            command=command,
            cwd=Path.cwd() if cwd is None else cwd,
        )
    except OSError as exc:
        print(f"  warning: cannot write the log {path}: {exc}", file=sys.stderr)
        return None
