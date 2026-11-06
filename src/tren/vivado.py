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
    the per-IP simulation netlists and stubs. It reads the IP component XMLs
    (``<ip>.xml`` or ``component.xml``) and drops a file that is only in a
    behavioral simulation file set. The synthesis file set is the authority.
    A behavioral file remains when its synthesis pair has no Verilog file.
    The synthesis tree also wins over a ``sim`` or ``simulation`` model; the
    model remains only when no synthesis counterpart exists. The result is
    deduped against the FileSet list. This is the card's rule of thumb:
    recorded / on-disk reality covers more ground than the vendor XML.
    When a source includes a header the filelist does not cover, the importer
    finds the header under ``XILINX_VIVADO`` and adds its directory to the
    incdirs. Without the variable it warns and lets slang fail. The scan
    ignores comments and resolves every preprocessor branch. It warns about a
    missing include only when the include is outside a conditional, because a
    guarded include may be inactive.

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
_PAIR_PREFIXES = (
    ("xilinx_verilogbehavioralsimulation", "simulation"),
    ("xilinx_verilogsimulationwrapper", "simulation"),
    ("xilinx_anylanguagebehavioralsimulation", "simulation"),
    ("xilinx_anylanguagesimulationwrapper", "simulation"),
    ("xilinx_verilogsynthesis", "synthesis"),
    ("xilinx_verilogsynthesiswrapper", "synthesis"),
    ("xilinx_anylanguagesynthesis", "synthesis"),
    ("xilinx_anylanguagesynthesiswrapper", "synthesis"),
)
_INCLUDE_OR_GUARD_RE = re.compile(
    r'`include\s+["<](?P<include>[^">\n]+)[">]'
    r"|`(?P<guard>ifdef|ifndef|elsif|else|endif)\b"
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


def _local_name(tag: str) -> str:
    """XML tag without its namespace prefix."""
    return tag.rsplit("}", 1)[-1]


def _child_text(element: ET.Element, name: str) -> str | None:
    """Text of the first direct child with local name ``name``."""
    for child in element:
        if _local_name(child.tag) == name and child.text:
            return child.text.strip()
    return None


def _component_file_sets(
    project_dir: Path,
) -> list[tuple[set[Path], set[Path]]]:
    """Return (synthesis, simulation) file-set pairs from the component XMLs.

    Each file-set name is paired with the same-name set of the other role.
    A file that is in a behavioral set but not in its synthesis set is a
    simulation model. A behavioral set with no synthesis file set at all is
    also a simulation model. The scanner drops those models unless the paired
    synthesis set exists but has no Verilog file, as for a VHDL-only IP.
    """
    pairs: dict[str, tuple[set[Path], set[Path]]] = {}
    for xml_path in project_dir.rglob("*.xml"):
        try:
            rel = xml_path.relative_to(project_dir)
        except ValueError:
            continue
        if _skip_generated(rel):
            continue
        try:
            root = ET.parse(xml_path).getroot()
        except (ET.ParseError, OSError, LookupError, ValueError):
            continue
        if _local_name(root.tag) != "component":
            continue
        for file_set in root.iter():
            if _local_name(file_set.tag) != "fileSet":
                continue
            set_name = _child_text(file_set, "name")
            if not set_name:
                continue
            name = set_name.lower()
            role = None
            key = ""
            for prefix, prefix_role in sorted(
                _PAIR_PREFIXES, key=lambda item: -len(item[0])
            ):
                if name.startswith(prefix):
                    role = prefix_role
                    key = name[len(prefix):]
                    break
            if role is None:
                continue
            synthesis, simulation = pairs.setdefault(key, (set(), set()))
            target = synthesis if role == "synthesis" else simulation
            for file_el in file_set:
                if _local_name(file_el.tag) != "file":
                    continue
                file_name = _child_text(file_el, "name")
                if not file_name:
                    continue
                target.add((xml_path.parent / file_name).resolve())
    return list(pairs.values())


def _drop_behavioral_only(files: list[Path], project_dir: Path) -> list[Path]:
    """Drop files the IP component XML marks as simulation-only.

    A behavioral file survives when its synthesis pair has no Verilog file.
    That keeps the only readable model for a VHDL-only IP.
    """
    drop: set[Path] = set()
    for synthesis, simulation in _component_file_sets(project_dir):
        if not synthesis:
            drop |= simulation
        elif any(p.suffix.lower() in _VERILOG_SUFFIXES for p in synthesis):
            drop |= simulation - synthesis
    if not drop:
        return files
    return [p for p in files if p.resolve() not in drop]


def _prefer_synthesis(files: list[Path]) -> list[Path]:
    """Keep a simulation model only when it has no synthesis counterpart.

    The component XML is the primary rule. This fallback covers a project
    without component XML, or a file outside the IP file sets. A ``sim`` file
    loses to a ``synth`` sibling with the same stem. A ``simulation`` file
    loses to a sibling ``hdl`` directory with a Verilog file. A project that
    generated only simulation output keeps the model and still opens.
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
    """Recursive .v/.sv scan of the project dir, skipping generated files.

    The IP component XMLs decide which sibling files are simulation-only.
    The directory rules then handle the generated trees those XMLs do not
    cover.
    """
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
    found = _drop_behavioral_only(found, project_dir)
    return _prefer_synthesis(found)


def _strip_comments(text: str) -> str:
    """Remove ``//`` and ``/* */`` comments, keeping string literals."""
    out: list[str] = []
    i = 0
    end = len(text)
    while i < end:
        char = text[i]
        if char == '"':
            out.append(char)
            i += 1
            while i < end:
                char = text[i]
                out.append(char)
                i += 1
                if char == "\\" and i < end:
                    out.append(text[i])
                    i += 1
                elif char == '"':
                    break
            continue
        if char == "/" and i + 1 < end and text[i + 1] == "/":
            i += 2
            while i < end and text[i] != "\n":
                i += 1
            continue
        if char == "/" and i + 1 < end and text[i + 1] == "*":
            i += 2
            while i < end:
                if text[i] == "*" and i + 1 < end and text[i + 1] == "/":
                    i += 2
                    break
                if text[i] == "\n":
                    out.append("\n")
                i += 1
            continue
        out.append(char)
        i += 1
    return "".join(out)


def _included_files(sources: list[Path]) -> tuple[set[str], set[str]]:
    """The ``include`` names used by the RTL sources.

    Return ``(names, unguarded)``. ``names`` holds every include, including
    one in a preprocessor conditional. ``unguarded`` holds only the includes
    outside a conditional. A guarded include may be inactive, so the caller
    does not warn about a missing one.
    """
    names: set[str] = set()
    unguarded: set[str] = set()
    for source in sources:
        try:
            text = _strip_comments(source.read_text(errors="replace"))
        except OSError:
            continue
        depth = 0
        for line in text.splitlines():
            for match in _INCLUDE_OR_GUARD_RE.finditer(line):
                include = match.group("include")
                if include is not None:
                    names.add(include)
                    if depth == 0:
                        unguarded.add(include)
                    continue
                guard = match.group("guard")
                if guard in ("ifdef", "ifndef"):
                    depth += 1
                elif guard == "endif":
                    depth = max(0, depth - 1)
    return names, unguarded


def _include_is_available(
    include: str, incdirs: list[Path], sources: list[Path]
) -> bool:
    """True when the filelist or a source directory already resolves the include."""
    if Path(include).is_absolute() and Path(include).is_file():
        return True
    for directory in incdirs:
        if (Path(directory) / include).is_file():
            return True
    for source in sources:
        if (source.parent / include).is_file():
            return True
    return False


def _known_xilinx_incdir(include: str, vivado: Path) -> Path | None:
    """Return a known install directory that resolves ``include``, or None."""
    relative = Path(include)
    if relative.is_absolute():
        return None
    for base in (
        vivado,
        vivado / "data" / "rsb" / "busdef",
        vivado / "data",
        vivado.parent,
        vivado.parent / "data" / "rsb" / "busdef",
        vivado.parent / "data",
    ):
        if (base / relative).is_file():
            return base
    return None


def _drop_parts(path: Path, count: int) -> Path:
    """Remove ``count`` trailing path parts."""
    for _ in range(count):
        path = path.parent
    return path


def _search_xilinx_tree(includes: list[str], vivado: Path) -> dict[str, Path]:
    """Find the includes in one walk of the Vivado data trees.

    Return a map from include name to the directory that resolves it. The
    walk is a fallback for a header outside the known directories.
    """
    pending = {include: tuple(Path(include).parts) for include in includes}
    found: dict[str, Path] = {}
    for root in (vivado / "data", vivado.parent / "data"):
        if not root.is_dir():
            continue
        for candidate in root.rglob("*"):
            if not candidate.is_file():
                continue
            matched = [
                include
                for include, parts in pending.items()
                if candidate.parts[-len(parts):] == parts
            ]
            for include in matched:
                found[include] = _drop_parts(
                    candidate, len(pending[include])
                )
                del pending[include]
            if not pending:
                return found
    return found


def _add_xilinx_incdirs(
    sources: list[Path], incdirs: list[Path], warnings: list[str]
) -> None:
    """Add the Vivado install directory for an include the filelist lacks.

    The generated BD SV wrapper includes ``vivado_interfaces.svh`` from the
    Vivado install, not from the project. When ``XILINX_VIVADO`` is set, the
    importer finds the header and adds its directory to the incdirs. Without
    the variable it warns and lets slang report the failure. The scan resolves
    includes in every preprocessor branch. It warns about a missing include
    only when the include is outside a conditional, because a guarded include
    may be inactive.
    """
    includes, unguarded = _included_files(sources)
    if not includes:
        return
    vivado_env = os.environ.get("XILINX_VIVADO")
    vivado = Path(vivado_env) if vivado_env else None
    pending: list[str] = []
    for include in sorted(includes):
        if _include_is_available(include, incdirs, sources):
            continue
        if vivado is not None:
            base = _known_xilinx_incdir(include, vivado)
            if base is not None:
                incdirs.append(base)
                continue
        pending.append(include)
    if not pending:
        return
    found = _search_xilinx_tree(pending, vivado) if vivado is not None else {}
    for include in pending:
        base = found.get(include)
        if base is not None:
            incdirs.append(base)
            continue
        if include not in unguarded:
            continue
        if vivado is None:
            warnings.append(
                f"include '{include}' is not in the filelist; set "
                "XILINX_VIVADO to let TREN find it in the Vivado install; "
                "slang may fail"
            )
        else:
            warnings.append(
                f"include '{include}' is not in the filelist and was not "
                f"found under XILINX_VIVADO ({vivado}); slang may fail"
            )


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
    _add_xilinx_incdirs(sources, incdirs, warnings)

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