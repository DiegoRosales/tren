"""Vivado project importer (deck card 217).

Parses the parts of a Vivado workspace that survive non-interactive use and
emits the design manifest (sources / top) the elaborator consumes.

What we read:

  - ``project.xpr`` — the XML project. Useful bits:
      * ``<FileSet Type="DesignSrcs">`` ``<File Path=...>`` — the design
        sources (Vivado quotes every added file),
      * ``<Config><Option Name="TopModule">`` in that FileSet — the
        synthesis top,
      * the ``$PRUNDIR/$PPRDIR/$PSRCDIR/$PGENDIR/$PCACHEDIR/
        $PIPUSERFILESDIR`` variable scheme, which we resolve against the
        project directory (Vivado's own conventional layout).
    Block-design ``.bd`` entries are not Verilog — dropped with a warning.
    Runs, options and XPM libraries are not our concern; unknown entries are
    ignored (degrade gracefully with warnings).

  - ``vivado.jou`` — the recorded command line. Batch-generated projects
    (e.g. a Vitis HLS export, `impl/verilog/`) record only
    ``source run_<tool>.tcl``; the *actual* RTL is the HLS-generated module
    set sitting next to the ``.xpr`` and is NOT in the FileSets (the FileSet
    lists just the thin ``bd_0_wrapper`` and the ``.bd``). To cover that, the
    importer completes the sources with a directory scan of Verilog files
    under the project dir (skipping Vivado's generated/runs dirs), deduped
    against the FileSet list. This is the card's rule of thumb: recorded /
    on-disk reality covers more ground than the vendor XML.

  - ``set top_module`` in ``settings.tcl`` / recorded ``set_property`` — used
    as the top only when the FileSet has no TopModule and the user did not
    pass ``--top``.

Emitted paths are root-relative (elaborator cwd=root convention).

    nix develop . -c tren import --format vivado <proj-dir|proj.xpr>
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

_VERILOG_SUFFIXES = (".v", ".sv")  # .vh are `include`-files (pulled in by parents)
_SKIP_DIRS = {
    "project.cache",
    "project.hw",
    "project.ip_user_files",
    "project.runs",
    ".Xil",
    ".git",
}
_SKIP_SUFFIXES = {
    ".tcl", ".log", ".jou", ".xpr", ".xdc", ".dat", ".txt", ".xci",
    ".xpe", ".bd", ".f", ".md", ".aux", ".bom", ".csv",
}

_VARS = {
    "$PRUNDIR": "project.runs",
    "$PPRDIR": ".",
    "$PSRCDIR": "project.srcs",
    "$PGENDIR": "project.gen",
    "$PCACHEDIR": "project.cache",
    "$PIPUSERFILESDIR": "project.ip_user_files",
}


@dataclass(frozen=True)
class VivadoProject:
    name: str
    sources: tuple[str, ...]  # paths relative to the importer root
    top: str | None
    warnings: tuple[str, ...]


def _project_dir(xpr: Path) -> Path:
    return xpr.resolve().parent


def _resolve(path: str, project_dir: Path) -> Path:
    """Expand Vivado's $VAR scheme against the project directory."""
    for var, rel in _VARS.items():
        if path.startswith(var):
            tail = path[len(var) :].lstrip("/\\")
            return project_dir / rel / tail
    p = Path(path)
    return p if p.is_absolute() else project_dir / p


def _parse_xpr_file_lists(xpr: Path, warnings: list[str]) -> tuple[list[Path], str | None]:
    """(design-source file paths, top module) from the .xpr FileSets."""
    try:
        root = ET.parse(xpr).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"not a parseable Vivado project.xpr: {exc}") from exc

    project_dir = _project_dir(xpr)
    sources: list[Path] = []
    top: str | None = None
    for fileset in root.iter("FileSet"):
        ftype = fileset.get("Type")
        for cfg in fileset.iter("Config"):
            for opt in cfg.iter("Option"):
                if opt.get("Name") == "TopModule" and top is None:
                    top = opt.get("Val")
        if ftype != "DesignSrcs":
            continue
        for f in fileset.findall("File"):
            raw = (f.get("Path") or "").strip()
            if not raw:
                continue
            resolved = _resolve(raw, project_dir)
            if resolved.suffix.lower() not in _VERILOG_SUFFIXES:
                warnings.append(f"dropping non-Verilog DesignSrcs entry: {raw}")
                continue
            if resolved.is_file():
                sources.append(resolved)
            else:
                warnings.append(f"DesignSrcs file missing: {raw}")
    return sources, top


def _scan_verilog(project_dir: Path) -> list[Path]:
    """Recursive .v/.sv/.vh scan of the project dir, skipping generated dirs."""
    found: list[Path] = []
    for p in project_dir.rglob("*"):
        if p.is_dir():
            continue
        if p.suffix.lower() not in _VERILOG_SUFFIXES:
            continue
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        if p.name.startswith("_"):
            continue
        found.append(p)
    return found


def _recorded_top(project_dir: Path) -> str | None:
    """top module from settings.tcl / vivado.jou if clearly spelled out."""
    for name in ("settings.tcl", "vivado.jou", "vivado.log"):
        path = project_dir / name
        if not path.is_file():
            continue
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if line.startswith("#") or line.startswith("//"):
                continue
            m = re.search(r"\bset\s+top_module\s+([\w\.]+)", line)
            if m:
                return m.group(1)
    return None


def _dedupe_by_basename(paths: list[Path]) -> list[Path]:
    """Keep one copy per module: the shallowest (Vivado's generated dirs
    duplicate the RTL set under ipshared/hdl/verilog)."""
    best: dict[str, Path] = {}
    for p in sorted(paths):
        prev = best.get(p.name)
        if prev is None or len(p.parts) < len(prev.parts):
            best[p.name] = p
    return [best[k] for k in sorted(best)]


def parse_vivado(
    target: str | Path,
    *,
    name: str | None = None,
    top: str | None = None,
    warnings_out: list[str] | None = None,
) -> VivadoProject:
    """Parse a Vivado workspace (dir containing project.xpr, or the .xpr)."""
    target = Path(target)
    xpr = target if target.suffix == ".xpr" else target / "project.xpr"
    if not xpr.is_file():
        raise FileNotFoundError(f"no Vivado project.xpr under {target}")

    warnings: list[str] = []
    project_dir = _project_dir(xpr)

    listed, xpr_top = _parse_xpr_file_lists(xpr, warnings)
    scanned = _scan_verilog(project_dir)
    sources = _dedupe_by_basename([*listed, *scanned])

    recorded = _recorded_top(project_dir)
    if len(scanned) > len(set(listed)):
        warnings.append(
            f"{len(scanned)} Verilog sources also found by directory scan "
            "(batch/HLS projects carry the RTL outside the .xpr FileSets)"
        )
    if xpr_top is None and top is None and recorded is not None:
        warnings.append(f"no TopModule in .xpr; using recorded top '{recorded}'")

    return VivadoProject(
        name=name or project_dir.name,
        sources=tuple(str(s) for s in sources),
        top=xpr_top or recorded if top is None else top,
        warnings=tuple(warnings),
    )


def to_manifest_layout(proj: VivadoProject, root: Path) -> tuple[tuple[str, ...], str | None]:
    """Turn parsed sources into root-relative manifest paths."""
    rel: list[str] = []
    for s in proj.sources:
        rel.append(os.path.relpath(Path(s).resolve(), root))
    return tuple(sorted(set(rel))), proj.top