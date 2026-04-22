"""Builder for Max .maxpat files.

Usage:
    p = Patch(rect=[100, 100, 800, 600])
    p.add(maxclass="newobj", text="cycle~ 440", varname="osc1", patching_rect=[100, 100, 100, 22])
    p.add(maxclass="ezdac~", varname="out", patching_rect=[100, 200, 45, 45])
    p.connect(src="osc1", dst="out")
    p.save("/tmp/test.maxpat")
"""
from __future__ import annotations

import json
import os
from typing import Any

DEFAULT_APP = {
    "major": 9,
    "minor": 1,
    "revision": 4,
    "architecture": "x64",
    "modernui": 1,
}

# UI objects (maxclass != "newobj") with known inlet/outlet counts.
# "newobj" types get their I/O from docs.json at runtime.
_UI_OBJECT_IO: dict[str, tuple[int, int]] = {
    "flonum": (1, 2),
    "number": (1, 2),
    "comment": (1, 0),
    "message": (2, 1),
    "button": (1, 1),
    "toggle": (1, 1),
    "ezdac~": (2, 0),
    "ezadc~": (0, 2),
    "dial": (1, 2),
    "slider": (1, 1),
    "gain~": (2, 2),
    "meter~": (1, 1),
    "live.dial": (1, 2),
    "live.slider": (1, 1),
    "live.numbox": (1, 2),
    "scope~": (2, 0),
    "multislider": (1, 2),
    "matrixctrl": (1, 2),
    "textedit": (1, 4),
    "umenu": (2, 3),
    "panel": (0, 0),
    "pictctrl": (1, 2),
    "swatch": (1, 2),
    "rslider": (1, 3),
}

# Output types by maxclass. UI outputs are typically numeric (""), audio uses "signal".
_UI_OUTLETTYPE: dict[str, list[str]] = {
    "flonum": ["", "bang"],
    "number": ["", "bang"],
    "comment": [],
    "ezdac~": [],
    "button": ["bang"],
    "toggle": [""],
    "dial": ["", "bang"],
    "slider": [""],
}

_docs_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs.json")
try:
    with open(_docs_path) as _f:
        _docs = json.load(_f)
    _flat_docs: dict[str, dict[str, Any]] = {}
    for _lst in _docs.values():
        for _obj in _lst:
            _flat_docs[_obj["name"]] = _obj
except Exception:
    _flat_docs = {}


def infer_io(maxclass: str, text: str | None = None) -> tuple[int, int]:
    """Best-effort inlet/outlet count inference."""
    if maxclass in _UI_OBJECT_IO:
        return _UI_OBJECT_IO[maxclass]
    if maxclass == "newobj" and text:
        obj_name = text.split()[0]
        entry = _flat_docs.get(obj_name)
        if entry:
            inlets = len(entry.get("inletlist") or []) or 2
            outlets = len(entry.get("outletlist") or []) or 1
            return (inlets, outlets)
    return (2, 1)


def infer_outlettype(maxclass: str, text: str | None = None) -> list[str] | None:
    if maxclass in _UI_OUTLETTYPE:
        return _UI_OUTLETTYPE[maxclass]
    if maxclass == "newobj" and text:
        obj_name = text.split()[0]
        # Signal objects (~ suffix) output signals
        if obj_name.endswith("~"):
            n = infer_io(maxclass, text)[1]
            return ["signal"] * n
    return None


class Patch:
    def __init__(self, rect: list | tuple = (100.0, 100.0, 1000.0, 780.0)):
        self._boxes: list[dict] = []
        self._lines: list[dict] = []
        self._next_id = 1
        self._by_varname: dict[str, str] = {}
        self.rect = [float(x) for x in rect]

    def add(
        self,
        *,
        maxclass: str = "newobj",
        text: str | None = None,
        varname: str | None = None,
        patching_rect: list | tuple | None = None,
        numinlets: int | None = None,
        numoutlets: int | None = None,
        outlettype: list[str] | None = None,
        **extras: Any,
    ) -> str:
        """Add a box. Returns the generated id (e.g. "obj-3")."""
        obj_id = f"obj-{self._next_id}"
        self._next_id += 1

        if numinlets is None or numoutlets is None:
            ni, no = infer_io(maxclass, text)
            if numinlets is None:
                numinlets = ni
            if numoutlets is None:
                numoutlets = no

        if outlettype is None:
            outlettype = infer_outlettype(maxclass, text)

        box: dict[str, Any] = {
            "id": obj_id,
            "maxclass": maxclass,
            "numinlets": int(numinlets),
            "numoutlets": int(numoutlets),
        }
        if outlettype:  # skip empty lists — Max omits them for objects with no outlets
            box["outlettype"] = outlettype
        if patching_rect is not None:
            box["patching_rect"] = [float(x) for x in patching_rect]
        if text is not None:
            box["text"] = text
        if varname:
            box["varname"] = varname
            self._by_varname[varname] = obj_id
        box.update(extras)
        self._boxes.append({"box": box})
        return obj_id

    def connect(
        self,
        src: str,
        dst: str,
        src_outlet: int = 0,
        dst_inlet: int = 0,
    ) -> None:
        """Connect src (varname or id) outlet to dst (varname or id) inlet."""
        src_id = self._by_varname.get(src, src)
        dst_id = self._by_varname.get(dst, dst)
        self._lines.append(
            {
                "patchline": {
                    "source": [src_id, src_outlet],
                    "destination": [dst_id, dst_inlet],
                }
            }
        )

    def to_dict(self) -> dict:
        lines_with_order = self._add_line_order(self._lines)
        return {
            "patcher": {
                "fileversion": 1,
                "appversion": DEFAULT_APP,
                "classnamespace": "box",
                "rect": self.rect,
                "autosave": 0,
                "boxes": self._boxes,
                "lines": lines_with_order,
            }
        }

    @staticmethod
    def _add_line_order(lines: list[dict]) -> list[dict]:
        """Max writes an 'order' field on lines that share a source outlet,
        so execution is right-to-left (higher inlet index = lower order)."""
        from collections import defaultdict

        groups: dict[tuple, list[int]] = defaultdict(list)
        for idx, entry in enumerate(lines):
            p = entry["patchline"]
            key = (p["source"][0], p["source"][1])
            groups[key].append(idx)

        result: list[dict] = []
        seen_index: dict[int, dict] = {}
        for key, indices in groups.items():
            if len(indices) < 2:
                continue
            # Sort by dst inlet descending so rightmost (highest inlet) gets order=0
            sorted_idxs = sorted(
                indices,
                key=lambda i: lines[i]["patchline"]["destination"][1],
                reverse=True,
            )
            for order, i in enumerate(sorted_idxs):
                # Shallow-copy the line and inject order
                entry = lines[i]
                new_entry = {"patchline": {**entry["patchline"], "order": order}}
                seen_index[i] = new_entry

        for idx, entry in enumerate(lines):
            result.append(seen_index.get(idx, entry))
        return result

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=4)
