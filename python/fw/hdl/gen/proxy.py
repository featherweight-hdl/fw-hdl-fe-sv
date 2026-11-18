"""``<api>_proxy``: implements an API by forwarding each call to
``m_target``, calling its listeners' ``<m>_pre`` before and ``<m>_post``
after."""
from __future__ import annotations

from typing import List

import zuspec.ir.core as ir

from . import irb
from .kit import Kit, KitApi
from .listener import RET, list_mgmt, listener_decl


def proxy_name(api: KitApi) -> str:
    return api.name + "_proxy"


def _forward(fn: ir.Function) -> ir.Function:
    try:
        from zuspec.ir.core import SignalDirection
    except ImportError:                     # pragma: no cover
        from zuspec.ir.core.fields import SignalDirection
    q = irb.ref("m_listeners")
    args = list(fn.args.args) if fn.args is not None else []
    ins = [irb.ref(a.arg) for a in args
           if a.direction in (None, SignalDirection.INPUT, SignalDirection.INOUT)]
    post = irb.arg_refs(fn)
    ret = fn.returns is not None and not (fn.is_task or fn.is_async)

    def _each(name, refs):
        return irb.foreach("i", q, [irb.call(
            irb.attr(irb.index(q, irb.ref("i")), name), refs)])

    call = ir.ExprCall(func=irb.attr(irb.ref("m_target"), fn.name), args=irb.arg_refs(fn))
    body: List[ir.Stmt] = []
    if ret:
        body.append(irb.local(RET, fn.returns))
        post.append(irb.ref(RET))
    body.append(_each(fn.name + "_pre", ins))
    body.append(irb.assign(irb.ref(RET), call) if ret else ir.StmtExpr(expr=call))
    body.append(_each(fn.name + "_post", post))
    if ret:
        body.append(irb.ret(irb.ref(RET)))
    return irb.clone_method(fn, body)


def decls(api: KitApi, kit: Kit) -> List[ir.DataType]:
    field, mgmt = list_mgmt(irb.self_ref(listener_decl(api)))
    ctor = irb.method("new", [irb.arg("target", api.ref)],
                      [irb.assign(irb.ref("m_target"), irb.ref("target"))])
    ctor.args.defaults = [ir.ExprNull()]
    cls = ir.DataTypeClass(super=None, implements=[api.ref],
                           fields=[irb.field("m_target", api.ref), field],
                           functions=[ctor] + mgmt
                           + [_forward(fn) for fn in api.proto.methods])
    return [irb.template(proxy_name(api), api.tmpl, cls)]
