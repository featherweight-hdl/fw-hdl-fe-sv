"""``<api>_base``: a virtual class implementing an API with default bodies,
so a client overrides only the methods it needs. Each method reports an
error (it is not implemented) and zeroes its outputs and return value."""
from __future__ import annotations

from typing import List

import zuspec.ir.core as ir

from . import irb
from .kit import Kit, KitApi

try:
    from zuspec.ir.core import SignalDirection
except ImportError:                         # pragma: no cover
    from zuspec.ir.core.fields import SignalDirection


def base_name(api: KitApi) -> str:
    return api.name + "_base"


def _default(api: KitApi, fn: ir.Function) -> ir.Function:
    # A type parameter has no zero to write: an output or return value of
    # that type keeps the default it starts with in SV.
    params = {p.name for p in api.tmpl}

    def _set(dt) -> bool:
        return not (isinstance(dt, ir.DataTypeRef) and dt.ref_name in params)

    body: List[ir.Stmt] = [irb.sys_call("$error", [
        irb.const(f"{base_name(api)}::{fn.name}: not implemented")])]
    for a in (fn.args.args if fn.args is not None else []):
        if (a.direction in (SignalDirection.OUTPUT, SignalDirection.INOUT)
                and _set(a.datatype)):
            body.append(irb.assign(irb.ref(a.arg), irb.zero(a.datatype)))
    if (fn.returns is not None and not (fn.is_task or fn.is_async)
            and _set(fn.returns)):
        body.append(irb.ret(irb.zero(fn.returns)))
    return irb.clone_method(fn, body)


def decls(api: KitApi, kit: Kit) -> List[ir.DataType]:
    cls = ir.DataTypeClass(super=None, is_abstract=True, implements=[api.ref],
                           functions=[_default(api, fn) for fn in api.proto.methods])
    return [irb.template(base_name(api), api.tmpl, cls)]
