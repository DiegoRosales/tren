#!/usr/bin/env python
"""Card 215 acceptance: 1k-scope lazy-load + cache-hit profile.

Prints, for a synthetic 1100-scope IR:
  - how many scope *tree nodes* exist at mount (lazy: only the roots)
    vs after expanding one branch vs fully expanded — versus the total.
  - a j-scroll loop duration.
  - cold vs warm `elaborate()` (tool-invocation counts) with the on-disk
    cache (manifest + mtimes), proving a second run starts without re-running
    the tools.

Measured (nix develop, 2026-09-14):
  scopes in IR:                          1100
    tree nodes at mount (eager):         1100   (100% — whole graph built)
    tree nodes at mount (lazy):           100   (  9% — roots only)
    after scanning one branch (lazy):     110   (lineage only)
    fully expanded manually:             1100
  elaborate cold: 3 tool calls / warm: 0 tool calls (cache hit)

Run:  nix develop . -c python tools/profile_1k_scopes.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tren.cache import IrCache  # noqa: E402
from tren.elaborate import elaborate  # noqa: E402
from tren.ir import Design, Instance, Net, Param, Port, Scope  # noqa: E402
from tren.manifest import Manifest  # noqa: E402
from tren.tui import TrenApp  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def synthetic_1k() -> Design:
    """100 roots x 10 children == 1100 scopes, each with ports + nets."""
    ports = (Port(name="a", direction="in", width=32), Port(name="result", direction="out", width=32))
    nets = (Net(name="x", width=32, bits=(1, 2, 3)), Net(name="result", width=32, bits=(7, 8)))
    scopes: list[Scope] = []
    for i in range(100):
        child_instances = tuple(
            Instance(name=f"c{j}", module="mod", src=f"rtl/gen.sv:{10 + j}")
            for j in range(10)
        )
        scopes.append(
            Scope(
                name=f"top{i}",
                module=f"m{i}",
                ports=ports,
                nets=nets,
                instances=child_instances,
                src="rtl/gen.sv:1",
            )
        )
        for j in range(10):
            scopes.append(
                Scope(name=f"top{i}.c{j}", module="mod", ports=ports, nets=nets, src="rtl/gen.sv:9")
            )
    return Design(
        top="top0",
        scopes=tuple(scopes),
        connectivity=(),
        boundaries=(),
        created_by="tools/profile_1k_scopes.py (synthetic)",
    )


def count_scope_nodes(app: TrenApp) -> int:
    def walk(node) -> int:
        total = 0
        for child in node.children:
            data = child.data
            if data is not None and data.get("kind") == "scope":
                total += 1
                total += walk(child)  # children already ensure-populated by press
        return total

    return walk(app._tree().root)


def ensure(app: TrenApp, node) -> None:
    """_ensure_children exists on the lazy tree; no-op under the eager one."""
    ensure_children = getattr(app, "_ensure_children", None)
    if ensure_children is not None:
        ensure_children(node)


async def main() -> None:
    import asyncio

    design = synthetic_1k()
    total = len(design.scopes)

    app = TrenApp(design, root=REPO)
    async with app.run_test(size=(100, 30)) as pilot:
        at_mount = count_scope_nodes(app)
        t0 = time.perf_counter()
        for _ in range(30):
            await pilot.press("j")
        scroll_s = time.perf_counter() - t0

        app._select_path("top0.c1")
        one_branch = count_scope_nodes(app)

        # expand every scope node (simulating the user descending everywhere):
        # after this the whole graph IS materialized — the contrast with the
        # mount count is the whole point.
        stack = [app._tree().root]
        while stack:
            node = stack.pop()
            ensure(app, node)
            for child in node.children:
                data = child.data
                if data is not None and data.get("kind") == "scope" and not child.is_expanded:
                    child.expand()
                if data is not None and data.get("kind") == "scope":
                    stack.append(child)
        fully = count_scope_nodes(app)

    print(f"scopes in IR:                              {total}")
    print(f"  tree nodes at mount (lazy):             {at_mount}   ({at_mount/total:.0%} of IR)")
    print(f"  after scanning one branch:              {one_branch}")
    print(f"  30 j-presses:                           {scroll_s*1000:.1f} ms")
    print(f"  after expanding everything manually:    {fully}")

    # ---- cache: second run must not re-run the tools -------------------
    import tren.elaborate as E

    calls: dict[str, int] = {"n": 0}
    orig_run = E._run

    def counting(cmd, cwd):
        calls["n"] += 1
        return orig_run(cmd, cwd)

    E._run = counting
    manifest = Manifest.read(REPO / "rtl" / "demo.manifest.json")
    with tempfile.TemporaryDirectory(prefix="tren-profile-cache-") as td:
        cache = IrCache(Path(td) / "cache", root=REPO)
        t_cold = time.perf_counter()
        elaborate(manifest, REPO, cache=cache)
        cold = time.perf_counter() - t_cold
        n_cold = calls["n"]
        t_warm = time.perf_counter()
        elaborate(manifest, REPO, cache=cache)
        warm = time.perf_counter() - t_warm
        n_warm = calls["n"] - n_cold
    E._run = orig_run
    print(f"\nelaborate cold (yosys+slang):       {cold*1000:.0f} ms, {n_cold} tool calls")
    print(f"elaborate warm  (cache hit):        {warm*1000:.0f} ms, {n_warm} tool calls")

    if at_mount >= total:
        print("\nBEFORE (eager tree): mount materialized ALL {total} scopes".format(total=total))
    else:
        print("\nAFTER (lazy tree): mount materialized only the roots")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())