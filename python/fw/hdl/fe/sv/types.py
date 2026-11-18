"""Type helpers over pyslang symbols: canonical names, class walks, lookups."""
from __future__ import annotations

from typing import Iterator, Optional

API_PACKAGE = "fw_hdl_pkg"
#: The standard library (fw-hdl-spec std/), compiled with the API.
STD_PACKAGE = "fw_hdl_std_pkg"


def canon(t):
    return t.canonicalType if t is not None and hasattr(t, "canonicalType") else t


def type_name(t, aliases: bool = False) -> str:
    """The canonical spelling of a type, as the netlist uses it: a class
    specialization is its name and its parameters' values, with type
    parameters spelled the same way, ``fifo_chain#(bit[7:0])``; any other
    type as slang prints its canonical form. With ``aliases``, a typedef
    is spelled by its name: ``test_put_if#(uart_char_t)``."""
    if aliases and type(t).__name__ == "TypeAliasType" and not _type_param_alias(t):
        return t.name
    c = canon(t)
    if c is None:
        return "?"
    if getattr(c, "isStruct", False) or getattr(c, "isEnum", False) or \
            getattr(c, "isPackedUnion", False) or getattr(c, "isUnpackedUnion", False):
        # Nominal types keep the name of the typedef nearest them, as in the
        # netlist (``fw_rv_proto#(aw_t)``).
        name = None
        while getattr(t, "isAlias", False):
            name = t.name
            t = t.targetType.type
        if name:
            return name
    t = c
    if getattr(t, "isClass", False) and t.genericClass is not None:
        vals = []
        for m in t:
            k = type(m).__name__
            if k == "TypeParameterSymbol":
                vals.append(type_name(m.targetType.type, aliases))
            elif k == "ParameterSymbol" and not m.isLocalParam:
                vals.append(str(m.value))
        return f"{t.name}#({','.join(vals)})" if vals else t.name
    return str(t).replace(" ", "")


def _type_param_alias(t) -> bool:
    """A type parameter's alias (``T`` in ``fw_put_if #(T)``): spelled by
    its value, not its name."""
    p = getattr(t, "parentScope", None)
    m = p.find(t.name) if p is not None else None
    return type(m).__name__ == "TypeParameterSymbol"


def class_chain(cls) -> Iterator:
    """``cls`` and its base classes, most derived first."""
    c = canon(cls)
    while c is not None and getattr(c, "isClass", False):
        yield c
        c = canon(c.baseClass)


def is_api_class(cls) -> bool:
    """A class declared in the API package (``fw_hdl_pkg::fw_input``)."""
    c = canon(cls)
    return bool(getattr(c, "isClass", False)) and \
        str(getattr(c, "lexicalPath", "")).startswith(API_PACKAGE + "::")


def api_name(cls) -> Optional[str]:
    """The API class's name (``fw_input``) for a class of the API, else None."""
    c = canon(cls)
    if not is_api_class(c):
        return None
    return c.genericClass.name if c.genericClass is not None else c.name


def api_base(cls, names) -> Optional[str]:
    """The first API class in ``cls``'s chain whose name is in ``names``."""
    for c in class_chain(cls):
        n = api_name(c)
        if n in names:
            return n
    return None


def find_member(cls, name: str, kind: Optional[str] = None):
    """``name`` declared in ``cls`` or a base class, nearest first. A method
    prototype resolves to its subroutine."""
    for c in class_chain(cls):
        m = c.find(name)
        if m is None:
            continue
        k = type(m).__name__
        if k == "TransparentMemberSymbol":
            continue
        if k == "MethodPrototypeSymbol":
            m = m.subroutine
            k = type(m).__name__ if m is not None else None
        if m is None:
            continue
        if kind is None or k == kind:
            return m, c
    return None, None


def declares(cls, name: str) -> bool:
    m = canon(cls).find(name)
    return m is not None and type(m).__name__ != "TransparentMemberSymbol"


def type_param(cls, name: str, raw: bool = False):
    """The value of type parameter ``name`` of a class specialization;
    ``raw`` keeps a typedef name (``pair_t``) instead of resolving it."""
    m = canon(cls).find(name)
    if m is None or type(m).__name__ != "TypeParameterSymbol":
        return None
    t = m.targetType.type
    return t if raw else canon(t)


def implements(cls, iface_name: str) -> bool:
    for c in class_chain(cls):
        for i in c.implementedInterfaces:
            ic = canon(i)
            if ic.name == iface_name or (ic.genericClass is not None
                                         and ic.genericClass.name == iface_name):
                return True
    return False


def type_param_name(t, cls) -> Optional[str]:
    """If type ``t`` (as written: a type parameter is a ``TypeAliasType``)
    is type parameter ``T`` of class ``cls`` -- the parameter itself, or
    the same alias ``cls`` was specialized with (slang substitutes
    ``rv_fifo #(T)``'s ``T`` by the type the creator named), or a type
    parameter of another class specialized with it (``fw_rv_if::T`` of
    ``fw_rv_if #(T)``) -- the name of ``cls``'s parameter; else None."""
    cls = canon(cls)
    vals = [(m.name, m.targetType.type) for m in cls
            if type(m).__name__ == "TypeParameterSymbol" and not m.isLocalParam]
    for _ in range(32):
        if t is None or not getattr(t, "isAlias", False):
            return None
        for name, v in vals:
            if v is t or (getattr(v, "isAlias", False) and v == t):
                return name
        k = getattr(t, "parentScope", None)
        m = k.find(t.name) if k is not None else None
        if m is None or type(m).__name__ != "TypeParameterSymbol":
            return None
        mine = cls.find(t.name)
        if mine is not None and (mine is m or mine == m):
            return t.name
        t = m.targetType.type
    return None
