"""The kit generators' only path to SV: zuspec-be-sv."""
from __future__ import annotations

from typing import Sequence

from zuspec.be.sv.ir import SVRawItem
from zuspec.be.sv.ir.core_to_sv import translate_package
from zuspec.be.sv.ir.sv_emit import SVEmitter


def _sv_items(decl) -> list:
    """A decl's raw-item lines (``xtor.py``'s mechanism for the
    construction/wiring code and macro lines be-sv's IR cannot express
    yet): ``decl.metadata["sv_items"]``, or its body's when ``decl`` is a
    template wrapping a class."""
    target = getattr(decl, "body", None) if hasattr(decl, "body") else None
    for d in (decl, target):
        meta = getattr(d, "metadata", None)
        if meta and meta.get("sv_items"):
            return meta["sv_items"]
    return []


def to_sv(pkg_name: str, decls: Sequence, imports: Sequence[str] = ()) -> str:
    """``package pkg_name`` importing ``imports`` and declaring ``decls``
    (core IR classes, protocols and templates). A decl with
    ``metadata["sv_items"]`` (a list of raw-line blocks) has them appended
    to its translated ``SVClass``, matched by name, after translation."""
    decls = list(decls)
    extra = {d.name: _sv_items(d) for d in decls if _sv_items(d)}
    pkg = translate_package(pkg_name, {}, decls, imports=list(imports))
    if extra:
        for item in pkg.items:
            blocks = extra.get(getattr(item, "name", None))
            if blocks:
                item.items.extend(SVRawItem(lines=lines) for lines in blocks)
    sv = SVEmitter().emit_one(pkg)
    return sv if sv.endswith("\n") else sv + "\n"
