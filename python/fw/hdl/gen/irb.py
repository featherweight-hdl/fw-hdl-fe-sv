"""Builders for the core IR the kit generators emit. Generators build IR
only; ``emit.py`` turns it into SV through zuspec-be-sv."""
from __future__ import annotations

import dataclasses as dc
from typing import List, Optional, Sequence

import zuspec.ir.core as ir


def template(name: str, params: Sequence[ir.TemplateParam], body: ir.DataType) -> ir.DataType:
    """``body`` named ``name``, parameterized by ``params`` if there are any."""
    body.name = name
    if not params:
        return body
    return ir.DataTypeParameterized(name=name, template_params=list(params), body=body)


def field(name: str, dt: ir.DataType) -> ir.Field:
    return ir.Field(name=name, datatype=dt)


def ref(name: str) -> ir.Expr:
    return ir.ExprRefLocal(name=name)


def attr(value: ir.Expr, name: str) -> ir.Expr:
    return ir.ExprAttribute(value=value, attr=name)


def call(func: ir.Expr, args: Sequence[ir.Expr] = ()) -> ir.Stmt:
    """``func(args);`` as a statement."""
    return ir.StmtExpr(expr=ir.ExprCall(func=func, args=list(args)))


def foreach(index: str, coll: ir.Expr, body: List[ir.Stmt]) -> ir.Stmt:
    """``foreach (coll[index]) body``."""
    return ir.StmtForeach(target=ref(index), iter=coll, body=body,
                          index_var=ref(index))


def clone_method(fn: ir.Function, body: List[ir.Stmt],
                 name: Optional[str] = None) -> ir.Function:
    """``fn``'s signature with a new body: an implementation of an API
    method."""
    return dc.replace(fn, name=name or fn.name, body=list(body), metadata=dict(fn.metadata))


def arg_refs(fn: ir.Function) -> List[ir.Expr]:
    """References to ``fn``'s arguments, in order: to forward a call."""
    return [ref(a.arg) for a in (fn.args.args if fn.args is not None else [])]


def zero(dt: Optional[ir.DataType]) -> ir.Expr:
    """The default (zero) value of ``dt``: ``null`` for a class handle, ``0``
    otherwise. Guess: a struct or unpacked-array payload needs a fill
    (``'{default: '0}``) be-sv cannot express yet (gap G4)."""
    if isinstance(dt, (ir.DataTypeClass, ir.DataTypeProtocol)):
        return ir.ExprNull()
    return ir.ExprConstant(value=0)


def assign(target: ir.Expr, value: ir.Expr) -> ir.Stmt:
    return ir.StmtAssign(targets=[target], value=value)


def ret(value: Optional[ir.Expr] = None) -> ir.Stmt:
    return ir.StmtReturn(value=value)


def sys_call(name: str, args: Sequence[ir.Expr] = ()) -> ir.Stmt:
    """A system task call (``$error(...)``) as a statement."""
    return call(ir.ExprRefUnresolved(name=name), args)


def const(value) -> ir.Expr:
    return ir.ExprConstant(value=value)



def self_ref(t: ir.DataType) -> ir.DataType:
    """A reference to ``t`` (as ``template`` returns it), specialized over
    its own parameters: ``name #(T)`` inside a template over ``T``."""
    if not isinstance(t, ir.DataTypeParameterized):
        return ir.DataTypeRef(ref_name=t.name)
    args = []
    for p in t.template_params:
        if isinstance(p, ir.TemplateParamType):
            args.append(ir.TemplateArgType(param_name=p.name,
                                           type_value=ir.DataTypeRef(ref_name=p.name)))
        else:
            args.append(ir.TemplateArgValue(param_name=p.name,
                                            value_expr=ir.ExprRefParam(name=p.name)))
    return ir.DataTypeSpecialized(base_template=t, template_args=args)


def method(name: str, args: Sequence[ir.Arg], body: List[ir.Stmt] = (),
           returns: Optional[ir.DataType] = None, task: bool = False) -> ir.Function:
    """``function <returns|void> name(args)``, or ``task name(args)``."""
    return ir.Function(name=name, args=ir.Arguments(args=list(args)),
                       body=list(body), returns=returns, is_task=task)


def arg(name: str, dt: ir.DataType, direction=None) -> ir.Arg:
    """An argument, ``input`` unless ``direction`` says otherwise."""
    return ir.Arg(arg=name, datatype=dt,
                  direction=direction if direction is not None else _INPUT)


def eq(a: ir.Expr, b: ir.Expr) -> ir.Expr:
    return ir.ExprCompare(left=a, ops=[ir.CmpOp.Eq], comparators=[b])


def index(value: ir.Expr, i: ir.Expr) -> ir.Expr:
    return ir.ExprSubscript(value=value, slice=i)


def if_(test: ir.Expr, body: List[ir.Stmt]) -> ir.Stmt:
    return ir.StmtIf(test=test, body=list(body), orelse=[])


try:
    from zuspec.ir.core import SignalDirection as _Dir
except ImportError:                         # pragma: no cover
    from zuspec.ir.core.fields import SignalDirection as _Dir
_INPUT = _Dir.INPUT


def local(name: str, dt: ir.DataType, value: Optional[ir.Expr] = None) -> ir.Stmt:
    """A local variable declaration: ``dt name [= value];``."""
    return ir.StmtAnnAssign(target=ref(name), annotation=ir.ExprRefUnresolved(
        name=getattr(dt, "name", None) or getattr(dt, "ref_name", "") or ""),
        value=value, ir_type=dt)
