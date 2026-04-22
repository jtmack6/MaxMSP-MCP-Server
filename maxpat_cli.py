#!/usr/bin/env python3
"""Command-line tool to build .maxpat files from a JSON spec.

Spec format (all fields optional except boxes/lines):
    {
        "rect": [100, 100, 900, 700],
        "boxes": [
            {"maxclass": "newobj", "text": "cycle~ 440", "varname": "osc1",
             "patching_rect": [100, 100, 100, 22]},
            {"maxclass": "ezdac~", "varname": "out", "patching_rect": [100, 200, 45, 45]}
        ],
        "lines": [
            {"src": "osc1", "dst": "out", "dst_inlet": 0},
            {"src": "osc1", "dst": "out", "dst_inlet": 1}
        ]
    }

Usage:
    maxpat_cli.py build spec.json                       # writes spec.maxpat alongside
    maxpat_cli.py build spec.json -o ~/out.maxpat
    cat spec.json | maxpat_cli.py build -               # stdin input
    maxpat_cli.py validate spec.json                    # parse & check, no write
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    from maxpat_builder import Patch
except ImportError:
    # Allow running from any cwd when called as an absolute path
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from maxpat_builder import Patch


def _load_spec(src: str) -> dict[str, Any]:
    if src == "-":
        return json.load(sys.stdin)
    path = Path(src).expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"error: spec file not found: {path}")
    with path.open() as f:
        return json.load(f)


def _validate_spec(spec: dict[str, Any]) -> tuple[list, list, list]:
    """Return (boxes, lines, warnings). Raises SystemExit on errors."""
    warnings: list[str] = []
    if not isinstance(spec, dict):
        raise SystemExit("error: spec must be a JSON object")
    boxes = spec.get("boxes")
    lines = spec.get("lines", [])
    if not isinstance(boxes, list):
        raise SystemExit("error: spec.boxes must be a list")
    if not isinstance(lines, list):
        raise SystemExit("error: spec.lines must be a list")

    varnames = set()
    for i, b in enumerate(boxes):
        if not isinstance(b, dict):
            raise SystemExit(f"error: boxes[{i}] must be an object")
        if "maxclass" not in b:
            raise SystemExit(f"error: boxes[{i}] missing 'maxclass'")
        vn = b.get("varname")
        if vn:
            if vn in varnames:
                warnings.append(f"duplicate varname: {vn}")
            varnames.add(vn)

    for i, ln in enumerate(lines):
        if not isinstance(ln, dict):
            raise SystemExit(f"error: lines[{i}] must be an object")
        for key in ("src", "dst"):
            if key not in ln:
                raise SystemExit(f"error: lines[{i}] missing '{key}'")
        # Allow ids like "obj-3" too — just warn if it's a name we don't know
        for key in ("src", "dst"):
            val = ln[key]
            if not val.startswith("obj-") and val not in varnames:
                warnings.append(f"lines[{i}].{key}={val!r} does not match any box varname")

    return boxes, lines, warnings


def _build_patch(spec: dict[str, Any]) -> Patch:
    boxes, lines, _warnings = _validate_spec(spec)
    rect = spec.get("rect")
    p = Patch(rect=tuple(rect) if rect else (100.0, 100.0, 1000.0, 780.0))
    for b in boxes:
        p.add(**b)
    for ln in lines:
        p.connect(
            src=ln["src"],
            dst=ln["dst"],
            src_outlet=int(ln.get("src_outlet", 0)),
            dst_inlet=int(ln.get("dst_inlet", 0)),
        )
    return p


def _default_output(spec_src: str) -> Path:
    if spec_src == "-":
        raise SystemExit("error: -o is required when reading spec from stdin")
    return Path(spec_src).with_suffix(".maxpat").expanduser().resolve()


def cmd_build(args: argparse.Namespace) -> int:
    spec = _load_spec(args.spec)
    _boxes, _lines, warnings = _validate_spec(spec)
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    patch = _build_patch(spec)
    out = Path(args.output).expanduser().resolve() if args.output else _default_output(args.spec)
    out.parent.mkdir(parents=True, exist_ok=True)
    patch.save(str(out))
    print(f"wrote {out}  ({len(spec['boxes'])} boxes, {len(spec.get('lines', []))} lines)")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    spec = _load_spec(args.spec)
    boxes, lines, warnings = _validate_spec(spec)
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    print(f"ok: {len(boxes)} boxes, {len(lines)} lines")
    return 1 if warnings and args.strict else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="maxpat_cli", description=__doc__.strip().split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="build a .maxpat from a JSON spec")
    p_build.add_argument("spec", help="path to JSON spec file, or '-' for stdin")
    p_build.add_argument("-o", "--output", help="output path (default: <spec>.maxpat)")
    p_build.set_defaults(func=cmd_build)

    p_val = sub.add_parser("validate", help="parse and check a JSON spec without writing")
    p_val.add_argument("spec", help="path to JSON spec file, or '-' for stdin")
    p_val.add_argument("--strict", action="store_true", help="exit non-zero if there are warnings")
    p_val.set_defaults(func=cmd_validate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
