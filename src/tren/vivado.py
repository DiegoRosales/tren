"""Vivado project importer.

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
    under the project dir. The scan skips the generated run/cache dirs and
    the per-IP simulation netlists and stubs. The synthesis tree wins over a
    ``sim`` or ``simulation`` model; the model remains only when no
    synthesis counterpart exists. The result is deduped against the FileSet
    list. This is the card's rule of thumb: recorded / on-disk reality covers
    more ground than the vendor XML.

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
_HEADER_SUFFIXES = (".vh", ".svh")  # FileSet entries typed as Verilog Headers
# Vivado writes several representations of each generated design unit. Only
# the synthesis tree is the design: sim/ and simulation/ hold behavioral
# models, and *_sim_netlist.* / *_stub.* repeat a module the wrapper already
# declares (with a shared glbl that would be defined once per IP).
_SIM_DIR = "sim"
_SIMULATION_DIR = "simulation"
_SYNTH_DIR = "synth"
_HDL_DIR = "hdl"
_GENERATED_DIR_SUFFIXES = (
    ".cache",
    ".gen",
    ".hw",
    ".ip_user_files",
    ".runs",
    ".sim",
)
_SKIP_DIRS = {".Xil", ".git"}
_SKIP_NAME_SUFFIXES = (
    "_sim_netlist.v",
    "_sim_netlist.sv",
    "_stub.v",
    "_stub.sv",
)

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
    incdirs: tuple[str, ...] = ()  # dirs of FileSet entries typed as headers


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


def _parse_xpr_file_lists(
    xpr: Path, warnings: list[str], incdirs_out: list[Path] | None = None
) -> tuple[list[Path], str | None]:
    """(design-source file paths, top module) from the .xpr FileSets.

    ``incdirs_out``, when given, collects the directories of FileSet entries
    that are Verilog headers (``.vh``/``.svh``): Vivado records real include
    dirs as header-typed files. Without it those entries fall back
    to the generic non-Verilog warning (the low-level reader's default).
    """
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
            if resolved.suffix.lower() in _HEADER_SUFFIXES:
                if incdirs_out is not None:
                    incdirs_out.append(resolved.parent)
                else:
                    warnings.append(
                        f"dropping Verilog-header DesignSrcs entry (no incdir sink): {raw}"
                    )
                continue
            if resolved.suffix.lower() not in _VERILOG_SUFFIXES:
                warnings.append(f"dropping non-Verilog DesignSrcs entry: {raw}")
                continue
            if resolved.is_file():
                sources.append(resolved)
            else:
                warnings.append(f"DesignSrcs file missing: {raw}")
    return sources, top


def _skip_generated(rel: Path) -> bool:
    """True when ``rel`` is a generated copy, not a design source.

    ``rel`` is relative to the project directory, so the project's own name
    cannot make a directory look generated. A file is generated when any
    parent directory is a Vivado run/cache tree or ``.Xil``, or when it is an
    IP stub or simulation netlist under ``ip/``. Simulation trees are not
    skipped here: ``_prefer_synthesis`` keeps a model only when the synthesis
    tree has no counterpart.
    """
    parts = rel.parts
    if (
        parts
        and parts[-1].lower().endswith(_SKIP_NAME_SUFFIXES)
        and "ip" in parts[:-1]
    ):
        return True
    for part in parts[:-1]:
        if part in _SKIP_DIRS:
            return True
        if part.endswith(_GENERATED_DIR_SUFFIXES):
            return True
    return False


def _prefer_synthesis(files: list[Path]) -> list[Path]:
    """Keep a simulation model only when it has no synthesis counterpart.

    A ``sim`` file loses to a ``synth`` sibling with the same stem. A
    ``simulation`` file loses to a sibling ``hdl`` directory with a Verilog
    file. A project that generated only simulation output keeps the model and
    still opens.
    """
    synth_stems: dict[Path, set[str]] = {}
    kept: list[Path] = []
    for p in files:
        parent = p.parent
        if parent.name == _SIM_DIR:
            synth = parent.parent / _SYNTH_DIR
            if synth.is_dir():
                stems = synth_stems.get(synth)
                if stems is None:
                    stems = {
                        f.stem
                        for f in synth.iterdir()
                        if f.suffix.lower() in _VERILOG_SUFFIXES
                    }
                    synth_stems[synth] = stems
                if p.stem in stems:
                    continue
        elif parent.name == _SIMULATION_DIR:
            hdl = parent.parent / _HDL_DIR
            if hdl.is_dir() and any(
                f.suffix.lower() in _VERILOG_SUFFIXES for f in hdl.iterdir()
            ):
                continue
        kept.append(p)
    return kept


def _scan_verilog(project_dir: Path) -> list[Path]:
    """Recursive .v/.sv scan of the project dir, skipping generated files."""
    found: list[Path] = []
    for p in project_dir.rglob("*"):
        if p.is_dir():
            continue
        if p.suffix.lower() not in _VERILOG_SUFFIXES:
            continue
        if _skip_generated(p.relative_to(project_dir)):
            continue
        if p.name.startswith("_"):
            continue
        found.append(p)
    return _prefer_synthesis(found)


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
) -> VivadoProject:
    """Parse a Vivado workspace (dir containing project.xpr, or the .xpr)."""
    target = Path(target)
    xpr = target if target.suffix == ".xpr" else target / "project.xpr"
    if not xpr.is_file():
        raise FileNotFoundError(f"no Vivado project.xpr under {target}")

    warnings: list[str] = []
    incdirs: list[Path] = []
    project_dir = _project_dir(xpr)

    listed, xpr_top = _parse_xpr_file_lists(xpr, warnings, incdirs_out=incdirs)
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
        incdirs=tuple(str(d) for d in sorted(set(incdirs))),
    )


def to_manifest_layout(
    proj: VivadoProject, root: Path
) -> tuple[tuple[str, ...], tuple[str, ...], str | None]:
    """Turn parsed sources into root-relative manifest paths + incdirs.

    the manifest records the directory of every source (yosys needs
    them spelled out as ``-I``; slang/Vivado imply the including file's own
    directory only), and adds the directories of header-typed FileSet entries
    on top. A source at the root records ``.`` - slang does not search the
    cwd, so a root header included from a subdirectory needs ``-I.``.
    """
    rel: list[str] = []
    incdirs: set[str] = set()
    for s in proj.sources:
        resolved = Path(s).resolve()
        rel.append(os.path.relpath(resolved, root))
        incdirs.add(os.path.relpath(resolved.parent, root))
    for d in proj.incdirs:
        incdirs.add(os.path.relpath(Path(d).resolve(), root))
    return (
        tuple(sorted(set(rel))),
        tuple(sorted(i for i in incdirs if i)),
        proj.top,
    )