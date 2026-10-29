#!/usr/bin/env python
"""Auto-generate the SW bill of materials: `make bom`.

Writes `docs/sw-bom.md`. It must run inside the nix dev shell so
the backends are on PATH, and it reports what the shell *actually resolves*:

- runtime Python packages      - every distribution seen by the shell's
                                 interpreter, with the version it resolves
                                 (textual, rich and their transitive deps);
- elaboration backends/tools   - the version each CLI reports on PATH;
- pinned target versions       - the numbers README's "Pinned versions"
                                 sentence names (the rewrite targets).

The document is generated, not maintained by hand, so it cannot drift from
the installed toolchain. Regenerate it whenever the flake pins or pyproject
dependencies change.

Run (as the Makefile does):
    nix develop . -c python tools/bom.py        # -> docs/sw-bom.md
    nix develop . -c python tools/bom.py --out /tmp/sw-bom.md
"""

from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PYPROJECT = REPO / "pyproject.toml"
README = REPO / "README.md"
DEFAULT_OUT = REPO / "docs" / "sw-bom.md"

# A tool: (name, argv, role, version extractor).
# The extractor takes the first non-empty output line and returns a short
# version string. It is pure so the unit tests can feed it a sample line.
def _vers_yosys(line: str) -> str:
    return line.split()[1]  # "Yosys 0.69+post (..."


def _vers_slang(line: str) -> str:
    return line.partition("version ")[2].strip()  # "slang version 11.0.0+0"


def _vers_verilator(line: str) -> str:
    return " ".join(line.split()[1:3])  # "Verilator 5.052 2026-09-05 rev ..."


def _vers_rustc(line: str) -> str:
    return line.split()[1]  # "rustc 1.98.1 (...)"


def _vers_cargo(line: str) -> str:
    return line.split()[1]  # "cargo 1.98.0 (...)"


def _vers_jq(line: str) -> str:
    return line.split("-", 1)[-1]  # "jq-1.8.2"


def _vers_rg(line: str) -> str:
    return line.split()[1]  # "ripgrep 15.2.0"


def _vers_tmux(line: str) -> str:
    return line.split()[-1]  # "tmux 3.7c"


def _vers_python(line: str) -> str:
    return line.split()[1]  # "Python 3.14.7"


TOOLS: tuple[tuple[str, list[str], str, object], ...] = (
    ("yosys", ["-V"], "bit-level netlist (write_json): connectivity, bit map, black-box ports", _vers_yosys),
    ("slang", ["--version"], "slang-hier scope tree + resolved params; slang --ast-json declarations", _vers_slang),
    ("verilator", ["--version"], "optional importer test subject (-E/--json-only)", _vers_verilator),
    ("rustc", ["--version"], "rewrite-target toolchain (ratatui rewrite)", _vers_rustc),
    ("cargo", ["--version"], "rewrite-target toolchain (ratatui rewrite)", _vers_cargo),
    ("jq", ["--version"], "dev-shell JSON inspection", _vers_jq),
    ("rg", ["--version"], "dev-shell search", _vers_rg),
    ("tmux", ["-V"], "real-terminal OSC 52 and pane-cue verification", _vers_tmux),
    ("python3", ["--version"], "the Python interpreter TREN runs on", _vers_python),
)

# Declared project dependencies: package name -> role. The pyproject file is
# parsed for the constraint string; they are also the only two first-party
# imports in the package.
DECLARED_ROLES = {
    "textual": "3-pane TUI: Tree, source/ports panes, Pilot tests (8.2 pinned)",
    "rich": "rich.text.Text styling for the source and I/O panes",
}


def _first_line(output: str) -> str:
    for line in output.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def probe_version(argv: list[str], extract: object, output: str) -> str:
    """Version string for a tool given its `--version` output.

    `argv` is informational (for a helpful error). `extract` is the pure
    parser; `output` is the captured stdout+stderr.
    """
    line = _first_line(output)
    if not line:
        return "not found"
    try:
        version = extract(line)
    except (IndexError, ValueError):
        return line  # unknown shape: report the raw line rather than guess
    return version if version else line


def _run_version(argv: list[str]) -> str:
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=30, check=False
        )
    except OSError as exc:
        return f"error: {exc}"
    except subprocess.TimeoutExpired:
        return "timeout"
    return (proc.stdout or "") + (proc.stderr or "")


def _pyproject_declared() -> list[tuple[str, str]]:
    """(package, constraint) rows from pyproject.toml's dependencies."""
    text = PYPROJECT.read_text()
    rows: list[tuple[str, str]] = []
    in_deps = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("dependencies"):
            in_deps = True
            continue
        if in_deps:
            if stripped.startswith("]") or stripped.startswith("["):
                break
            match = re.match(r'"([A-Za-z0-9_.-]+)\s*([><=!~]+.*)?"', stripped)
            if match:
                name, constraint = match.group(1), match.group(2) or "any"
                rows.append((name, constraint.rstrip('",')))
    return rows


def _python_closure() -> list[tuple[str, str, str]]:
    """(name, version, role) for every dist seen by the interpreter.

    The dev-shell interpreter exposes exactly the runtime closure: textual,
    rich, and rich's transitive dependencies. `DECLARED_ROLES` marks the
    first-party imports; everything else is transitive to rich.
    """
    import importlib.metadata as md

    rows: list[tuple[str, str, str]] = []
    for dist in sorted(md.distributions(), key=lambda d: (d.metadata.get("Name") or "").lower()):
        name = dist.metadata.get("Name") or dist.metadata.get("name") or "?"
        version = dist.version or "?"
        role = DECLARED_ROLES.get(name, "transitive dependency of rich")
        rows.append((name, version, role))
    return rows


_PINNED = re.compile(
    r"\b(Python|textual|rich|rust|ratatui|yosys|sv-lang|cargo)\s+([0-9][0-9a-z.+-]*)"
)


def _pinned_targets() -> list[tuple[str, str]]:
    """(name, version) pairs from README's Pinned versions sentence."""
    text = README.read_text()
    found: dict[str, str] = {}
    for name, version in _PINNED.findall(text):
        found[name] = version.rstrip(".")
    return sorted(found.items())


def _commit_sha() -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return "unknown"
    return proc.stdout.strip() or "unknown"


def render_bom(*, commit: str, date: str) -> str:
    """Assemble the markdown document (the non-tool parts are pure here)."""
    declared = _pyproject_declared()
    declared_by_name = dict(declared)
    closure = _python_closure()
    pinned = _pinned_targets()

    def py_row(name: str, version: str, role: str) -> str:
        constraint = declared_by_name.get(name, "transitive")
        return f"| {name} | {version} | {constraint} | {role} |"

    python_rows = "\n".join(py_row(*row) for row in closure)
    tool_rows = "\n".join(
        f"| {name} | {probe_version(args, extract, _run_version([name, *args]))} | {role} |"
        for name, args, role, extract in TOOLS
    )
    pinned_rows = "\n".join(f"| {name} | {version} |" for name, version in pinned)

    return f"""# TREN - Software bill of materials

Auto-generated by `tools/bom.py` (`make bom`). Do not edit by hand.

Regenerate with: `nix develop . -c python tools/bom.py`

Source: {REPO} @ {commit}   Generated: {date} (UTC)

## Runtime Python packages

Declared in `pyproject.toml`; the version column is what the dev-shell
interpreter resolves, and the constraint column is the declared floor.

| Package | Version | Declared | Role |
|---|---|---|---|
{python_rows}

## Elaboration backends and tooling

Versions are what each CLI reports on PATH inside the dev shell.

| Component | Version | Role |
|---|---|---|
{tool_rows}

## Pinned target versions

The numbers README's `Pinned versions` sentence names. These include the
earmarked rewrite target (ratatui) that is not yet a runtime dependency.

| Component | Version |
|---|---|
{pinned_rows}
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT,
        help="output path (default: docs/sw-bom.md)",
    )
    args = parser.parse_args(argv)

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
    doc = render_bom(commit=_commit_sha(), date=now)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(doc)
    print(f"wrote SW BOM to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
