"""``<api>_imp #(<params>, type IMP)``: implements an API by forwarding
every call to ``m_imp``, an ``IMP``: any class with the API's methods. A
component with one export per API type needs no hand-written adapter."""
from __future__ import annotations

from typing import List

import zuspec.ir.core as ir

from . import irb
from .kit import Kit, KitApi


def imp_name(api: KitApi) -> str:
    return api.name + "_imp"


def _forward(fn: ir.Function) -> ir.Function:
    call = ir.ExprCall(func=irb.attr(irb.ref("m_imp"), fn.name), args=irb.arg_refs(fn))
    if fn.returns is not None and not (fn.is_task or fn.is_async):
        return irb.clone_method(fn, [irb.ret(call)])
    return irb.clone_method(fn, [ir.StmtExpr(expr=call)])


def decls(api: KitApi, kit: Kit) -> List[ir.DataType]:
    imp_t = ir.DataTypeRef(ref_name="IMP")
    params = list(api.tmpl) + [ir.TemplateParamType(
        name="IMP", kind=ir.TemplateParamKind.TYPE, default_value=ir.DataTypeInt(bits=32, signed=True))]
    ctor = irb.method("new", [irb.arg("imp", imp_t)],
                      [irb.assign(irb.ref("m_imp"), irb.ref("imp"))])
    cls = ir.DataTypeClass(super=None, implements=[api.ref],
                           fields=[irb.field("m_imp", imp_t)],
                           functions=[ctor] + [_forward(fn) for fn in api.proto.methods])
    return [irb.template(imp_name(api), params, cls)]
