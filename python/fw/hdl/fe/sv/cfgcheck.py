"""The static FW-E-CFG-WRITE check (protocols.md, "Configuration").

A sink's ``cfg`` belongs to the runtime. fe-sv walks every method of
each elaborated component's classes, whether elaboration ran it or not, and
reports a write whose target is rooted at a sink's ``cfg``: an
assignment (or output argument), an increment or decrement, or a ``ref``
argument. A ``const ref`` argument is a read.
"""
from __future__ import annotations

from typing import Any, Optional

from pyslang.ast import VariableFlags
from zuspec.fe.sv.interp.values import unwrap

from . import types as ty
from .objects import CFG_CONS, FwCfg, FwComponent, FwEndpoint

_INCDEC = {"Preincrement", "Predecrement", "Postincrement", "Postdecrement"}
_SELECT = {"MemberAccess", "ElementSelect", "RangeSelect"}
_SINKS = {"fw_cfg_sink", "fw_port_cfg_sink", "fw_export_cfg_sink", "fw_socket_cfg_sink"}


def _kind(e) -> str:
    k = getattr(e, "kind", None)
    return k.name if k is not None else ""


def _cfg_root(e) -> Optional[Any]:
    """The sink handle expression of a target rooted at ``<handle>.cfg``."""
    while e is not None:
        k = _kind(e)
        if k == "Conversion":
            e = e.operand
            continue
        if k not in _SELECT:
            return None
        if k == "MemberAccess" and e.member.name == "cfg":
            t = ty.canon(getattr(e.value, "type", None))
            if getattr(t, "isClass", False) and ty.api_name(t) in _SINKS:
                return e.value
        e = e.value
    return None


def _value(e, comp: FwComponent) -> Any:
    """The interpreter value a handle expression names in ``comp``, or None."""
    k = _kind(e)
    try:
        if k == "Conversion":
            return _value(e.operand, comp)
        if k == "NamedValue":
            sym = e.symbol
            if sym.name == "this":
                return comp.sv
            if type(sym).__name__ == "ClassPropertySymbol":
                return unwrap(comp.sv).get(sym.name, None)
            return None
        if k == "MemberAccess":
            base = _value(e.value, comp)
            return None if base is None else unwrap(base).get(e.member.name, None)
    except Exception:
        return None
    return None


def _ref_target(formal) -> bool:
    """A formal that writes its actual: ``ref`` (not ``const ref``)."""
    d = getattr(getattr(formal, "direction", None), "name", "")
    if d != "Ref":
        return False
    return not int(getattr(formal, "flags", 0)) & int(VariableFlags.Const)


def check_writes(rt) -> None:
    seen = set()

    def report(comp: FwComponent, h, how: str) -> None:
        o = rt.fw(_value(h, comp))
        sink = ((isinstance(o, FwCfg) and o.kind == CFG_CONS)
                or (isinstance(o, FwEndpoint) and o.cfg == "sink"))
        path = o.path if sink else comp.path
        if path not in seen:
            seen.add(path)
            rt.error("FW-E-CFG-WRITE", path, f"{how} writes a sink's cfg (in {comp.path})")

    def scan(comp: FwComponent, sub) -> None:
        def visit(n):
            k = _kind(n)
            if k == "Assignment":
                h = _cfg_root(n.left)
                if h is not None:
                    report(comp, h, "an assignment")
            elif k == "UnaryOp" and n.op.name in _INCDEC:
                h = _cfg_root(n.operand)
                if h is not None:
                    report(comp, h, "an increment or decrement")
            elif k == "Call" and not n.isSystemCall:
                sub_ = n.subroutine
                for f, a in zip(getattr(sub_, "arguments", []), n.arguments):
                    if _ref_target(f):
                        h = _cfg_root(a)
                        if h is not None:
                            report(comp, h, f"ref argument {f.name!r} of {sub_.name}")
            return True
        sub.visit(visit)

    done = set()
    for comp in sorted((o for o in rt._by_sv.values() if isinstance(o, FwComponent)),
                       key=lambda o: o.seq):
        for cls in ty.class_chain(comp.cls):
            if ty.is_api_class(cls):
                break
            for m in cls:
                if type(m).__name__ == "SubroutineSymbol" and (id(comp), id(m)) not in done:
                    done.add((id(comp), id(m)))
                    scan(comp, m)
