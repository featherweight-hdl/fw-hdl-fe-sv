"""Module parameter values of externs: ``fw_value #(T)::of(v)`` and
``fw_type #(T)::get()`` (semantics/extern.md, "Parameters").

A :class:`ParamArg` keeps the type ``T`` (a slang type, typedef names
intact) and, for a value, the value (an interpreter value, possibly a
traced ``Sym``). From them come the netlist spelling, the SV source text
(for header checks), the check against a module's parameter, and the IR.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Optional

import zuspec.ir.core as ir
from zuspec.fe.sv.interp.values import unwrap

from . import types as ty


@dc.dataclass
class ParamArg:
    kind: str                   # "value" | "type"
    T: Any                      # slang type, aliases intact
    value: Any = None           # for a value: the interpreter value (maybe a Sym)
    ttype: Any = None           # for a type: T as written (a traced TType), or None


def _qual(t) -> str:
    """The package-qualified name of an enum/struct typedef, else ""."""
    a = t
    while getattr(a, "isAlias", False):
        lp = str(getattr(a, "lexicalPath", "") or "")
        if lp:
            return lp
        a = a.targetType.type
    return ""


def _enum_label(v: int, c) -> Optional[str]:
    from zuspec.fe.sv.interp.values import const_to_py
    for m in c:
        if type(m).__name__ == "EnumValueSymbol" and const_to_py(m.value, c) == v:
            return m.name
    return None


def _pkg_of(qual: str) -> str:
    return qual.rsplit("::", 1)[0] if "::" in qual else ""


def netlist_entry(a: ParamArg) -> dict:
    from .netlist import _value_text, spell
    if a.kind == "type":
        return {"type": "type", "value": spell(a.T)}
    return {"type": spell(a.T), "value": _value_text(unwrap(a.value), ty.canon(a.T))}


def sv_type(t) -> str:
    """``t`` as SV source: a typedef'd enum or struct by its qualified name,
    a vector by its canonical spelling."""
    c = ty.canon(t)
    if getattr(c, "isEnum", False) or getattr(c, "isStruct", False):
        q = _qual(t)
        if q:
            return q
    return str(c)


def sv_value(a: ParamArg) -> str:
    """The value as SV source text, typed as the module needs it."""
    if a.kind == "type":
        return sv_type(a.T)
    v, c = unwrap(a.value), ty.canon(a.T)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if getattr(c, "isEnum", False):
        lab = _enum_label(v, c)
        pkg = _pkg_of(_qual(a.T))
        return f"{pkg}::{lab}" if pkg else lab
    w = int(c.bitWidth)
    if getattr(c, "isStruct", False) or (not c.isSigned and w > 32):
        return f"{w}'h{v & ((1 << w) - 1):x}"
    return str(v)


def check(a: ParamArg, hp) -> Optional[str]:
    """Why ``a`` does not fit the module's parameter ``hp`` (a
    module_header.HeaderParam), or None."""
    if hp is None:
        return "the module has no such parameter"
    if a.kind == "type":
        return None if hp.kind == "type" else "a type for a value parameter"
    if hp.kind == "type":
        return "a value for a parameter type"
    c = ty.canon(a.T)
    is_str = bool(getattr(c, "isString", False))
    if is_str != hp.is_string:
        return "a string for a non-string parameter" if is_str else "a non-string value for a string parameter"
    is_enum = bool(getattr(c, "isEnum", False))
    if is_enum or hp.is_enum:
        if not (is_enum and hp.is_enum and _qual(a.T) == hp.qual):
            return f"an enum value fits only a parameter of the same enum type ({hp.type})"
    if getattr(c, "isStruct", False) and hp.qual and _qual(a.T) != hp.qual:
        return f"a {_qual(a.T) or 'struct'} value for a {hp.qual} parameter"
    return None


def to_ir(a: ParamArg, type_ir=None):
    """The value in the IR, as zuspec-be-sv renders it: an ``Expr`` for a
    value, a ``DataType`` for a type. ``type_ir`` maps a type (the IR
    mapper's, which gives a packed struct or enum its IR type); by default
    a typedef'd enum or struct is a ``DataTypeRef`` to its qualified name
    and any other type a ``DataTypeInt`` (two- or four-state as written)."""
    c = ty.canon(a.T)
    if a.kind == "type":
        if type_ir is not None:
            return type_ir(a.T)
        if getattr(c, "isEnum", False) or getattr(c, "isStruct", False):
            q = _qual(a.T)
            if q:
                return ir.DataTypeRef(ref_name=q)
        return ir.DataTypeInt(bits=int(c.bitWidth), signed=bool(c.isSigned),
                              four_state=bool(c.isFourState))
    v = unwrap(a.value)
    if isinstance(v, str):
        return ir.ExprConstant(value=v)
    if getattr(c, "isEnum", False):
        pkg = _pkg_of(_qual(a.T))
        lab = _enum_label(v, c)
        return ir.ExprStaticRef(path=[pkg, lab] if pkg else [lab])
    w = int(c.bitWidth)
    if getattr(c, "isStruct", False) or (not c.isSigned and w > 32):
        return ir.ExprConstant(value=v & ((1 << w) - 1), width=w)
    return ir.ExprConstant(value=v)
