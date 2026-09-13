"""TREN spike: yosys JSON -> bit map -> drivers/loads of one net.

Runs inside the nix dev shell (yosys is a flake dependency):

    nix develop . -c bash -c "PYTHONPATH=src python -m tren.spike"

or with a pre-generated netlist (skip the yosys subprocess):

    PYTHONPATH=src python -m tren.spike --netlist <out.json>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from .bits import (
    describe_module,
    drivers_of,
    loads_of,
    load_netlist,
    trace_drivers,
    unmangle,
)


def run_yosys(rtl: str, top: str, out_json: str) -> None:
    script = (
        f"read_verilog -sv {rtl}; "
        f"hierarchy -top {top}; "
        "proc; "
        f"write_json {out_json}"
    )
    subprocess.run(
        ["yosys", "-Q", "-p", script],
        check=True,
        capture_output=True,
    )


def show_rows(kind: str, rows: list[dict]) -> None:
    if not rows:
        print(f"  {kind}: (none)")
        return
    for r in rows:
        bits = r["bits"]
        span = f"{bits[0]}" if len(bits) == 1 else f"{bits[0]}..{bits[-1]}"
        print(
            f"  {kind}: {r['cell']!r} ({r['type']}) "
            f"port {r['port']} bits[{span}]"
        )
        if r.get("src"):
            print(f"          src {r['src']}")


def show_driver_chain(design: dict, chain: list[dict], indent: int = 0) -> None:
    pad = "  " * indent
    if not chain:
        print(f"{pad}- (no drivers)")
        return
    for entry in chain:
        note = entry.get("note")
        if note is not None:
            print(f"{pad}- {note}")
            continue
        bits = entry["bits"]
        span = f"{bits[0]}" if len(bits) == 1 else f"{bits[0]}..{bits[-1]}"
        base, params = unmangle(entry["type"])
        src = entry.get("src") or entry.get("net_src")
        print(
            f"{pad}- {entry['cell']!r} ({base}{params or ''}) "
            f"port {entry['port']} bits[{span}]"
        )
        if src:
            print(f"{pad}  src {src}")
        into = entry.get("into")
        if into is not None:
            child_base, _ = unmangle(into["module"])
            print(
                f"{pad}  -> {child_base}.{into['port']} "
                f"(child bits {into['child_bits'][0]}..{into['child_bits'][-1]})"
            )
            show_driver_chain(design, into["drivers"], indent + 2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rtl", nargs="?", default="rtl/demo.sv")
    parser.add_argument("--top", default="top")
    parser.add_argument("--net", default="out")
    parser.add_argument("--netlist", help="pre-generated yosys write_json file")
    parser.add_argument("--net2", default="o", help="second net to show loads of")
    parser.add_argument("--keep", action="store_true", help="keep the netlist JSON")
    args = parser.parse_args(argv)

    if args.netlist is not None:
        design = load_netlist(args.netlist)
    elif args.keep:
        out_dir = Path(".cache/tren")
        out_dir.mkdir(parents=True, exist_ok=True)
        netlist_path = str(out_dir / "netlist.json")
        run_yosys(args.rtl, args.top, netlist_path)
        print(f"(netlist kept at {netlist_path})")
        design = load_netlist(netlist_path)
    else:
        with tempfile.TemporaryDirectory(prefix="tren-spike-") as td:
            netlist_path = str(Path(td) / "netlist.json")
            run_yosys(args.rtl, args.top, netlist_path)
            design = load_netlist(netlist_path)

    print("== design ==")
    for key in design["modules"]:
        base, params = unmangle(key)
        suffix = f"  {{W={params['W']}}}" if "W" in params else ""
        print(f"  module {base}{suffix}")

    module_key = next(
        (k for k, v in design["modules"].items() if k == args.top or unmangle(k)[0] == args.top),
        None,
    )
    if module_key is None:
        print(f"top module {args.top!r} not found", file=sys.stderr)
        return 1

    top_module = design["modules"][module_key]
    net = top_module.get("netnames", {}).get(args.net)
    if net is None:
        print(f"net {args.net!r} not in {args.top}", file=sys.stderr)
        return 1
    bits = set(net["bits"])

    print(f"\n== drivers of {args.top}.{args.net} (width {len(net['bits'])}) ==")
    chain = trace_drivers(design, module_key, args.net)
    show_driver_chain(design, chain)
    if not chain:
        # e.g. `assign o = {hi, lo}`: yosys 0.69 promotes the concatenation to
        # pure net aliasing — o's bit list overlays hi/lo, no cell drives it.
        print("  (no cell drives this net — likely a top-level port or a")
        print("   bit-aliased assignment like o = {hi, lo})")

    print(f"\n== loads of {args.top}.{args.net} ==")
    show_rows("load", [r for r in loads_of(top_module, bits)])

    # second sample: an internal net, to show non-empty loads and the group sizes
    net2 = top_module.get("netnames", {}).get(args.net2)
    if net2 is not None:
        bits2 = set(net2["bits"])
        print(f"\n== drivers of {args.top}.{args.net2} ==")
        show_rows("driver", [r for r in drivers_of(top_module, bits2)])
        print(f"== loads of {args.top}.{args.net2} ==")
        show_rows("load", [r for r in loads_of(top_module, bits2)])

    print("\n== json (driver/load rows for the boundary trace) ==")
    payload = {
        "top": args.top,
        "net": args.net,
        "width": len(net["bits"]),
        "drivers": trace_drivers(design, module_key, args.net),
        "loads": [r for r in loads_of(top_module, bits)],
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())