"""``<api>_txn``: a virtual class for one call of an API, with pure virtual
``sprint()`` and ``compare(o, output diff)``; and per method ``<m>`` a class
``<api>_<m>_txn extends <api>_txn`` with a ``rand`` field per argument
(inputs and outputs, their types kept) and ``__ret`` for a function's
return value.

A bundle's channel APIs are already per direction, so ``<p>_req_if_txn`` and
``<p>_rsp_if_txn`` are its request and response transactions: no alias is
declared (a package-level typedef cannot keep the bundle's parameters).

``<api>_txn_sink`` is an interface class with ``put(<api>_txn t)``;
``<api>_txn_listener extends <api>_listener_base`` holds a sink (its
``new(sink)``) and, at each ``<m>_post``, builds an ``<api>_<m>_txn`` from
the arguments and ``__ret`` and puts it to the sink.

``<api>_txn_driver`` holds an ``<api>`` handle (its ``new(api)``); its task
``drive(<api>_txn t)`` casts ``t`` to each method's transaction and, on a
match, calls that method with the fields, storing outputs and ``__ret``
back in the transaction."""
from __future__ import annotations

from typing import List

import zuspec.ir.core as ir

from . import irb
from .kit import Kit, KitApi
from .listener import RET, _args, _methods, listener_base_name

try:
    from zuspec.ir.core import SignalDirection
except ImportError:                         # pragma: no cover
    from zuspec.ir.core.fields import SignalDirection

_STR = ir.DataTypeString
_BIT = ir.DataTypeInt(bits=1, signed=False)
_THAT = "that"
_TXN = "__txn"


def txn_name(api: KitApi) -> str:
    return api.name + "_txn"


def method_txn_name(api: KitApi, m: str) -> str:
    return f"{api.name}_{m}_txn"


def sink_name(api: KitApi) -> str:
    return api.name + "_txn_sink"


def txn_listener_name(api: KitApi) -> str:
    return api.name + "_txn_listener"


def txn_driver_name(api: KitApi) -> str:
    return api.name + "_txn_driver"


def _fields(fn: ir.Function) -> List[ir.Field]:
    out = [irb.field(a.arg, a.datatype) for a in _args(fn)]
    if fn.returns is not None and not (fn.is_task or fn.is_async):
        out.append(irb.field(RET, fn.returns))
    for f in out:
        f.rand_kind = "rand"
    return out


def _spec(dt) -> str:
    # An integer prints in hex; anything else (a struct, a type parameter)
    # as ``%p``.
    return "0x%0h" if isinstance(dt, ir.DataTypeInt) else "%p"


def _fmt(args: List[ir.Expr]) -> ir.Expr:
    return ir.ExprCall(func=ir.ExprRefUnresolved(name="$sformatf"), args=args)


def _sprint(m: str, fields: List[ir.Field]) -> ir.Function:
    if not fields:
        return irb.method("sprint", [], [irb.ret(irb.const(f"{m}()"))], returns=_STR())
    fmt = f"{m}(" + " ".join(f"{f.name}={_spec(f.datatype)}" for f in fields) + ")"
    return irb.method("sprint", [], [irb.ret(_fmt(
        [irb.const(fmt)] + [irb.ref(f.name) for f in fields]))], returns=_STR())


def _compare_proto(base_ref: ir.DataType) -> ir.Function:
    return irb.method("compare", [irb.arg("o", base_ref),
                                  irb.arg("diff", _STR(), SignalDirection.OUTPUT)],
                      returns=_BIT)


def _compare(m: str, cls_ref: ir.DataType, base_ref: ir.DataType,
             fields: List[ir.Field]) -> ir.Function:
    # Guess: ``!=`` as in payloads (gap G7): an X or Z field is not reported.
    that, diff = irb.ref(_THAT), irb.ref("diff")
    cast = ir.ExprCall(func=ir.ExprRefUnresolved(name="$cast"), args=[that, irb.ref("o")])
    body: List[ir.Stmt] = [
        irb.local(_THAT, cls_ref),
        irb.assign(diff, irb.const("")),
        irb.if_(ir.ExprUnary(op=ir.UnaryOp.Not, operand=cast), [
            irb.assign(diff, irb.const(f" not a {m} transaction")), irb.ret(irb.const(0))]),
        irb.if_(irb.eq(that, ir.ExprNull()), [
            irb.assign(diff, irb.const(" null")), irb.ret(irb.const(0))])]
    for f in fields:
        a, b, s = irb.ref(f.name), irb.attr(that, f.name), _spec(f.datatype)
        ne = ir.ExprCompare(left=a, ops=[ir.CmpOp.NotEq], comparators=[b])
        msg = _fmt([irb.const(f" {f.name}: {s} != {s}"), a, b])
        body.append(irb.if_(ne, [irb.assign(diff, ir.ExprConcat(values=[diff, msg]))]))
    body.append(irb.ret(irb.eq(diff, irb.const(""))))
    fn = _compare_proto(base_ref)
    fn.body = body
    return fn


def decls(api: KitApi, kit: Kit) -> List[ir.DataType]:
    protos = [irb.method("sprint", [], returns=_STR())]
    base = irb.template(txn_name(api), api.tmpl, ir.DataTypeClass(
        super=None, is_abstract=True, functions=protos))
    base_ref = irb.self_ref(base)
    protos.append(_compare_proto(base_ref))
    for fn in protos:
        fn.metadata = {"pure": True}
    out = [base]
    for fn in api.proto.methods:
        fields = _fields(fn)
        cls = ir.DataTypeClass(super=base_ref, fields=fields)
        t = irb.template(method_txn_name(api, fn.name), api.tmpl, cls)
        cls.functions = [_sprint(fn.name, fields),
                         _compare(fn.name, irb.self_ref(t), base_ref, fields)]
        out.append(t)
    sink = irb.template(sink_name(api), api.tmpl, ir.DataTypeProtocol(
        methods=[irb.method("put", [irb.arg("t", base_ref)])]))
    sink_ref = irb.self_ref(sink)
    return out + [sink, _txn_listener(api, sink_ref, out[1:]),
                  _txn_driver(api, base_ref, out[1:])]


def _post(post: ir.Function, txn_ref: ir.DataType) -> ir.Function:
    # ``<api>_<m>_txn __txn = new; __txn.<a> = <a>; ...; m_sink.put(__txn);``
    # (``__txn`` cannot clash with an argument, as ``t`` could.)
    t = irb.ref(_TXN)
    body: List[ir.Stmt] = [irb.local(_TXN, txn_ref, ir.ExprNew(datatype=txn_ref))]
    body += [irb.assign(irb.attr(t, a.arg), irb.ref(a.arg)) for a in _args(post)]
    body.append(irb.call(irb.attr(irb.ref("m_sink"), "put"), [t]))
    return irb.clone_method(post, body)


def _txn_listener(api: KitApi, sink_ref: ir.DataType,
                  txns: List[ir.DataType]) -> ir.DataType:
    base = irb.self_ref(irb.template(listener_base_name(api), api.tmpl,
                                     ir.DataTypeClass(super=None)))
    ctor = irb.method("new", [irb.arg("sink", sink_ref)],
                      [irb.assign(irb.ref("m_sink"), irb.ref("sink"))])
    posts = [fn for fn in _methods(api) if fn.name.endswith("_post")]
    cls = ir.DataTypeClass(super=base, fields=[irb.field("m_sink", sink_ref)],
                           functions=[ctor] + [_post(p, irb.self_ref(t))
                                               for p, t in zip(posts, txns)])
    return irb.template(txn_listener_name(api), api.tmpl, cls)


def _txn_driver(api: KitApi, base_ref: ir.DataType,
                txns: List[ir.DataType]) -> ir.DataType:
    # ``<api>_<m>_txn __<m>; ... if ($cast(__<m>, t)) begin
    # [__<m>.__ret =] m_api.<m>(__<m>.<a>...); return; end`` per method:
    # outputs are written through the field references.
    ctor = irb.method("new", [irb.arg("api", api.ref)],
                      [irb.assign(irb.ref("m_api"), irb.ref("api"))])
    decls: List[ir.Stmt] = []
    body: List[ir.Stmt] = []
    for fn, t in zip(api.proto.methods, txns):
        name = "__" + fn.name
        v = irb.ref(name)
        decls.append(irb.local(name, irb.self_ref(t)))
        call = ir.ExprCall(func=irb.attr(irb.ref("m_api"), fn.name),
                           args=[irb.attr(v, a.arg) for a in _args(fn)])
        if fn.returns is not None and not (fn.is_task or fn.is_async):
            stmt = irb.assign(irb.attr(v, RET), call)
        else:
            stmt = ir.StmtExpr(expr=call)
        cast = ir.ExprCall(func=ir.ExprRefUnresolved(name="$cast"), args=[v, irb.ref("t")])
        body.append(irb.if_(cast, [stmt, irb.ret()]))
    drive = irb.method("drive", [irb.arg("t", base_ref)], decls + body, task=True)
    cls = ir.DataTypeClass(super=None, fields=[irb.field("m_api", api.ref)],
                           functions=[ctor, drive])
    return irb.template(txn_driver_name(api), api.tmpl, cls)
