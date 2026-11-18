"""``<api>_listener``: an interface class with ``<m>_pre(inputs)``, called
when a method of the API is called, and ``<m>_post(args[, __ret])``, called
when it completes; all void functions, every argument an input.
``<api>_listener_base`` implements it with empty bodies;
``<api>_listener_fanout`` forwards each call to the listeners it holds."""
from __future__ import annotations

from typing import List

import zuspec.ir.core as ir

from . import irb
from .kit import Kit, KitApi

try:
    from zuspec.ir.core import SignalDirection
except ImportError:                         # pragma: no cover
    from zuspec.ir.core.fields import SignalDirection

RET = "__ret"


def listener_name(api: KitApi) -> str:
    return api.name + "_listener"


def listener_base_name(api: KitApi) -> str:
    return api.name + "_listener_base"


def fanout_name(api: KitApi) -> str:
    return api.name + "_listener_fanout"


def _args(fn: ir.Function) -> List[ir.Arg]:
    return list(fn.args.args) if fn.args is not None else []


def _methods(api: KitApi) -> List[ir.Function]:
    out = []
    for fn in api.proto.methods:
        ins = [irb.arg(a.arg, a.datatype) for a in _args(fn)
               if a.direction in (None, SignalDirection.INPUT, SignalDirection.INOUT)]
        post = [irb.arg(a.arg, a.datatype) for a in _args(fn)]
        if fn.returns is not None and not (fn.is_task or fn.is_async):
            post.append(irb.arg(RET, fn.returns))
        out += [irb.method(fn.name + "_pre", ins), irb.method(fn.name + "_post", post)]
    return out


def list_mgmt(lst_ref: ir.DataType) -> tuple:
    """The ``m_listeners[$]`` field and ``add_listener``/``remove_listener``."""
    q = irb.ref("m_listeners")
    add = irb.method("add_listener", [irb.arg("l", lst_ref)],
                     [irb.call(irb.attr(q, "push_back"), [irb.ref("l")])])
    rem = irb.method("remove_listener", [irb.arg("l", lst_ref)], [
        irb.foreach("i", q, [irb.if_(irb.eq(irb.index(q, irb.ref("i")), irb.ref("l")), [
            irb.call(irb.attr(q, "delete"), [irb.ref("i")]),
            irb.ret()])])])
    return (irb.field("m_listeners", ir.DataTypeList(element_type=lst_ref)), [add, rem])


def listener_decl(api: KitApi) -> ir.DataType:
    return irb.template(listener_name(api), api.tmpl,
                        ir.DataTypeProtocol(methods=_methods(api)))


def decls(api: KitApi, kit: Kit) -> List[ir.DataType]:
    lst = listener_decl(api)
    lst_ref = irb.self_ref(lst)
    base = ir.DataTypeClass(super=None, is_abstract=True, implements=[lst_ref],
                            functions=[irb.clone_method(fn, []) for fn in _methods(api)])
    field, mgmt = list_mgmt(lst_ref)
    q = irb.ref("m_listeners")
    fwd = [irb.clone_method(fn, [irb.foreach("i", q, [irb.call(
        irb.attr(irb.index(q, irb.ref("i")), fn.name), irb.arg_refs(fn))])])
        for fn in _methods(api)]
    fanout = ir.DataTypeClass(super=None, implements=[lst_ref], fields=[field],
                              functions=mgmt + fwd)
    return [lst,
            irb.template(listener_base_name(api), api.tmpl, base),
            irb.template(fanout_name(api), api.tmpl, fanout)]
