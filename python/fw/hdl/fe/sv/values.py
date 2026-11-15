"""Interpreter values, as FeRt reads and writes them.

The one place that knows zuspec-fe-sv's value representation: integral
values are Python ints, strings are str, class handles are ``Obj`` (None
for null), unpacked structs are ``Struct``, and arrays are lists.
"""
from __future__ import annotations

from typing import Any, List

from zuspec.fe.sv.interp.values import Struct, Sym, copy_value, str_to_int, strip, unwrap


from zuspec.fe.sv.interp import Cell as Ref   # a value passed by ref: get()/set(v)


def copy(v: Any) -> Any:
    return copy_value(v)


def fields(v: Any) -> List[str]:
    return list(v.keys()) if isinstance(v, Struct) else []


def field(v: Any, name: str) -> Any:
    return v[name]


def equal(a: Any, b: Any) -> bool:
    """Value equality, ignoring tracing (a ``Sym`` is its value)."""
    a, b = strip(a), strip(b)
    return a is b if hasattr(a, "cls") or hasattr(b, "cls") else a == b


def set_field(obj, name: str, value: Any) -> None:
    obj.set(name, value)


def elements(v: Any) -> List[Any]:
    return list(unwrap(v))


def to_int(v: Any) -> int:
    v = unwrap(v)
    if isinstance(v, str):
        return str_to_int(v)
    return int(v)


def to_str(v: Any) -> str:
    v = unwrap(v)
    if isinstance(v, int):
        from zuspec.fe.sv.interp.values import int_to_str
        return int_to_str(v)
    return str(v)
