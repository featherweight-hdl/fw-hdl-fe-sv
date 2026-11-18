"""The elaborated design as Zuspec IR (core-layer1.md §6.3, §6.6).

The output is *structural* IR: components with ports, local signals,
bundle fields in a role, child instance fields, and ``bind_map``
connections; externs as ``DataTypeExtern`` with their pin map. Flattening
bundles and turning connections into nets and instances is a Zuspec pass
(``zuspec.synth.passes.struct_flatten``), not fe-sv's job.

Parameterized emission (§6.6): each component is mapped *generally* when
it can be, i.e. one IR type per class, whatever its parameter values:

- its integral type parameters, its value class parameters (a
  struct-valued one, a *shape*, flattened: ``S.data_w`` is ``S_data_w``)
  and the integral fields of its params struct (nested struct fields
  joined by ``_``) are ``template_params``. A port's or bundle member's
  type is traced as written (``fw_input #(bit [W-1:0])``): a
  ``DataTypeRef`` to a type parameter, or a ``DataTypeInt`` whose
  ``width_expr`` is an expression of the parameters. A bundle type is
  generic over its own class parameters (``fw_rv_if`` with ``T``), its
  field in a component carrying ``inst_params``;
- each child carries ``inst_params``: the values its creator computed, and
  the class parameters it was created with, as expressions over the
  component's parameters and genvars. The values come from running the
  component's code with its parameters traced (``zuspec.fe.sv.interp``
  tracing: every parameter is a ``Sym``);
- children, ports and bundles created in a loop over a parameter, named
  ``base[i]``, are an array: one field ``base`` of a ``DataTypeArray`` type
  whose ``size_expr`` is the loop bound (a child array's ``index_var`` is
  the loop variable);
- connections made in a loop over parameters go in a ``GenerateFor``;
  children and connections under an ``if`` on parameters go in a
  ``GenerateIf``, both arms. The arm that did not run at the elaborated
  values is *explored*: the component's class is elaborated again on its
  own, with that arm forced (``FeRt.explore``), and the runs are merged;
  ``if``\\ s with the same condition (one in ``build()``, one in
  ``connect()``) become one block;
- an extern is generic over its class parameters too: its
  ``extern_params`` are expressions over them, and its instance's
  ``inst_params`` say what they are.

Domains (core-domains-plan §6.2, the Z6 shapes): each kind used is a
``DataTypeDomainKind`` in ``type_m``; a component's domain objects,
associations, drivers and waivers are its ``domains``,
``domain_assocs``, ``domain_drivers`` and ``domain_waivers``; an extern's
domain pins are ``ExternPin``\\ s with ``domain_kind``. A domain the
component's code reached is written as it can be reached from the
component: its own domain object by name, else the resolution of one of
its associations (its own, an interface's or a child's) that gives it;
the ref the code read it through (``get``), when fe-sv recorded one. An
association of a child created under a branch on parameters goes in the
child's generate if; any other domain statement under a loop or branch on
parameters makes the component a specialization (§6.1).

A component whose structure cannot be written that way (the
*specialization rule*: an override from a farther ancestor, a
constructor factory that picks another realization, control flow the
tracing cannot follow, ...) is mapped *concretely*: its parameters baked
in, as in M4/M5. An informational diagnostic (``FW-I-EMIT-SPEC``) names
what forced it; with ``require_param`` it is an error
(``FW-E-EMIT-SPEC``).

Equal IR types are shared. The first type made for a class takes the
class's own name (``fifo_chain``); others get ``__1``, ``__2``.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

import zuspec.ir.core as ir

from .params import to_ir as param_ir
from zuspec.fe.sv.interp.trace import leaves
from zuspec.fe.sv.interp.values import Sym, strip

from . import types as ty
from .objects import (BUNDLE, BUNDLE_KINDS, MIRROR, MONITOR, CFG_CONS, CFG_PUB, Diag,
                      FwBundle, FwCfg, FwComponent, FwDomain, FwEndpoint, FwObject, FwSignal)
from . import domains as dm

_ROLE = {BUNDLE: ir.BundleRole.INITIATOR, MIRROR: ir.BundleRole.MIRROR,
         MONITOR: ir.BundleRole.MONITOR}

SPEC_INFO = "FW-I-EMIT-SPEC"
SPEC_ERROR = "FW-E-EMIT-SPEC"

#: At most this many elaborations of one component (itself and the runs that
#: explore arms of its branches).
MAX_RUNS = 16


class NotParam(Exception):
    """The component cannot be mapped generally; the message says why."""


# ----------------------------------------------------------------- types

def _int_type(t) -> ir.DataTypeInt:
    """An integral type as a ``DataTypeInt``: its width, signedness and
    whether it is four-state (``logic``) or two-state (``bit``)."""
    t = ty.canon(t)
    if t is None:
        return ir.DataTypeInt(bits=1, signed=False)
    return ir.DataTypeInt(bits=int(t.bitWidth), signed=bool(t.isSigned),
                          four_state=bool(getattr(t, "isFourState", True)))


def _is_named_value_type(t) -> bool:
    """A packed struct or an enum: an IR type of its own (§6.3)."""
    c = ty.canon(t)
    return bool(getattr(c, "isEnum", False)) or \
        (bool(getattr(c, "isStruct", False)) and bool(getattr(c, "isPackedStruct",
                                                                getattr(c, "isIntegral", False))))


def type_params(cls) -> List[Any]:
    """The integral type parameters of a class specialization: the ones
    that become ``parameter type`` (in declaration order)."""
    out = []
    for m in ty.canon(cls):
        if type(m).__name__ == "TypeParameterSymbol" and not m.isLocalParam:
            if getattr(ty.canon(m.targetType.type), "isIntegral", False):
                out.append(m)
    return out


def _default_type_of(cls, m):
    """The declared default of type parameter ``m`` (a slang type), else
    its value here."""
    g = getattr(ty.canon(cls), "genericClass", None)
    d = None
    if g is not None:
        try:
            # pyslang binds defaultSpecialization as a property that needs
            # the scope: call its getter.
            d = type(g).defaultSpecialization.fget(g, g.parentScope)
        except Exception:
            d = None
    if d is not None:
        dm = ty.canon(d).find(m.name)
        if dm is not None and type(dm).__name__ == "TypeParameterSymbol":
            return dm.targetType.type
    return m.targetType.type


def _base_name(cls) -> str:
    c = ty.canon(cls)
    return c.genericClass.name if getattr(c, "genericClass", None) is not None else c.name


def _field_refs(x, used: Optional[set], remap: Optional[Dict[int, int]] = None) -> None:
    """Each ``ExprRefField`` on ``self`` in ``x`` (process blocks): its
    index added to ``used``, or moved by ``remap``."""
    import dataclasses as dc
    if isinstance(x, ir.ExprRefField) and isinstance(x.base, ir.TypeExprRefSelf):
        if used is not None:
            used.add(x.index)
        if remap is not None:
            x.index = remap[x.index]
    if isinstance(x, list):
        for e in x:
            _field_refs(e, used, remap)
    elif dc.is_dataclass(x) and not isinstance(x, (type, ir.DataType)):
        for f in dc.fields(x):
            v = getattr(x, f.name)
            if isinstance(v, list) or (dc.is_dataclass(v) and not isinstance(v, type)):
                _field_refs(v, used, remap)


def _assigned(body: list, out: set) -> None:
    """The indexes of the ``self`` fields ``body`` assigns (a target's root:
    ``n`` of ``n[i]``)."""
    for s in body:
        if isinstance(s, ir.StmtAssign):
            for t in s.targets:
                while isinstance(t, (ir.ExprSubscript, ir.ExprAttribute)):
                    t = t.value
                if isinstance(t, ir.ExprRefField) and isinstance(t.base, ir.TypeExprRefSelf):
                    out.add(t.index)
        elif isinstance(s, ir.StmtIf):
            _assigned(s.body, out)
            _assigned(s.orelse, out)


def _port(s: FwSignal, dtype: ir.DataType, name: Optional[str] = None) -> ir.Field:
    name = s.name if name is None else name
    if s.dir is None:
        return ir.Field(name=name, datatype=dtype)
    return ir.FieldInOut(name=name, datatype=dtype, is_out=s.dir == "out",
                         is_inout=s.dir == "inout",
                         direction={"in": ir.SignalDirection.INPUT,
                                    "out": ir.SignalDirection.OUTPUT,
                                    "inout": ir.SignalDirection.INOUT}[s.dir])


# ------------------------------------------------------------- expressions

def _ekey(e) -> Any:
    """A structural key of an expression or type (for type interning and for
    comparing loop iterations and runs)."""
    if e is None:
        return None
    if isinstance(e, ir.ExprConstant):
        return ["c", e.value, e.width]
    if isinstance(e, ir.ExprRefParam):
        return ["p", e.name]
    if isinstance(e, ir.ExprRefLocal):
        return ["g", e.name]
    if isinstance(e, ir.ExprRefUnresolved):
        return ["u", e.name]
    if isinstance(e, ir.ExprBin):
        return ["bin", e.op.name, _ekey(e.lhs), _ekey(e.rhs)]
    if isinstance(e, ir.ExprCompare):
        return ["cmp", [o.name for o in e.ops], _ekey(e.left), [_ekey(x) for x in e.comparators]]
    if isinstance(e, ir.ExprBool):
        return ["bool", e.op.name, [_ekey(x) for x in e.values]]
    if isinstance(e, ir.ExprUnary):
        return ["un", e.op.name, _ekey(e.operand)]
    if isinstance(e, ir.ExprIfExp):
        return ["if", _ekey(e.test), _ekey(e.body), _ekey(e.orelse)]
    if isinstance(e, ir.ExprCall):
        return ["call", _ekey(e.func), [_ekey(x) for x in e.args]]
    if isinstance(e, ir.ExprAttribute):
        return ["attr", _ekey(e.value), e.attr]
    if isinstance(e, ir.ExprStaticRef):
        return ["sref", e.is_global, list(e.path)]
    if isinstance(e, ir.ExprHierarchical):
        return ["path", [[el.name, _ekey(el.subscript)] for el in e.elements]]
    if isinstance(e, ir.DataTypeRef):
        return ["tref", e.ref_name]
    if isinstance(e, ir.DataTypeInt):
        return ["int", e.bits if e.width_expr is None else None, e.signed, _ekey(e.width_expr),
                e.four_state]
    if isinstance(e, ir.DataTypeArray):
        return ["arr", _ekey(e.element_type),
                e.size if e.size_expr is None else None, _ekey(e.size_expr)]
    if isinstance(e, ir.Bind):
        return ["bind", _ekey(e.lhs), _ekey(e.rhs)]
    if isinstance(e, ir.GenerateFor):
        return ["for", e.var, _ekey(e.start), _ekey(e.cond), e.label,
                [_ekey(x) for x in e.items]]
    if isinstance(e, ir.GenerateIf):
        return ["gif", _ekey(e.cond), e.label, [_ekey(x) for x in e.items],
                [_ekey(x) for x in e.orelse]]
    if isinstance(e, ir.Field):
        return _field_key(e)
    if isinstance(e, ir.ProtocolRef):
        return ["proto", e.family, {k: _ekey(v) for k, v in e.params.items()}]
    if isinstance(e, ir.DomainAssoc):
        return ["dassoc", _pkey(e.target), e.kind, e.assoc, _dkey(e.domain)]
    raise TypeError(f"no key for {type(e).__name__}")


def cond_key(e) -> Tuple[str, bool]:
    """A key of a branch condition that is the same in every run (leaves by
    name), and whether it is negated: ``!c`` keys as ``c``, negated."""
    neg = False
    while isinstance(e, ir.ExprUnary) and e.op == ir.UnaryOp.Not:
        neg = not neg
        e = e.operand
    return json.dumps(_ekey(e), sort_keys=True), neg


def _field_key(f) -> list:
    return [type(f).__name__, f.name, _ekey(f.datatype), getattr(f, "is_out", None),
            getattr(f, "is_inout", None), f.role.name if f.role else None,
            {k: _ekey(v) for k, v in f.inst_params.items()}, f.index_var]


def _eval(e, env: Dict[int, int]) -> Optional[int]:
    """The concrete value of a traced expression, its leaves valued by
    ``env`` (``id(leaf)`` -> int); None if it cannot be computed."""
    if isinstance(e, ir.ExprConstant):
        return e.value if isinstance(e.value, int) else None
    if id(e) in env:
        return env[id(e)]
    try:
        if isinstance(e, ir.ExprBin):
            a, b = _eval(e.lhs, env), _eval(e.rhs, env)
            if a is None or b is None:
                return None
            return {ir.BinOp.Add: lambda: a + b, ir.BinOp.Sub: lambda: a - b,
                    ir.BinOp.Mult: lambda: a * b, ir.BinOp.Div: lambda: a // b,
                    ir.BinOp.Mod: lambda: a % b}.get(e.op, lambda: None)()
        if isinstance(e, ir.ExprUnary) and e.op == ir.UnaryOp.USub:
            a = _eval(e.operand, env)
            return None if a is None else -a
        if isinstance(e, ir.ExprIfExp):
            t = _eval(e.test, env)
            return None if t is None else _eval(e.body if t else e.orelse, env)
        if isinstance(e, ir.ExprCompare):
            a, b = _eval(e.left, env), _eval(e.comparators[0], env)
            if a is None or b is None:
                return None
            return int({ir.CmpOp.Eq: a == b, ir.CmpOp.NotEq: a != b, ir.CmpOp.Lt: a < b,
                        ir.CmpOp.LtE: a <= b, ir.CmpOp.Gt: a > b,
                        ir.CmpOp.GtE: a >= b}[e.ops[0]])
        if isinstance(e, ir.ExprBool):
            vs = [_eval(x, env) for x in e.values]
            if any(v is None for v in vs):
                return None
            return int(all(vs) if e.op == ir.BoolOp.And else any(vs))
    except (ZeroDivisionError, KeyError):
        return None
    return None


def _subscripts(e) -> List[Tuple[str, Any]]:
    """``(array name, index)`` of every subscript in a traced handle path
    (``u[i+1].in`` -> ``[("u", i+1)]``), outermost first."""
    out = []
    if isinstance(e, ir.ExprSubscript):
        out += _subscripts(e.value)
        v = e.value
        while isinstance(v, ir.ExprAttribute):
            v = v.value
        name = e.value.attr if isinstance(e.value, ir.ExprAttribute) else \
            (v.name if isinstance(v, ir.ExprRefUnresolved) else None)
        out.append((name, e.slice))
    elif isinstance(e, ir.ExprAttribute):
        out += _subscripts(e.value)
    return out


def _norm(e, tr):
    """``e`` with each member read of a shape (``ExprAttribute`` chains on a
    struct leaf of ``tr``, an ``objects.Traced``) replaced by the leaf of
    that field: ``S.data_w`` -> ``S_data_w`` (§6.6)."""
    if e is None or tr is None:
        return e
    if isinstance(e, ir.ExprAttribute):
        path = []
        x = e
        while isinstance(x, ir.ExprAttribute):
            path.append(x.attr)
            x = x.value
        hit = tr.sleaves.get(id(x))
        if hit is not None:
            leaf = hit[1].get(tuple(reversed(path)))
            if leaf is not None:
                return leaf
        return ir.ExprAttribute(value=_norm(e.value, tr), attr=e.attr)
    if isinstance(e, ir.ExprBin):
        return ir.ExprBin(lhs=_norm(e.lhs, tr), op=e.op, rhs=_norm(e.rhs, tr))
    if isinstance(e, ir.ExprCompare):
        return ir.ExprCompare(left=_norm(e.left, tr), ops=list(e.ops),
                              comparators=[_norm(x, tr) for x in e.comparators])
    if isinstance(e, ir.ExprBool):
        return ir.ExprBool(op=e.op, values=[_norm(x, tr) for x in e.values])
    if isinstance(e, ir.ExprUnary):
        return ir.ExprUnary(op=e.op, operand=_norm(e.operand, tr))
    if isinstance(e, ir.ExprIfExp):
        return ir.ExprIfExp(test=_norm(e.test, tr), body=_norm(e.body, tr),
                            orelse=_norm(e.orelse, tr))
    if isinstance(e, ir.ExprCall):
        return ir.ExprCall(func=e.func, args=[_norm(x, tr) for x in e.args])
    if isinstance(e, ir.ExprSubscript):
        return ir.ExprSubscript(value=_norm(e.value, tr), slice=_norm(e.slice, tr))
    return e


def _check(e, tr, genvars, what: str, owner: str):
    """``e`` as an expression over the parameters of the object traced by
    ``tr`` and the genvars of the frames in ``genvars`` (shape members
    resolved, :func:`_norm`); NotParam if it is not one."""
    if e is None:
        raise NotParam(f"{what} is not an expression of {owner}'s parameters")
    e = _norm(e, tr)
    ok = {id(f.leaf) for f in genvars}
    for x in leaves(e):
        if id(x) not in tr.leaves and id(x) not in ok:
            raise NotParam(f"{what} is not an expression of {owner}'s parameters")
    return e


def _check_type(dt, tr, what: str, owner: str):
    """A traced type (``TType.ir``) in the terms of ``tr``'s object."""
    if isinstance(dt, ir.DataTypeInt) and dt.width_expr is not None:
        return ir.DataTypeInt(bits=dt.bits, signed=dt.signed, four_state=dt.four_state,
                              width_expr=_check(dt.width_expr, tr, (), what, owner))
    return dt


_ELEM = re.compile(r"([A-Za-z_][A-Za-z0-9_$]*)\[(\d+)\]")
_FMT = re.compile(r"([A-Za-z_][A-Za-z0-9_$]*)\[%0?d\]")


# ------------------------------------------------------------------ mapper

class _Scope:
    """What is in scope while mapping one component (one run of it)
    generally."""

    def __init__(self, c: FwComponent):
        self.c = c
        # Arrays (children, ports, bundles made in a loop over parameters):
        # base name -> {"key": the loop's frame key, "size": size Expr,
        # "kind": "child" | "port"}.
        self.arrays: Dict[str, dict] = {}
        # Concrete values of the leaves, to check traced indices against.
        self.env: Dict[int, int] = {}
        vals = {flat: _get(c.params, path) for flat, path, _ in c.mparams}
        vals.update({flat: v for flat, _p, _t, v in c.tr.cparams})
        for lid, name in c.leaves.items():
            if name in vals and isinstance(vals[name], int):
                self.env[lid] = vals[name]

    def check(self, e, genvars, what: str):
        return _check(e, self.c.tr, genvars, what, self.c.path)


def _get(v, path):
    for p in path:
        v = v[p]
    return v


def _loops(ctx) -> tuple:
    return tuple(x[0] for x in ctx if x[0].kind == "loop")


# Region tree of one component's structure: what was created and connected
# in which loop iteration and branch arm (keyed so that runs and iterations
# can be merged).

class _Block:
    """A region: ``items`` by key, in order: ``("f", name)`` a Field,
    ``("e", base)`` an array element (an _Elem), ``("b", key)`` a Bind,
    ``("d", key)`` a DomainAssoc, ``("loop", frame key)`` a _Loop,
    ``("if", frame key)`` an _If."""

    def __init__(self):
        self.items: Dict[tuple, Any] = {}
        self.frames: Dict[tuple, int] = {}       # frame key -> id(frame) seen here


class _Loop:
    def __init__(self, f, start, cond):
        self.f = f
        self.start = start
        self.cond = cond
        self.count = f.count
        self.iters: Dict[int, _Block] = {}
        self.need = False                         # ran no iteration, body has effects


class _If:
    def __init__(self, f, cond, why: str):
        self.f = f
        self.cond = cond                          # checked, or None (why says why)
        self.why = why
        self.arms: Dict[bool, Optional[_Block]] = {True: None, False: None}
        self.need: Dict[bool, bool] = {True: False, False: False}


class _Elem:
    """An element of an array, in one iteration: ``sig`` keys the element's
    type and parameters (equal in every iteration); ``make(size)`` makes the
    array's field."""

    def __init__(self, ks, sig, make, kind):
        self.ks = set(ks)
        self.sig = sig
        self.make = make
        self.kind = kind


def _same(a, b, where: str) -> None:
    if json.dumps(_ekey(a), sort_keys=True) != json.dumps(_ekey(b), sort_keys=True):
        raise NotParam(where)


def _merge(a: Optional[_Block], b: Optional[_Block], where: str) -> Optional[_Block]:
    """Two runs (or two loop iterations) of one region, as one. Everything
    but the arms of branches and the iterations of loops must be equal."""
    if a is None:
        return b
    if b is None:
        return a
    if list(a.items) != list(b.items) and set(a.items) != set(b.items):
        diff = sorted(str(k) for k in set(a.items) ^ set(b.items))
        raise NotParam(f"{where} differ in what they create or connect ({diff[0]})")
    out = _Block()
    out.frames = dict(a.frames)
    for k, x in a.items.items():
        y = b.items[k]
        if isinstance(x, _Loop):
            m = _Loop(x.f, x.start, x.cond)
            _same(x.start, y.start, f"the start of the loop at {x.f.where} differs")
            _same(x.cond, y.cond, f"the bound of the loop at {x.f.where} differs")
            m.count = x.count
            m.need = x.need or y.need
            for i in sorted(set(x.iters) | set(y.iters)):
                m.iters[i] = _merge(x.iters.get(i), y.iters.get(i), where)
            out.items[k] = m
        elif isinstance(x, _If):
            m = _If(x.f, x.cond if x.cond is not None else y.cond, x.why or y.why)
            if x.cond is not None and y.cond is not None:
                _same(x.cond, y.cond, f"the condition at {x.f.where} differs")
            for arm in (True, False):
                m.arms[arm] = _merge(x.arms[arm], y.arms[arm], where)
                m.need[arm] = x.need[arm] or y.need[arm]
            out.items[k] = m
        elif isinstance(x, _Elem):
            if json.dumps(x.sig, sort_keys=True) != json.dumps(y.sig, sort_keys=True):
                raise NotParam(f"the elements of {k[1]}[] differ in type or parameters")
            out.items[k] = _Elem(x.ks | y.ks, x.sig, x.make, x.kind)
        else:
            _same(x, y, f"{where} differ in {k[1]}")
            out.items[k] = x
    return out


class IrMapper:
    def __init__(self, rt, parameterize: bool = True, require_param: bool = False):
        self.rt = rt
        self.parameterize = parameterize
        self.require_param = require_param
        self.ctxt = ir.Context()
        self.diags: List[Diag] = []
        self._sig: Dict[str, str] = {}        # structural key -> type name
        self._names: Dict[str, int] = {}
        self.type_of: Dict[int, str] = {}     # id(FwComponent) -> type name
        self.generic: Dict[str, bool] = {}    # type name -> has template parameters
        self._diag_keys: set = set()
        self._vtypes: Dict[str, str] = {}     # value type signature -> type_m key
        self._anon = 0

    def run(self) -> Tuple[ir.Context, str]:
        top = self._component(self.rt.top)
        self._port_names()
        self._hubs()
        return self.ctxt, top

    # -- types --------------------------------------------------------------
    def _type(self, t) -> ir.DataType:
        """A concrete type (a slang type): a packed struct or enum as a
        ``DataTypeRef`` to its IR type (:meth:`_value_type`), any other
        integral type as a ``DataTypeInt``."""
        if t is not None and _is_named_value_type(t):
            return ir.DataTypeRef(ref_name=self._value_type(t))
        return _int_type(t)

    def _value_type(self, t) -> str:
        """The ``type_m`` key of packed struct or enum ``t``, mapped (once)
        to a ``DataTypeStruct`` (``packed``) or ``DataTypeEnum``. Keyed by
        its package-qualified name (``prm_pkg::cfg_t``); an anonymous one,
        or a second type of the same name, gets a key of its own.

        A typedef of a package that the extern RTL declares too is that
        package's type: ``extern_name`` is set, so it is referred to, not
        declared again (a copy would not be the RTL port's type). Declared
        differently there, it is an error (``FW-E-EXT-TYPE``)."""
        from zuspec.fe.sv.module_header import type_signature
        from .params import _qual
        q = _qual(t)
        c = ty.canon(t)
        sig = f"{q}|{type_signature(c)}"
        if sig in self._vtypes:
            return self._vtypes[sig]
        if q:
            key, n = q, 0
            while key in self.ctxt.type_m:
                n += 1
                key = f"{q}#{n}"
        else:
            self._anon += 1
            key = f"{'enum' if getattr(c, 'isEnum', False) else 'struct'}_{self._anon}_t"
        self._vtypes[sig] = key
        if getattr(c, "isEnum", False):
            from zuspec.fe.sv.interp.values import const_to_py
            items = {m.name: const_to_py(m.value, c) for m in c
                     if type(m).__name__ == "EnumValueSymbol"}
            vt = ir.DataTypeEnum(name=key, items=items, width=int(c.bitWidth),
                                 base_type=_int_type(c.baseType))
        else:
            fields = [ir.Field(name=m.name, datatype=self._type(m.type)) for m in c
                      if type(m).__name__ == "FieldSymbol"]
            vt = ir.DataTypeStruct(name=key, super=None, packed=True, fields=fields)
        self.ctxt.type_m[key] = vt
        if q:
            hdrs = getattr(self.rt, "headers", None)
            rtl_sig = hdrs.declared_type(q) if hdrs is not None else None
            if rtl_sig is not None:
                if rtl_sig == type_signature(c):
                    vt.extern_name = q
                else:
                    self.diags.append(Diag("FW-E-EXT-TYPE", q,
                                           f"the extern RTL declares {q} differently "
                                           f"({rtl_sig}, here {type_signature(c)})"))
        return key

    def _ir_type(self, raw, scope_cls) -> ir.DataType:
        """A type as seen in ``scope_cls``'s code: ``DataTypeRef(T)`` if it
        is (follows from) ``scope_cls``'s type parameter ``T``, else
        concrete."""
        if scope_cls is not None:
            name = ty.type_param_name(raw, scope_cls)
            if name is not None:
                return ir.DataTypeRef(ref_name=name)
        return self._type(raw)

    def _default_type(self, cls, m) -> ir.DataType:
        """The declared default of type parameter ``m``, else its value
        here."""
        return self._type(_default_type_of(cls, m))

    # -- bundles ----------------------------------------------------------
    def _bundle_type(self, b: FwBundle, generic: bool = True) -> str:
        """A bundle type, generic over its own class parameters when
        ``generic`` (members whose type is written over one are typed by
        it). NotParam when a member's type cannot be written so."""
        scope = b if generic else None
        fields: List[ir.Field] = []
        for ch in b.children.values():
            if isinstance(ch, FwBundle):
                fields.append(self._bundle_field(ch, scope))
            else:
                fields.append(_port(ch, self._sig_type(ch, scope)))
        tps = self._class_tparams(b.btype, b.tr) if generic else []
        t = ir.DataTypeBundle(name=b.btype_name, super=None, fields=fields,
                              template_params=tps)
        base = _base_name(b.btype) if generic else b.btype_name
        return self._intern(base, t, "bundle")

    def _bundle_field(self, b: FwBundle, o, genvars=(), name: Optional[str] = None,
                      array: Optional[ir.DataTypeArray] = None) -> ir.Field:
        """The field of bundle ``b`` in the code of ``o`` (a component or
        bundle traced generally; None: concretely)."""
        try:
            tname = self._bundle_type(b, True)
            inst = self._class_inst(b, b.btype, b.tr.cparams, o, genvars)
        except NotParam:
            tname = self._bundle_type(b, False)
            if o is not None and b.targs is not None and b.targs.traced:
                raise NotParam(f"the type of bundle {b.path} cannot be written over "
                               f"the parameters of {o.path}")
            inst = {}
        dt = ir.DataTypeRef(ref_name=tname)
        if array is not None:
            dt = ir.DataTypeArray(element_type=dt, size=array.size, size_expr=array.size_expr)
        return ir.Field(name=b.name if name is None else name, datatype=dt,
                        role=_ROLE[b.kind], inst_params=inst)

    def _sig_type(self, s: FwSignal, o) -> ir.DataType:
        """The type of signal ``s`` in the code of ``o`` (None: concrete)."""
        if o is None:
            return self._type(s.dtype)
        cls = o.btype if isinstance(o, FwBundle) else o.cls
        tt = s.ttype
        if tt is not None:
            if tt.tainted:
                raise NotParam(f"the type of {s.path} cannot be written over the parameters "
                               f"of {o.path}")
            if tt.ir is not None:
                return _check_type(tt.ir, o.tr, f"the type of {s.path}", o.path)
        return self._ir_type(s.dtype, cls)

    # -- class parameters --------------------------------------------------
    def _class_tparams(self, cls, tr) -> List[ir.TemplateParam]:
        """Template parameters for the class parameters of ``cls``: its
        integral type parameters and its value class parameters (shapes
        flattened), in declaration order. Defaults are the values here."""
        out: List[ir.TemplateParam] = []
        for m in ty.canon(cls):
            k = type(m).__name__
            if k == "TypeParameterSymbol" and not m.isLocalParam and \
                    getattr(ty.canon(m.targetType.type), "isIntegral", False):
                out.append(ir.TemplateParamType(name=m.name, kind=ir.TemplateParamKind.TYPE,
                                                default_value=self._default_type(cls, m)))
            elif k == "ParameterSymbol" and not m.isLocalParam and m.isPortParam:
                for flat, path, t, v in tr.cparams:
                    if path[0] == m.name:
                        out.append(ir.TemplateParamValue(
                            name=flat, kind=ir.TemplateParamKind.VALUE,
                            value_type=_int_type(t), default_value=ir.ExprConstant(value=int(v))))
        return out

    def _class_inst(self, obj, cls, cparams, o, genvars=()) -> Dict[str, Any]:
        """The values ``obj`` (a child or bundle, created as ``cls``) was
        given for its class parameters, in the terms of ``o`` (the object
        whose code created it; None: concretely)."""
        out: Dict[str, Any] = {}
        tt = obj.targs
        if o is not None and tt is not None and tt.tainted:
            raise NotParam(f"the class of {obj.path} cannot be written over the parameters "
                           f"of {o.path}")
        args = tt.args if tt is not None and tt.args is not None else None
        ocls = None if o is None else (o.btype if isinstance(o, FwBundle) else o.cls)
        if o is not None and args is not None and tt.traced:
            # A parameter left to its default is its class's value there,
            # unless the default is computed from the parameters passed.
            for m in ty.canon(cls):
                k = type(m).__name__
                if k not in ("ParameterSymbol", "TypeParameterSymbol") or m.isLocalParam \
                        or not m.isPortParam or m.name in args:
                    continue
                if _default_names_params(m, cls):
                    raise NotParam(f"class parameter {m.name} of {obj.path} is left to its "
                                   f"default, which is computed from the others")
        for m in type_params(cls):
            raw = ty.type_param(cls, m.name, raw=True)
            a = args.get(m.name) if args is not None else None
            if o is None:
                out[m.name] = self._type(raw)
            elif a is not None and a.tainted:
                raise NotParam(f"the type parameter {m.name} of {obj.path} cannot be "
                               f"written over the parameters of {o.path}")
            elif a is not None and a.ir is not None:
                out[m.name] = _check_type(a.ir, o.tr, f"type parameter {m.name} of {obj.path}",
                                          o.path)
            else:
                out[m.name] = self._ir_type(raw, ocls)
        for flat, path, _t, v in cparams:
            if o is None:
                out[flat] = ir.ExprConstant(value=int(v))
                continue
            if args is None:
                if o.tr.leaves or getattr(o, "mparams", None):
                    raise NotParam(f"the class parameters of {obj.path} cannot be traced "
                                   f"(it is not created by naming its class)")
                out[flat] = ir.ExprConstant(value=int(v))
                continue
            a = args.get(path[0])
            if not isinstance(a, Sym):
                out[flat] = ir.ExprConstant(value=int(v))
                continue
            e = a.expr
            if e is not None:
                for p in path[1:]:
                    e = ir.ExprAttribute(value=e, attr=p)
            out[flat] = _check(e, o.tr, genvars, f"class parameter {flat} of {obj.path}",
                               o.path)
        return out

    # -- components -------------------------------------------------------
    def _component(self, c: FwComponent) -> str:
        if id(c) in self.type_of:
            return self.type_of[id(c)]
        for ch in c.children.values():
            if isinstance(ch, FwComponent):
                self._component(ch)
        if c.is_extern:
            name = self._extern(c)
        else:
            t, why = None, None
            if self.parameterize:
                try:
                    t = self._general(c)
                except NotParam as e:
                    why = str(e)
            if t is None:
                t = self._concrete(c)
            name = self._intern(_base_name(c.cls), t, "component")
            if why is not None:
                self._spec(c, name, why)
            elif self.parameterize and name != _base_name(c.cls):
                self._spec(c, name, "its structure differs from the other instances of "
                           f"{_base_name(c.cls)} (constructor factories that pick other "
                           f"realizations)", info=True)
        self.type_of[id(c)] = name
        return name

    def _spec(self, c: FwComponent, name: str, why: str, info: bool = False) -> None:
        msg = f"emitted as a specialization ({name}): {why}"
        key = (c.path, msg)
        if key in self._diag_keys:
            return
        self._diag_keys.add(key)
        if self.require_param and not info:
            self.diags.append(Diag(SPEC_ERROR, c.path, msg))
        else:
            self.diags.append(Diag(SPEC_INFO, c.path, msg, "info"))

    def _template_params(self, c: FwComponent) -> List[ir.TemplateParam]:
        tps = self._class_tparams(c.cls, c.tr)
        for flat, path, t in c.mparams:
            d = _get(c.params_dflt, path) if c.params_dflt is not None else _get(c.params, path)
            tps.append(ir.TemplateParamValue(name=flat, kind=ir.TemplateParamKind.VALUE,
                                             value_type=_int_type(t),
                                             default_value=ir.ExprConstant(value=int(d))))
        names = [tp.name for tp in tps]
        if len(set(names)) != len(names):
            raise NotParam(f"two module parameters would have the same name ({names})")
        return tps

    def _inst_params(self, ch: FwComponent, sc: Optional[_Scope], genvars) -> Dict[str, Any]:
        """What ``ch``'s creator passes for ``ch``'s template parameters."""
        tname = self.type_of[id(ch)]
        o = sc.c if sc is not None else None
        if not self.generic.get(tname):
            if sc is not None:
                # A specialized child: its values must not depend on ours.
                if ch.targs is not None and ch.targs.traced:
                    raise NotParam(f"child {ch.name} is specialized ({tname}) but its class "
                                   f"is written over our parameters")
                if ch.targs is None:
                    for v in self._class_inst(ch, ch.cls, [], o).values():
                        if isinstance(v, ir.DataTypeRef):
                            raise NotParam(f"child {ch.name} is specialized but its type "
                                           f"parameter {v.ref_name} is ours")
                for flat, e in ch.param_exprs.items():
                    if e is None or any(True for _ in leaves(e)):
                        raise NotParam(f"child {ch.name} is specialized ({tname}) but its "
                                       f"parameter {flat} depends on ours")
            return {}
        out = self._class_inst(ch, ch.cls, ch.tr.cparams, o, genvars)
        for flat, path, _t in ch.mparams:
            if sc is None:
                out[flat] = ir.ExprConstant(value=int(_get(ch.params, path)))
            else:
                out[flat] = sc.check(ch.param_exprs.get(flat), genvars,
                                     f"the value of {ch.path}'s parameter {flat}")
        return out

    # -- concrete (per specialization) -------------------------------------
    def _concrete(self, c: FwComponent) -> ir.DataTypeComponent:
        fields: List[ir.Field] = []
        for ch in c.children.values():
            if isinstance(ch, FwSignal):
                fields.append(_port(ch, self._type(ch.dtype)))
            elif isinstance(ch, FwBundle):
                fields.append(self._bundle_field(ch, None))
            elif isinstance(ch, FwComponent):
                fields.append(ir.Field(name=ch.name,
                                       datatype=ir.DataTypeRef(ref_name=self.type_of[id(ch)]),
                                       inst_params=self._inst_params(ch, None, ())))
        t = ir.DataTypeComponent(name=c.type_name, super=None, fields=fields)
        for conn in c.conns:
            t.bind_map.append(ir.Bind(lhs=self._path(conn.a, c), rhs=self._path(conn.b, c)))
        for tie in c.ties:
            t.bind_map.append(ir.Bind(lhs=self._path(tie.sig, c),
                                      rhs=ir.ExprConstant(value=tie.value, width=tie.sig.width)))
        try:
            self._domains(c, t)
            self._protocols(c, t)
        except NotParam as e:
            from .rt import ElabError
            raise ElabError(str(e))
        self._procs(c, t)
        return t

    def _procs(self, c: FwComponent, t: ir.DataTypeComponent) -> None:
        """``c``'s step and comb blocks (zuspec-fe-sv ``ProcMapper``, as a
        back end lowering to RTL sees them) as ``t.proc_blocks``. The class
        properties they use that are not yet fields (component state) are
        added as fields. A step block runs in the component's clock domain
        and, when it has a reset branch or reads ``in_reset``, its reset
        domain (``DomainRef`` with ``target=[]``); one that writes a
        signal set to another clock domain runs in that one."""
        from zuspec.fe.sv.error import ErrorSeverity, ErrorReporter
        from zuspec.fe.sv.proc_mapper import ProcMapper, field_index
        from zuspec.synth.passes.domains.ports import _in_resets
        names = {f.name: i for i, f in enumerate(t.fields)}
        state = []
        for n in field_index(c.cls):
            if n in names:
                continue
            m = c.cls.find(n)
            mt = getattr(m, "type", None)
            if mt is None or not getattr(mt, "isIntegral", False):
                continue
            names[n] = len(t.fields) + len(state)
            state.append(ir.Field(name=n, datatype=self._type(mt)))
        rep = ErrorReporter()
        blocks = ProcMapper(reporter=rep, fields=names, emit=True).map(c.cls)
        for e in rep.errors:
            code, _, msg = e.message.partition(": ")
            if not code.startswith("FW-"):
                code, msg = "FW-E-PROC", e.message
            sev = "error" if e.severity == ErrorSeverity.ERROR else "warning"
            path = c.path
            if code == "FW-E-WRITE-MULTI":
                # "<class>.<written path>: written by ..."
                where, _, msg = msg.partition(": ")
                path = f"{c.path}.{where.partition('.')[2]}"
            self.diags.append(Diag(code, path, msg, sev))
        # FW-E-WRITE-IN: a block writes a port that is an input as seen
        # from the component (a mirror's valid, say)
        for b in blocks:
            for p in b.writes:
                o = c
                for n in p:
                    o = getattr(o, "children", {}).get(n)
                if isinstance(o, FwSignal) and o.effective_dir() == "in":
                    self.diags.append(Diag("FW-E-WRITE-IN", ".".join([c.path] + p),
                                           f"block '{b.name}' writes an input"))
        if not blocks:
            return
        used = set()
        _field_refs(blocks, used)
        keep = [f for f in state if names[f.name] in used]
        t.fields = list(t.fields) + keep
        new_ix = {f.name: i for i, f in enumerate(t.fields)}
        _field_refs(blocks, None, {names[n]: i for n, i in new_ix.items()})
        kept = {new_ix[f.name] for f in keep}
        for b in blocks:
            if b.kind != ir.ProcKind.STEP:
                continue
            w = set()
            _assigned(b.reset_body + b.body, w)
            b.state = [t.fields[i].name for i in sorted(w & kept)]
            b.clock = _write_clock(t, b)
            if b.reset_body or any(True for _ in _in_resets(b.body)):
                b.reset = ir.DomainRef(kind="fw_reset_kind", target=[])
        t.proc_blocks = blocks

    def _arrays_of(self, o) -> set:
        """The array fields (by name) of a component's IR type."""
        if not isinstance(o, FwComponent) or id(o) not in self.type_of:
            return set()
        t = self.ctxt.type_m.get(self.type_of[id(o)])
        if t is None:
            return set()
        return {f.name for f in t.fields if isinstance(f.datatype, ir.DataTypeArray)}

    def _path(self, o: FwObject, scope: FwComponent) -> ir.ExprHierarchical:
        """The path to ``o`` from ``scope``; an element of a child's array
        port (the child mapped generally) is a constant subscript."""
        elems = []
        cur = scope
        for name in o.rel_path(scope):
            m = _ELEM.fullmatch(name)
            nxt = cur.children.get(name) if cur is not None else None
            if m is not None and cur is not scope and m.group(1) in self._arrays_of(cur):
                elems.append(ir.ExprHierarchicalElem(
                    name=m.group(1), subscript=ir.ExprConstant(value=int(m.group(2)))))
            else:
                elems.append(ir.ExprHierarchicalElem(name=name))
            cur = nxt
        return ir.ExprHierarchical(elements=elems)

    # -- general (parameterized) -------------------------------------------
    def _general(self, c: FwComponent) -> ir.DataTypeComponent:
        tps = self._template_params(c)
        tree = self._explore(c)
        labels = [0]
        root = self._emit(tree, labels, True)
        fields = [x for x in root if isinstance(x, ir.Field)]
        names: Dict[str, int] = {}
        for f in fields:
            if f.name in names:
                raise NotParam(f"'{f.name}' names two fields of {c.path}")
            names[f.name] = 1
        root = _sink_binds(root, set(), _cond_children(root))
        t = ir.DataTypeComponent(name=c.type_name, super=None, fields=fields)
        t.template_params = tps
        t.bind_map = [x for x in root if isinstance(x, ir.Bind)]
        t.generates = [x for x in root if isinstance(x, (ir.GenerateFor, ir.GenerateIf))]
        _check_names(t)
        self._domains(c, t, general=True)
        self._protocols(c, t, general=True)
        self._procs(c, t)
        return t

    def _explore(self, c: FwComponent) -> _Block:
        """The region tree of ``c``: its own run, merged with runs that take
        the arms of its traced ``if``\\ s that did not run (and may have
        effects), until every arm is covered."""
        runs = [(c, {})]
        seen = {frozenset()}
        tree = None
        i = 0
        while i < len(runs):
            r, force = runs[i]
            t = self._tree(r)
            tree = _merge(tree, t, "the arms of the branches on parameters")
            for f in r.frames:
                if f.kind != "if" or not f.unexplored or f.forced or f.cond is None:
                    continue
                if any(x.kind == "opaque" for x, _ in f.outer):
                    continue
                ck, neg = cond_key(f.cond)
                if ck in force:
                    continue
                # Assume the condition has the other value: every if on it
                # (or on its negation) takes the other arm.
                f2 = dict(force)
                f2[ck] = (not f.taken) != neg
                k = frozenset(f2.items())
                if k in seen:
                    continue
                seen.add(k)
                if len(runs) >= MAX_RUNS:
                    raise NotParam(f"more than {MAX_RUNS} combinations of branch arms on "
                                   f"parameters to explore")
                top, errs = self.rt.explore(r, f2)
                if top is None or errs:
                    why = str(errs[0]) if errs else "no component"
                    raise NotParam(f"the arm of the branch on parameters at {f.where} that did not run "
                                   f"cannot be elaborated at the values seen ({why})")
                runs.append((top, f2))
            i += 1
        return tree

    def _tree(self, c: FwComponent) -> _Block:
        """The region tree of one run of ``c``."""
        for ch in c.children.values():
            if isinstance(ch, FwComponent):
                self._component(ch)
        sc = _Scope(c)
        for f in c.frames:
            if f.kind == "opaque" and f.unexplored:
                raise NotParam(f"{f.reason} ({f.where})")
            if f.irregular and f.unexplored:
                raise NotParam(f"a loop or branch on parameters is left early ({f.where})")
        for what, ctx in _domain_stmts(c, assocs=False):
            if ctx:
                raise NotParam(f"{what} is under a loop or branch on parameters")
        self._pre_arrays(sc)
        root = _Block()
        # Objects, in creation order. Domain, endpoint and configuration
        # objects are not fields.
        for ch in c.children.values():
            if not isinstance(ch, (FwDomain, FwEndpoint, FwCfg)):
                self._place_object(sc, root, ch)
        # Associations of children created under a branch on parameters: in
        # the child's arm.
        for a in c.dassocs:
            if a.ctx:
                self._place_assoc(sc, root, a)
        # Connections and ties.
        for conn in c.conns:
            b = ir.Bind(lhs=self._sym_path(sc, conn.a, conn.a_sym, conn.ctx),
                        rhs=self._sym_path(sc, conn.b, conn.b_sym, conn.ctx))
            blk = self._block(sc, root, conn.ctx)
            blk.items.setdefault(("b", json.dumps(_ekey(b))), b)
        for tie in c.ties:
            g = _loops(tie.ctx)
            if tie.value_sym is not None:
                rhs = sc.check(tie.value_sym.expr, g, f"the tie value of {tie.sig.path}")
            else:
                rhs = ir.ExprConstant(value=tie.value, width=tie.sig.width)
            b = ir.Bind(lhs=self._sym_path(sc, tie.sig, tie.sig_sym, tie.ctx), rhs=rhs)
            blk = self._block(sc, root, tie.ctx)
            blk.items.setdefault(("b", json.dumps(_ekey(b))), b)
        # Every traced frame has its region, even with nothing in it: an arm
        # that ran and made nothing is covered.
        for f in c.frames:
            if any(x.kind == "opaque" for x, _ in f.outer) or f.kind == "opaque":
                continue
            ctx = tuple((x, it, x.value, x.taken) for x, it in f.outer)
            ctx += ((f, 0 if f.kind == "loop" else f.iteration, f.value, f.taken),)
            if f.kind == "loop":
                blk = self._block(sc, root, ctx[:-1])
                node = self._loop_node(sc, blk, f, _loops(ctx[:-1]))
                if f.count == 0 and f.unexplored:
                    node.need = True
            else:
                self._block(sc, root, ctx)
                blk = self._block(sc, root, ctx[:-1])
                node = blk.items[("if", f.key)]
                if f.unexplored:
                    node.need[not f.taken] = True
        return root

    def _pre_arrays(self, sc: _Scope) -> None:
        """Find the arrays: objects created in a traced loop and named
        ``base[<loop variable>]``."""
        c = sc.c
        for ch in c.children.values():
            if isinstance(ch, (FwDomain, FwEndpoint, FwCfg)) or not ch.ctx or ch.ctx[-1][0].kind != "loop":
                continue
            f, _k, v, _t = ch.ctx[-1]
            if ch.name_sym is None:
                raise NotParam(f"{ch.path} is created in a loop over parameters but its name "
                               f"does not depend on the loop")
            base = self._array_base(ch, f)
            if ch.name != f"{base}[{v}]":
                raise NotParam(f"{ch.path}: an array element must be named "
                               f"{base}[<loop index>]")
            kind = "child" if isinstance(ch, FwComponent) else "port"
            a = sc.arrays.get(base)
            if a is None:
                size = _array_size(f)
                if size is None:
                    raise NotParam(f"the loop creating {base}[] ({f.where}) does not count "
                                   f"from 0 to a bound")
                size = sc.check(size, (), f"the size of {base}[]")
                sc.arrays[base] = {"key": f.key, "size": size, "kind": kind, "frame": f}
            elif a["key"] != f.key or a["kind"] != kind:
                raise NotParam(f"the elements of {base}[] are not made by one loop")

    def _block(self, sc: _Scope, root: _Block, ctx) -> _Block:
        """The region of ``ctx`` (traced frames, outermost first), made if
        need be."""
        blk = root
        done = []
        for f, k, v, taken in ctx:
            if f.kind == "opaque":
                raise NotParam(f"{f.reason} ({f.where})")
            seen = blk.frames.get(f.key)
            if seen is not None and seen != id(f):
                raise NotParam(f"the {'loop' if f.kind == 'loop' else 'branch'} at {f.where} "
                               f"runs more than once in the same place")
            blk.frames[f.key] = id(f)
            if f.kind == "loop":
                node = self._loop_node(sc, blk, f, tuple(done))
                blk = node.iters.setdefault(k, _Block())
                done.append(f)
            else:
                node = blk.items.get(("if", f.key))
                if node is None:
                    cond, why = None, ""
                    if f.cond is None:
                        why = f"the condition at {f.where} is not an expression of parameters"
                    else:
                        try:
                            cond = sc.check(f.cond, tuple(done), f"the condition at {f.where}")
                        except NotParam as e:
                            why = str(e)
                    node = blk.items[("if", f.key)] = _If(f, cond, why)
                arm = node.arms[bool(taken)]
                if arm is None:
                    arm = node.arms[bool(taken)] = _Block()
                blk = arm
        return blk

    def _loop_node(self, sc: _Scope, blk: _Block, f, outer) -> _Loop:
        node = blk.items.get(("loop", f.key))
        if node is None:
            start = sc.check(f.start, outer, f"the start of the loop at {f.where}")
            cond = sc.check(f.cond, outer + (f,), f"the bound of the loop at {f.where}")
            node = blk.items[("loop", f.key)] = _Loop(f, start, cond)
        return node

    def _place_object(self, sc: _Scope, root: _Block, ch: FwObject) -> None:
        c = sc.c
        if not ch.ctx:
            if ch.name_sym is not None:
                raise NotParam(f"the name of {ch.path} is computed from parameters")
            root.items[("f", ch.name)] = self._field_of(sc, ch, ())
            return
        last = ch.ctx[-1][0]
        if last.kind == "loop":
            f, k, _v, _t = ch.ctx[-1]
            base = _ELEM.fullmatch(ch.name).group(1)
            a = sc.arrays[base]
            if not isinstance(ch, FwComponent) and len(ch.ctx) != 1:
                raise NotParam(f"{ch.path}: a port array is made in a loop under other control "
                               f"on parameters")
            genvars = _loops(ch.ctx)
            if isinstance(ch, FwComponent):
                tname = self.type_of[id(ch)]
                ip = self._inst_params(ch, sc, genvars)
                sig = [tname, {n: _ekey(x) for n, x in ip.items()}]

                def make(size, base=base, tname=tname, ip=ip, f=f):
                    return ir.Field(name=base, datatype=ir.DataTypeArray(
                        element_type=ir.DataTypeRef(ref_name=tname), size=f.count,
                        size_expr=size), index_var=f.var, inst_params=ip)
            else:
                proto = self._field_of(sc, ch, genvars, name=base)
                sig = _field_key(proto)

                def make(size, ch=ch, base=base, f=f, genvars=genvars):
                    arr = ir.DataTypeArray(element_type=None, size=f.count, size_expr=size)
                    return self._field_of(sc, ch, genvars, name=base, array=arr)
            blk = self._block(sc, root, ch.ctx)
            key = ("e", base)
            prev = blk.items.get(key)
            if prev is not None:
                raise NotParam(f"{base}[] has two elements in one iteration")
            blk.items[key] = _Elem({k}, sig, make, "child" if isinstance(ch, FwComponent)
                                   else "port")
            return
        # Under a branch (and no loop): a child in a generate if.
        if not isinstance(ch, FwComponent):
            raise NotParam(f"{ch.path} is a port or signal created under a condition on "
                           f"parameters")
        if _loops(ch.ctx):
            raise NotParam(f"{ch.path} is created under a condition in a loop over parameters")
        if ch.name_sym is not None:
            raise NotParam(f"the name of {ch.path} is computed from parameters")
        blk = self._block(sc, root, ch.ctx)
        blk.items[("f", ch.name)] = self._field_of(sc, ch, ())

    def _place_assoc(self, sc: _Scope, root: _Block, a) -> None:
        """An association made under a branch on parameters (no loop): of a
        child created in the same arm, it goes in that arm's generate if,
        with the child. Any other is a specialization."""
        c = sc.c
        what = f"the association of {a.obj.path} with {a.dom.path}"
        if _loops(a.ctx) or any(f.kind != "if" for f, *_ in a.ctx):
            raise NotParam(f"{what} is under a loop on parameters")
        o = a.obj
        if not (isinstance(o, FwComponent) and o.parent is c and o.ctx
                and [(id(f), t) for f, _k, _v, t in o.ctx] ==
                [(id(f), t) for f, _k, _v, t in a.ctx]):
            raise NotParam(f"{what} is under a branch on parameters but is not of a child "
                           f"created in the same arm")
        da = ir.DomainAssoc(target=[o.name], kind=self._domain_kind(a.kname), assoc=a.name,
                            domain=self._dref(c, a.dom, a.ref))
        blk = self._block(sc, root, a.ctx)
        blk.items[("d", json.dumps(_ekey(da), sort_keys=True))] = da

    def _field_of(self, sc: _Scope, ch: FwObject, genvars, name: Optional[str] = None,
                  array: Optional[ir.DataTypeArray] = None) -> ir.Field:
        c = sc.c
        if isinstance(ch, FwComponent):
            return ir.Field(name=ch.name if name is None else name,
                            datatype=ir.DataTypeRef(ref_name=self.type_of[id(ch)]),
                            inst_params=self._inst_params(ch, sc, genvars))
        if isinstance(ch, FwSignal):
            dt = self._sig_type(ch, c)
            if array is not None:
                dt = ir.DataTypeArray(element_type=dt, size=array.size,
                                      size_expr=array.size_expr)
            return _port(ch, dt, name)
        return self._bundle_field(ch, c, genvars, name=name, array=array)

    # -- emission of the region tree ----------------------------------------
    def _emit(self, blk: Optional[_Block], labels, root: bool) -> List[Any]:
        """The IR items of a region: Fields, Binds and generate blocks."""
        out: List[Any] = []
        if blk is None:
            return out
        fields, rest = [], []
        for key, x in blk.items.items():
            if key[0] == "f":
                fields.append(x)
            elif key[0] in ("b", "d"):
                rest.append(x)
            elif key[0] == "e":
                raise NotParam(f"{key[1]}[] is made outside a loop")
            elif key[0] == "loop":
                fs, gs = self._emit_loop(x, labels, root)
                fields.extend(fs)
                rest.extend(gs)
            else:
                rest.extend(self._emit_if(x, labels))
        return fields + _merge_ifs(rest)

    def _emit_loop(self, node: _Loop, labels, root: bool):
        f = node.f
        if not node.iters:
            if node.need:
                raise NotParam(f"a loop on parameters ({f.where}) has code that did not run "
                               f"at the elaborated values")
            return [], []
        body = None
        for k in sorted(node.iters):
            body = _merge(body, node.iters[k], f"the iterations of the loop at {f.where}")
        fields, rest = [], _Block()
        for key, x in body.items.items():
            if key[0] == "e":
                if sorted(x.ks) != list(range(node.count)):
                    raise NotParam(f"the loop creating {key[1]}[] ({f.where}) does not "
                                   f"create one element per iteration")
                if x.kind == "port" and not root:
                    raise NotParam(f"port array {key[1]}[] is made under a condition")
                fields.append(x.make(self._size_of(key[1], node)))
            else:
                rest.items[key] = x
        items = self._emit(rest, labels, False)
        gens = []
        if items:
            if any(isinstance(x, ir.Field) for x in items):
                raise NotParam(f"a child is created in the loop at {f.where} that is not "
                               f"an element of an array")
            labels[0] += 1
            gens.append(ir.GenerateFor(var=f.var, start=node.start, cond=node.cond,
                                       label=f"g_{labels[0]}", items=items))
        return fields, gens

    @staticmethod
    def _size_of(base: str, node: _Loop):
        """The size of the array made by ``node``: from its bound."""
        s = _array_size(node.f)
        if s is None:
            raise NotParam(f"the loop creating {base}[] ({node.f.where}) does not count "
                           f"from 0 to a bound")
        # The bound as checked (the frame's own leaves are the run's).
        c = node.cond
        if isinstance(c, ir.ExprCompare) and len(c.ops) == 1:
            if c.ops[0] == ir.CmpOp.Lt:
                return c.comparators[0]
            if c.ops[0] == ir.CmpOp.LtE:
                return ir.ExprBin(lhs=c.comparators[0], op=ir.BinOp.Add,
                                  rhs=ir.ExprConstant(value=1))
            if c.ops[0] == ir.CmpOp.Gt:
                return c.left
            if c.ops[0] == ir.CmpOp.GtE:
                return ir.ExprBin(lhs=c.left, op=ir.BinOp.Add, rhs=ir.ExprConstant(value=1))
        raise NotParam(f"the loop creating {base}[] ({node.f.where}) does not count "
                       f"from 0 to a bound")

    def _emit_if(self, node: _If, labels) -> List[Any]:
        for arm in (True, False):
            if node.arms[arm] is None and node.need[arm]:
                raise NotParam(f"a branch on parameters ({node.f.where}) has code that did "
                               f"not run at the elaborated values")
        labels[0] += 1
        label = f"g_{labels[0]}"
        items = self._emit(node.arms[True], labels, False)
        orelse = self._emit(node.arms[False], labels, False)
        if not items and not orelse:
            return []
        if node.cond is None:
            raise NotParam(node.why or f"the branch at {node.f.where} cannot be a generate if")
        return [ir.GenerateIf(cond=node.cond, label=label, items=items, orelse=orelse)]

    @staticmethod
    def _array_base(ch: FwObject, f) -> str:
        ns = ch.name_sym
        e = ns.expr if ns is not None else None
        if not (isinstance(e, ir.ExprCall) and e.func.name == "$sformatf" and len(e.args) == 2
                and isinstance(e.args[0], ir.ExprConstant) and e.args[1] is f.leaf):
            raise NotParam(f"{ch.path} is created in a loop over parameters but not named "
                           f"$sformatf(\"<name>[%0d]\", <loop index>)")
        m = _FMT.fullmatch(str(e.args[0].value))
        if m is None:
            raise NotParam(f"{ch.path}: an array element must be named "
                           f"\"<name>[%0d]\"")
        return m.group(1)

    def _sym_path(self, sc: _Scope, o: FwObject, sym, ctx) -> ir.ExprHierarchical:
        """The path to ``o`` from the component, with array subscripts (the
        component's own arrays, or a child's array ports) written as the
        traced index expressions."""
        genvars = _loops(ctx)
        if sym is not None and sym.expr is None:
            raise NotParam(f"{o.path} is selected in a way the tracing cannot follow")
        subs = _subscripts(sym.expr) if sym is not None else []
        used = [False] * len(subs)
        env = dict(sc.env)
        for f, _k, v, _t in ctx:
            if f.kind == "loop":
                env[id(f.leaf)] = v
        elems = []
        cur = sc.c
        arrays = set(sc.arrays)
        for name in o.rel_path(sc.c):
            nxt = cur.children.get(name) if cur is not None else None
            m = _ELEM.fullmatch(name)
            if m is None or m.group(1) not in arrays:
                elems.append(ir.ExprHierarchicalElem(name=name))
            else:
                base, k = m.group(1), int(m.group(2))
                idx = None
                for j, (sname, sx) in enumerate(subs):
                    if not used[j] and sname == base and _eval(sx, env) == k:
                        idx = sc.check(sx, genvars, f"the index of {o.path}")
                        used[j] = True
                        break
                if idx is None:
                    if subs:
                        raise NotParam(f"cannot tell how {o.path} was selected")
                    idx = ir.ExprConstant(value=k)
                elems.append(ir.ExprHierarchicalElem(name=base, subscript=idx))
            arrays = self._arrays_of(nxt)
            cur = nxt
        for j, (sname, _sx) in enumerate(subs):
            if not used[j]:
                raise NotParam(f"{o.path} is selected through '{sname}' by a traced index")
        return ir.ExprHierarchical(elements=elems)

    # -- externs ----------------------------------------------------------
    def _extern(self, c: FwComponent) -> str:
        rec = c.extern
        t = None
        if self.parameterize and not rec.ctl and not c.frames:
            try:
                t = self._extern_type(c, True)
            except NotParam:
                t = None
        if t is None:
            t = self._extern_type(c, False)
        return self._intern(_base_name(c.cls), t, "extern")

    def _extern_type(self, c: FwComponent, general: bool) -> ir.DataTypeExtern:
        rec = c.extern
        sc = _Scope(c) if general else None
        o = c if general else None
        fields: List[ir.Field] = []
        for ch in c.children.values():
            if isinstance(ch, FwSignal):
                fields.append(_port(ch, self._sig_type(ch, o)))
            elif isinstance(ch, FwBundle):
                fields.append(self._bundle_field(ch, o))
        params: Dict[str, ir.Expr] = {}
        for k, arg in rec.params.items():
            s = arg.value
            if general and arg.kind == "value" and isinstance(s, Sym):
                params[k] = sc.check(s.expr, (), f"extern parameter {k}")
            elif general and arg.kind == "type" and arg.ttype is not None and arg.ttype.traced:
                if arg.ttype.tainted or arg.ttype.ir is None:
                    raise NotParam(f"the type of extern parameter {k} cannot be written over "
                                   f"the parameters of {c.path}")
                params[k] = _check_type(arg.ttype.ir, c.tr, f"extern parameter {k}", c.path)
            else:
                params[k] = param_ir(arg, self._type)
        pins = []
        hdr = rec.header
        for p, what, v, w in rec.resolved:
            if what == "port":
                pins.append(ir.ExternPin(name=p, path=v.rel_path(v.scope)))
            elif what == "tie":
                s = rec.tie_syms.get(p)
                if general and isinstance(s, Sym):
                    e = sc.check(s.expr, (), f"the tie of pin {p}")
                else:
                    e = ir.ExprConstant(value=v, width=w or 0)
                pins.append(ir.ExternPin(name=p, tie=e))
            elif what == "domain":
                pins.append(ir.ExternPin(
                    name=p, domain_kind=self._domain_kind(v.kname), output=v.out,
                    domain=self._dref(c, v.dom, v.ref) if v.explicit else None,
                    domain_attr=_value_exprs(self.rt, v.attr, v.attr_type)))
            else:
                hp = hdr.port(p) if hdr is not None else None
                pins.append(ir.ExternPin(name=p, open=True, datatype=_open_pin_type(
                    hp, hdr, params, general, f"open pin {p} of {c.path}")))
        t = ir.DataTypeExtern(name=c.type_name, super=None, fields=fields,
                              extern_name=rec.module, extern_params=params, pins=pins)
        # What its build() said about domains: its interfaces' associations
        # (a dual-clock FIFO's in and out), provides, port names.
        if general:
            self._domains(c, t)
        else:
            try:
                self._domains(c, t)
            except NotParam as e:
                from .rt import ElabError
                raise ElabError(str(e))
        if general:
            t.template_params = self._template_params(c)
        return t

    # -- domains (Z6) -------------------------------------------------------
    def _domain_kind(self, kname: str) -> str:
        """The kind's ``DataTypeDomainKind`` in ``type_m``, made once."""
        if kname not in self.ctxt.type_m:
            d = self.rt.dom
            kclass = d.kinds[kname]
            derivs: Dict[str, ir.DomainDerivDecl] = {}
            ptype = _member_type(kclass, "params_t")
            dt = _field_type(ptype, "deriv")
            for m in (dt or []):
                if type(m).__name__ == "EnumValueSymbol":
                    n = m.name.lower()
                    derivs[n] = ir.DomainDerivDecl(sources=[] if n == "root" else ["from"])
            sev = {r.name.lower(): ir.DomainSeverity[d.severity(kname, r).upper()]
                   for r in ir.DomainRel}
            self.ctxt.type_m[kname] = ir.DataTypeDomainKind(
                name=kname, physical=d.physical(kname), derivations=derivs, severities=sev)
        return kname

    def _dref(self, c: FwComponent, dom: Optional[FwDomain],
              ref: Optional[ir.DomainRef] = None) -> Optional[ir.DomainRef]:
        """``dom`` as ``c``'s code reaches it (see the module docstring):
        ``ref``, the ref the code read it through, when it gives ``dom``."""
        if dom is None:
            return None
        k = self._domain_kind(dom.kname)
        if ref is not None and self._ref_gives(c, ref, dom):
            return ir.DomainRef(kind=ref.kind, assoc=ref.assoc, target=list(ref.target))
        if dom.parent is c:
            return ir.DomainRef(decl=dom.name)
        if dm.get(c, k) is dom:
            return ir.DomainRef(kind=k)
        for (kn, n), a in c.assoc.items():
            if kn == k and n and a.dom is dom:
                return ir.DomainRef(kind=k, assoc=n)
        for ch in c.children.values():
            if isinstance(ch, FwComponent):
                for (kn, n), a in ch.assoc.items():
                    if kn == k and n and a.dom is dom:
                        return ir.DomainRef(kind=k, assoc=n, target=[ch.name])
            if isinstance(ch, (FwComponent, FwBundle, FwSignal)) and dm.get(ch, k) is dom:
                return ir.DomainRef(kind=k, target=[ch.name])
        raise NotParam(f"{c.path} uses domain {dom.path}, which it cannot reach by name or "
                       f"through an association")

    @staticmethod
    def _ref_gives(c: FwComponent, ref: ir.DomainRef, dom: FwDomain) -> bool:
        o = c
        for n in ref.target:
            o = o.children.get(n) if o is not None else None
        return o is not None and dm.get(o, ref.kind, ref.assoc) is dom

    def _domains(self, c: FwComponent, t: ir.DataTypeComponent, general: bool = False) -> None:
        """The Z6 shapes of what ``c``'s code said about domains. In a
        general type, an association made under a branch on parameters is
        in its generate if instead (``_place_assoc``)."""
        rt = self.rt
        for ch in c.children.values():
            if not isinstance(ch, FwDomain):
                continue
            k = self._domain_kind(ch.kname)
            deriv, params = _decl_params(rt, ch, lambda o: self._dref(c, o))
            refs = list(ch.source_refs) + [None] * (len(ch.sources) - len(ch.source_refs))
            t.domains.append(ir.DomainDecl(
                name=ch.name, kind=k, deriv=deriv, params=params,
                sources=[self._dref(c, s, r) for s, r in zip(ch.sources, refs)]))
        for a in c.dassocs:
            if general and a.ctx:
                continue
            t.domain_assocs.append(ir.DomainAssoc(
                target=[] if a.obj is c else a.obj.rel_path(c), kind=self._domain_kind(a.kname),
                assoc=a.name, domain=self._dref(c, a.dom, a.ref)))
        for drv in c.drivers:
            t.domain_drivers.append(ir.DomainDriver(
                domain=self._dref(c, drv.dom), signal=drv.sig.rel_path(c),
                attr=_value_exprs(rt, drv.attr, drv.attr_type)))
        for w in c.waivers:
            t.domain_waivers.append(ir.DomainWaiver(
                kind=self._domain_kind(w.kname), a=w.a.rel_path(c), b=w.b.rel_path(c),
                reason=w.reason))
        for (kn, assoc), port in c.pnames.items():
            t.domain_port_names.append(ir.DomainPortName(
                kind=self._domain_kind(kn), assoc=assoc, port=port))
        for (kn, assoc), (p, pt, refs) in c.provs.items():
            deriv, sources, params = _value_params(rt, p, pt, lambda o, r=None: self._dref(c, o, r),
                                                   refs)
            t.domain_provides.append(ir.DomainProvides(
                kind=self._domain_kind(kn), assoc=assoc, deriv=deriv, sources=sources,
                params=params))

    # -- protocols (protocol-kit-plan §8.11, the Z7 shapes) --------------
    def _hubs(self) -> None:
        """A ``DataTypeProtocolHub`` per family used, and a
        ``DataTypeProtocol`` per API it carries, in ``type_m``."""
        proto = getattr(self.rt, "proto", None)
        if proto is None:
            return
        for fam in proto.families:
            recs = [r for r in proto.recs.values() if r.family == fam]
            if not recs or fam in self.ctxt.type_m:
                continue
            r = recs[0]
            hub = ir.DataTypeProtocolHub(name=fam, template_params=[
                ir.TemplateParamType(name=k, kind=ir.TemplateParamKind.TYPE)
                if isinstance(v, str) else
                ir.TemplateParamValue(name=k, kind=ir.TemplateParamKind.VALUE,
                                      value_type=ir.DataTypeInt(bits=32, signed=True))
                for k, v in r.params.items()])
            for d in sorted(r.apis):
                api = _base_name(r.apis[d].cls)
                hub.apis.append(ir.ProtocolApi(
                    api=api, dir=ir.ProtoDir.REQ if d == 0 else ir.ProtoDir.RSP))
                if api not in self.ctxt.type_m:
                    self.ctxt.type_m[api] = ir.DataTypeProtocol(name=api)
            self.ctxt.type_m[fam] = hub

    def _protocols(self, c: FwComponent, t: ir.DataTypeComponent,
                   general: bool = False) -> None:
        """The Z7 shapes of ``c``: its converter, its bundles' protocols,
        its endpoints and configuration objects, and the API and
        configuration connections made in its code. In a general type, an
        endpoint or configuration object under a loop or branch on
        parameters makes ``c`` a specialization."""
        rt = self.rt
        proto = getattr(rt, "proto", None)
        if proto is not None:
            for r in proto.recs.values():
                e = next((x for x in r.converters if x["type"] == c.type_name), None)
                if e is not None:
                    t.converter = ir.ProtocolConverter(
                        from_proto=_proto_ref(proto.recs[e["from"]]),
                        to_proto=_proto_ref(proto.recs[e["to"]]), role=e.get("role", ""))
                    break

        def walk(o):
            for ch in o.children.values():
                if isinstance(ch, FwComponent):
                    continue
                yield ch
                yield from walk(ch)
        for ch in sorted(walk(c), key=lambda o: o.seq):
            if ch.kind in BUNDLE_KINDS and ch.proto is not None:
                t.bundle_protocols.append(ir.BundleProtocol(
                    target=ch.rel_path(c), protocol=_proto_ref(ch.proto), role=ch.kind))
            elif general and ch.ctx and isinstance(ch, (FwEndpoint, FwCfg)):
                raise NotParam(f"{ch.path} is under a loop or branch on parameters")
            elif isinstance(ch, FwEndpoint):
                # port, export, socket; a socket's api is the OUT it calls.
                kind = ch.api[3:]
                a = ty.type_param(ch.cls, "OUT" if ch.sock else "API")
                t.api_endpoints.append(ir.ApiEndpoint(
                    name=".".join(ch.rel_path(c)), kind=kind,
                    api=_base_name(a) if a is not None else None))
            elif isinstance(ch, FwCfg):
                dt = self._type(ty.type_param(ch.cls, "T"))
                lst = t.cfg_publishers if ch.kind == CFG_PUB else t.cfg_consumers
                cls = ir.CfgPublisher if ch.kind == CFG_PUB else ir.CfgConsumer
                lst.append(cls(name=".".join(ch.rel_path(c)), cfg_type=dt))
        for b in rt.binds:
            if b.scope is not c:
                continue
            if b.kind == "api":
                t.api_connections.append(ir.ApiConnection(
                    kind="connect_up" if b.up else "connect",
                    src=b.a.rel_path(c), dst=b.b.rel_path(c)))
            elif b.kind == "cfg":
                t.cfg_connections.append(ir.CfgConnection(
                    publisher=b.a.rel_path(c), consumer=b.b.rel_path(c),
                    derived=False))      # no derived cfg binding

    def _port_names(self) -> None:
        """Each kind's ``K::port_name(assoc, attr)`` for every association
        name of the kind and every attribute value its pins, drivers and
        ``pin_default()`` have (``DataTypeDomainKind.port_names``)."""
        rt, d = self.rt, self.rt.dom
        assocs: Dict[str, set] = {}
        attrs: Dict[str, list] = {}
        for c in rt.components:
            for (kn, n) in c.assoc:
                assocs.setdefault(kn, set()).add(n)
            for drv in c.drivers:
                attrs.setdefault(drv.dom.kname, []).append(drv.attr)
            if c.extern is not None:
                for dp in c.extern.dpins:
                    attrs.setdefault(dp.kname, []).append(dp.attr)
        # Association names the classes read (a named association read but
        # set by no parent is still a port of the class).
        for t in list(self.ctxt.type_m.values()):
            for r in _domain_refs(t):
                if r.kind is not None and r.decl is None:
                    assocs.setdefault(r.kind, set()).add(r.assoc)
            for pn in getattr(t, "domain_port_names", []):
                assocs.setdefault(pn.kind, set()).add(pn.assoc)
        for kn, k in self.ctxt.type_m.items():
            if not isinstance(k, ir.DataTypeDomainKind) or not k.physical:
                continue
            dflt = d.pin_default(kn)
            k.pin_default = _value_exprs(rt, dflt, None)
            # A pin attribute named active_low is the polarity (fw_domain_kind).
            k.polarity = "active_low" if "active_low" in k.pin_default else ""
            for a in sorted(assocs.get(kn, set()) | {""}):
                for v in [dflt] + attrs.get(kn, []):
                    key = ir.port_name_key(a, _value_exprs(rt, v, None))
                    if key not in k.port_names:
                        k.port_names[key] = d.kind_port_name(kn, a, v)

    # -- interning --------------------------------------------------------
    def _intern(self, base: str, t, kind: str) -> str:
        """The name of ``t``: an equal type already made is reused. The first
        type of a class takes the class's own name, so the module emitted
        for it does too (§6.6); later ones get ``__1``, ``__2``."""
        key = json.dumps([kind, base, _key(t)], sort_keys=True)
        if key in self._sig:
            return self._sig[key]
        n = self._names.get(base, 0)
        while True:
            name = base if n == 0 else f"{base}__{n}"
            n += 1
            if name not in self.ctxt.type_m:
                break
        self._names[base] = n
        t.name = name
        self.ctxt.type_m[name] = t
        self._sig[key] = name
        self.generic[name] = bool(getattr(t, "template_params", None))
        return name


def _write_clock(t, b):
    """The clock domain of step block ``b`` of ``t``: that of the first
    signal it writes (or the signal's nearest parent) that ``t`` sets to a
    clock domain, else the component's own (FW-E-STEP-DOMAINS checks that
    a block's writes agree)."""
    clocks = {tuple(a.target): a.domain for a in getattr(t, "domain_assocs", [])
              if a.kind == "fw_clock_kind" and a.assoc == "" and a.target}
    for p in b.writes:
        for n in range(len(p), 0, -1):
            d = clocks.get(tuple(p[:n]))
            if d is not None:
                return d
    return ir.DomainRef(kind="fw_clock_kind", target=[])


def _domain_refs(t):
    """Every ``DomainRef`` written in IR type ``t``."""
    out = []
    for d in getattr(t, "domains", []):
        out.extend(d.sources)
        out.extend(v for v in d.params.values() if isinstance(v, ir.DomainRef))
    out.extend(a.domain for a in getattr(t, "domain_assocs", []))
    out.extend(d.domain for d in getattr(t, "domain_drivers", []))
    for p in getattr(t, "domain_provides", []):
        out.extend(p.sources)
    for p in getattr(t, "pins", None) or []:
        if p.domain is not None:
            out.append(p.domain)
    return [r for r in out if isinstance(r, ir.DomainRef)]


def _domain_stmts(c: FwComponent, assocs: bool = True):
    """``(what, traced frames)`` of each domain statement of ``c``'s code
    (but associations, without ``assocs``)."""
    for ch in c.children.values():
        if isinstance(ch, FwDomain):
            yield f"domain {ch.path}", ch.ctx
    for a in (c.dassocs if assocs else ()):
        yield f"the association of {a.obj.path} with {a.dom.path}", a.ctx
    for d in c.drivers:
        yield f"the driver of {d.dom.path}", d.ctx
    for w in c.waivers:
        yield f"the waiver of {w.a.path} and {w.b.path}", ()


def _member_type(cls, name: str):
    """The type ``name`` declared in ``cls`` or a base class (a typedef)."""
    for k in ty.class_chain(cls):
        m = k.find(name)
        if m is not None and type(m).__name__ != "TransparentMemberSymbol":
            return ty.canon(m)
    return None


def _field_type(t, name: str):
    for f in (t or []):
        if type(f).__name__ == "FieldSymbol" and f.name == name:
            return ty.canon(f.type)
    return None


def _value_exprs(rt, v, t, dref=None) -> Dict[str, ir.Expr]:
    """A struct value (a params value or pin attributes) as a field -> Expr
    map: integral fields as constants, domain handles by ``dref``; null
    handles are left out."""
    out: Dict[str, ir.Expr] = {}
    for k, x in v.items():
        x = strip(x)
        if isinstance(x, int):
            out[k] = ir.ExprConstant(value=x)
        elif dref is not None and x is not None and isinstance(rt.fw(x), FwDomain):
            out[k] = dref(rt.fw(x))
    return out


def _decl_params(rt, d: FwDomain, dref):
    """A domain object's derivation name and its params, but ``deriv`` and
    ``from`` (the sources)."""
    from .netlist import _value_text
    pt = ty.canon(d.params_type)
    dv = strip(d.params["deriv"])
    deriv = _value_text(dv, _field_type(pt, "deriv")).lower()
    params = _value_exprs(rt, d.params, pt, dref)
    params.pop("deriv", None)
    return deriv, params


def _value_params(rt, v, t, dref, refs=()):
    """A params value (``provides``) as ``DomainDecl`` writes one: the
    derivation's name, its sources (``from``, through ``refs`` where the
    code's refs are known) and its other params."""
    from .netlist import _value_text
    pt = ty.canon(t)
    deriv = _value_text(strip(v["deriv"]), _field_type(pt, "deriv")).lower()
    srcs = strip(v["from"]) if "from" in v.keys() else None
    srcs = srcs if isinstance(srcs, list) else [srcs]
    refs = list(refs) + [None] * (len(srcs) - len(refs))
    sources = [dref(rt.fw(x), r) for x, r in zip(srcs, refs)
               if x is not None and isinstance(rt.fw(x), FwDomain)]
    params = _value_exprs(rt, v, pt, dref)
    params.pop("deriv", None)
    params.pop("from", None)
    return deriv, sources, params


def _open_pin_type(hp, hdr, params: Dict[str, Any], general: bool,
                   what: str) -> Optional[ir.DataType]:
    """The type of an open pin (``hp``, a module_header.HeaderPort), for
    the net it is connected to: as wide as the pin. In a type mapped
    generally (``general``), a width over the module's parameters is
    written over the extern's (``params``: the module parameter values the
    extern sets), NotParam if it cannot be. None without a header."""
    if hp is None:
        return None
    if hp.type_param:
        v = params.get(hp.type_param)
        if isinstance(v, ir.DataType):
            return v
        return ir.DataTypeInt(bits=hp.width, signed=hp.signed)
    if not general:
        return ir.DataTypeInt(bits=hp.width, signed=hp.signed)
    e = _module_expr(hp.width_expr, hdr, params, 0)
    if e is None:
        raise NotParam(f"the width of {what} cannot be written over its parameters")
    if not _has_refs(e):
        return ir.DataTypeInt(bits=hp.width, signed=hp.signed)
    return ir.DataTypeInt(bits=hp.width, signed=hp.signed, width_expr=e)


def _module_expr(e, hdr, params: Dict[str, Any], depth: int):
    """``e`` (over a module's parameters, from its header) over the values
    the extern gives them: a parameter it sets is that value; any other,
    or a localparam, its initializer, likewise. None if it cannot be."""
    if e is None or depth > 16:
        return None
    if isinstance(e, ir.ExprRefParam):
        if e.name in params:
            v = params[e.name]
            return v if isinstance(v, ir.Expr) and not isinstance(v, ir.ExprStaticRef) \
                and not (isinstance(v, ir.ExprConstant) and not isinstance(v.value, int)) \
                else None
        return _module_expr(hdr.param_exprs.get(e.name), hdr, params, depth + 1)
    if isinstance(e, ir.ExprConstant):
        return e
    if isinstance(e, ir.ExprBin):
        a = _module_expr(e.lhs, hdr, params, depth)
        b = _module_expr(e.rhs, hdr, params, depth)
        return None if a is None or b is None else ir.ExprBin(lhs=a, op=e.op, rhs=b)
    if isinstance(e, ir.ExprUnary):
        a = _module_expr(e.operand, hdr, params, depth)
        return None if a is None else ir.ExprUnary(op=e.op, operand=a)
    if isinstance(e, ir.ExprCall):
        args = [_module_expr(x, hdr, params, depth) for x in e.args]
        return None if any(x is None for x in args) else ir.ExprCall(func=e.func, args=args)
    return None


def _has_refs(e) -> bool:
    """Whether ``e`` may depend on a parameter (anything but constants
    combined by operators and calls)."""
    if isinstance(e, ir.ExprConstant):
        return False
    if isinstance(e, ir.ExprBin):
        return _has_refs(e.lhs) or _has_refs(e.rhs)
    if isinstance(e, ir.ExprUnary):
        return _has_refs(e.operand)
    if isinstance(e, ir.ExprCall):
        return any(_has_refs(x) for x in e.args)
    return True


def _default_names_params(m, cls) -> bool:
    """Whether the default of class parameter ``m`` of ``cls`` (as declared)
    names another parameter of ``cls``."""
    if type(m).__name__ == "TypeParameterSymbol":
        syn = getattr(m.targetType, "typeSyntax", None)
    else:
        syn = getattr(m.declaredType, "initializerSyntax", None)
    if syn is None:
        return False
    c = ty.canon(cls)
    found = [False]

    def visit(n):
        if getattr(n, "kind", None) is not None and n.kind.name == "IdentifierName":
            x = c.find(n.identifier.valueText)
            if x is not None and type(x).__name__ in ("ParameterSymbol", "TypeParameterSymbol") \
                    and x.name != m.name:
                found[0] = True
        return True
    try:
        syn.visit(visit)
    except Exception:                     # pragma: no cover - be safe
        return True
    return found[0]


def _merge_ifs(items: List[Any]) -> List[Any]:
    """Sibling generate ifs on the same condition (or its negation) as one
    block: an ``if`` in ``build()`` and one in ``connect()`` on the same
    parameter make the children and the connections of one arm."""
    out: List[Any] = []
    by_cond: Dict[str, ir.GenerateIf] = {}
    for x in items:
        if not isinstance(x, ir.GenerateIf):
            out.append(x)
            continue
        neg = isinstance(x.cond, ir.ExprUnary) and x.cond.op == ir.UnaryOp.Not
        k = json.dumps(_ekey(x.cond.operand if neg else x.cond), sort_keys=True)
        g = by_cond.get(k)
        items_, orelse = (x.orelse, x.items) if neg else (x.items, x.orelse)
        if g is None:
            if neg:
                x = ir.GenerateIf(cond=x.cond.operand, label=x.label, items=items_,
                                  orelse=orelse)
            by_cond[k] = x
            out.append(x)
        else:
            g.items = g.items + items_
            g.orelse = g.orelse + orelse
    return out


def _cond_children(items, inside: bool = False) -> set:
    """The names of the children declared in generate ifs."""
    out = set()
    for x in items:
        if isinstance(x, ir.Field) and inside:
            out.add(x.name)
        elif isinstance(x, ir.GenerateIf):
            out |= _cond_children(x.items, True) | _cond_children(x.orelse, True)
        elif isinstance(x, ir.GenerateFor):
            out |= _cond_children(x.items, inside)
    return out


def _declares(items, name: str) -> bool:
    for x in items:
        if isinstance(x, ir.Field) and x.name == name:
            return True
        if isinstance(x, ir.GenerateIf) and (_declares(x.items, name) or
                                             _declares(x.orelse, name)):
            return True
    return False


def _bind_names(b: ir.Bind) -> set:
    return {side.elements[0].name for side in (b.lhs, b.rhs)
            if isinstance(side, ir.ExprHierarchical) and side.elements}


def _sink_binds(items: List[Any], visible: set, cond: set) -> List[Any]:
    """Each connection to a child declared in generate ifs moved into the
    arms that declare it: ``if (p) u = A::create("u"); else u =
    B::create("u");`` with ``u.in.connect_up(in)`` made after the branch
    connects whichever ``u`` the arm has. An arm without the child is not
    in the structural subset."""
    here = visible | {x.name for x in items if isinstance(x, ir.Field)}
    out: List[Any] = []
    for x in items:
        if not isinstance(x, ir.Bind):
            out.append(x)
            continue
        missing = sorted(n for n in _bind_names(x) if n in cond and n not in here)
        if not missing:
            out.append(x)
            continue
        gifs = [g for g in items if isinstance(g, ir.GenerateIf)
                and all(_declares(g.items, n) or _declares(g.orelse, n) for n in missing)]
        if len(gifs) != 1:
            raise NotParam(f"a connection to {missing[0]} is made where {missing[0]} may not "
                           f"exist")
        g = gifs[0]
        for arm in (g.items, g.orelse):
            if not all(_declares(arm, n) for n in missing):
                raise NotParam(f"a connection to {missing[0]} is made after a branch on "
                               f"parameters with an arm that does not create it")
            arm.append(x)
    for x in out:
        if isinstance(x, ir.GenerateIf):
            x.items = _sink_binds(x.items, here, cond)
            x.orelse = _sink_binds(x.orelse, here, cond)
        elif isinstance(x, ir.GenerateFor):
            for b in x.items:
                if isinstance(b, ir.Bind) and any(n in cond and n not in here
                                                  for n in _bind_names(b)):
                    raise NotParam(f"a connection in the loop {x.label} is made to a child "
                                   f"that may not exist")
            x.items = _sink_binds(x.items, here, cond)
    return out


def _check_names(t: ir.DataTypeComponent) -> None:
    """Every field, inside generate blocks too, has a name of its own,
    except in arms of a generate if that exclude each other."""
    seen: Dict[str, List[tuple]] = {}

    def add(name, arms):
        for other in seen.get(name, []):
            d = dict(other)
            if not any(g in d and d[g] != a for g, a in arms):
                raise NotParam(f"'{name}' names two fields")
        seen.setdefault(name, []).append(arms)

    for f in t.fields:
        add(f.name, ())

    def walk(items, arms):
        for x in items:
            if isinstance(x, ir.Field):
                add(x.name, arms)
            elif isinstance(x, ir.GenerateFor):
                walk(x.items, arms)
            elif isinstance(x, ir.GenerateIf):
                walk(x.items, arms + ((id(x), 0),))
                walk(x.orelse, arms + ((id(x), 1),))
    walk(t.generates, ())


def _array_size(f) -> Optional[ir.Expr]:
    """The element count of an array made by loop ``f``: it must start at 0
    and run while ``i < N`` (``N``), ``i <= M`` (``M + 1``)."""
    if not (isinstance(f.start, ir.ExprConstant) and f.start.value == 0):
        return None
    c = f.cond
    if not isinstance(c, ir.ExprCompare) or len(c.ops) != 1:
        return None
    op, lhs, rhs = c.ops[0], c.left, c.comparators[0]
    if rhs is f.leaf:
        lhs, rhs = rhs, lhs
        op = {ir.CmpOp.Gt: ir.CmpOp.Lt, ir.CmpOp.GtE: ir.CmpOp.LtE}.get(op)
    if lhs is not f.leaf or any(x is f.leaf for x in leaves(rhs)):
        return None
    if op == ir.CmpOp.Lt:
        return rhs
    if op == ir.CmpOp.LtE:
        return ir.ExprBin(lhs=rhs, op=ir.BinOp.Add, rhs=ir.ExprConstant(value=1))
    return None


def _key(t) -> list:
    """A structural key of a type: what makes two types equal."""
    def tpk(tp):
        if isinstance(tp, ir.TemplateParamType):
            return ["type", tp.name, _ekey(tp.default_value)]
        return ["value", tp.name, _ekey(tp.value_type), _ekey(tp.default_value)]
    out = [_field_key(f) for f in t.fields]
    out.append([tpk(tp) for tp in getattr(t, "template_params", [])])
    if isinstance(t, ir.DataTypeComponent):
        out.append([[_ekey(b.lhs), _ekey(b.rhs)] for b in t.bind_map])
        out.append([_ekey(g) for g in t.generates])
        out.append([[d.name, d.kind, d.deriv, [_dkey(x) for x in d.sources],
                     _dmap(d.params)] for d in t.domains])
        out.append([[_pkey(a.target), a.kind, a.assoc, _dkey(a.domain)]
                    for a in t.domain_assocs])
        out.append([[_dkey(d.domain), _pkey(d.signal), _dmap(d.attr)]
                    for d in t.domain_drivers])
        out.append([[w.kind, _pkey(w.a), _pkey(w.b), w.reason] for w in t.domain_waivers])
        out.append([[x.kind, x.assoc, x.port] for x in t.domain_port_names])
        out.append([[x.kind, x.assoc, x.deriv, [_dkey(y) for y in x.sources], _dmap(x.params)]
                    for x in t.domain_provides])
        cv = t.converter
        out.append(None if cv is None else
                   [_ekey(cv.from_proto), _ekey(cv.to_proto), cv.role])
        out.append([[_pkey(x.target), _ekey(x.protocol), x.role] for x in t.bundle_protocols])
        out.append([[x.name, x.kind, x.api, _ekey(x.protocol), _pkey(x.imp)]
                    for x in t.api_endpoints])
        out.append([[x.kind, _pkey(x.src), _pkey(x.dst)] for x in t.api_connections])
        out.append([[x.name, _ekey(x.cfg_type)] for x in t.cfg_publishers])
        out.append([[x.name, _ekey(x.cfg_type)] for x in t.cfg_consumers])
        out.append([[_pkey(x.publisher), _pkey(x.consumer), x.derived]
                    for x in t.cfg_connections])
    if isinstance(t, ir.DataTypeExtern):
        out.append([t.extern_name, {k: _ekey(v) for k, v in t.extern_params.items()},
                    [[p.name, p.path, _ekey(p.tie), p.open, _ekey(p.datatype),
                      p.domain_kind, _dkey(p.domain), _dmap(p.domain_attr), p.output]
                     for p in t.pins]])
    return out


def _proto_ref(rec) -> ir.ProtocolRef:
    """A protocol record as a ``ProtocolRef``: its family and its params
    by name (a type parameter as a ``DataTypeRef`` to its spelling)."""
    return ir.ProtocolRef(family=rec.family, params={
        k: ir.DataTypeRef(ref_name=v) if isinstance(v, str) else ir.ExprConstant(value=v)
        for k, v in rec.params.items()})


def _pkey(path) -> list:
    return [p if isinstance(p, str) else [p.name, _ekey(p.subscript)] for p in path]


def _dkey(e) -> Any:
    """:func:`_ekey`, with domain references."""
    if isinstance(e, ir.DomainRef):
        return ["dref", e.decl, e.kind, e.assoc, _pkey(e.target)]
    return _ekey(e)


def _dmap(m) -> dict:
    return {k: _dkey(v) for k, v in m.items()}


def to_ir(rt, parameterize: bool = True, require_param: bool = False) \
        -> Tuple[ir.Context, str, List[Diag]]:
    """The design ``rt`` elaborated, as structural Zuspec IR: the context,
    the top's type name, and the mapping's diagnostics (§6.6)."""
    m = IrMapper(rt, parameterize, require_param)
    ctxt, top = m.run()
    return ctxt, top, m.diags
