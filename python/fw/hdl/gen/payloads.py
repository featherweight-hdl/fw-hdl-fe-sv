"""``<prefix>_payloads``: per bundle of channels, a class of static
functions over each struct channel payload ``<n>_t``:

- ``<n>_sprint(v)``: its fields, ``code=0x1 tag=0x2a``;
- ``<n>_compare(a, b, output diff)``: 1 when equal, else 0 with the
  differing fields in ``diff``;
- ``<n>_mask(v)``: ``v`` as a link with pins delivers it. fw-hdl has no
  optional fields (``fw_opt``) yet, so this is ``v`` (guess).

Declared once per kit (``kit_decls``), not per API.
"""
from __future__ import annotations

from typing import List

import zuspec.ir.core as ir

from . import irb
from .kit import Kit

try:
    from zuspec.ir.core import SignalDirection
except ImportError:                         # pragma: no cover
    from zuspec.ir.core.fields import SignalDirection

_STATIC = {"static": True}
_STR = ir.DataTypeString


def payloads_name(prefix: str) -> str:
    return prefix + "_payloads"


def short_name(t: ir.DataTypeRef) -> str:
    """``scenario::test_ch_rsp_t`` -> ``test_ch_rsp``."""
    n = t.ref_name.rsplit("::", 1)[-1]
    return n[:-2] if n.endswith("_t") else n


def _fmt(args: List[ir.Expr]) -> ir.Expr:
    return ir.ExprCall(func=ir.ExprRefUnresolved(name="$sformatf"), args=args)


def _sprint(n: str, t: ir.DataTypeRef, names: List[str]) -> ir.Function:
    v = irb.ref("v")
    fmt = " ".join(f"{f}=0x%0h" for f in names)
    return irb.method(f"{n}_sprint", [irb.arg("v", t)],
                      [irb.ret(_fmt([irb.const(fmt)] + [irb.attr(v, f) for f in names]))],
                      returns=_STR())


def _compare(n: str, t: ir.DataTypeRef, names: List[str]) -> ir.Function:
    # Guess: ``!=`` (be-sv has no ``!==``); a 4-state field holding X or Z
    # compares unknown, so is not reported.
    a, b, diff = irb.ref("a"), irb.ref("b"), irb.ref("diff")
    body: List[ir.Stmt] = [irb.assign(diff, irb.const(""))]
    for f in names:
        fa, fb = irb.attr(a, f), irb.attr(b, f)
        ne = ir.ExprCompare(left=fa, ops=[ir.CmpOp.NotEq], comparators=[fb])
        msg = _fmt([irb.const(f" {f}: 0x%0h != 0x%0h"), fa, fb])
        body.append(irb.if_(ne, [irb.assign(diff, ir.ExprConcat(values=[diff, msg]))]))
    body.append(irb.ret(irb.eq(diff, irb.const(""))))
    return irb.method(f"{n}_compare",
                      [irb.arg("a", t), irb.arg("b", t),
                       irb.arg("diff", _STR(), SignalDirection.OUTPUT)],
                      body, returns=ir.DataTypeInt(bits=1, signed=False))


def _mask(n: str, t: ir.DataTypeRef) -> ir.Function:
    return irb.method(f"{n}_mask", [irb.arg("v", t)], [irb.ret(irb.ref("v"))],
                      returns=t)


def kit_decls(kit: Kit) -> List[ir.DataType]:
    out = []
    for prefix, structs in kit.structs.items():
        if not structs:
            continue
        fns = []
        for t, st in structs:
            n, names = short_name(t), [f.name for f in st.fields]
            fns += [_sprint(n, t, names), _compare(n, t, names), _mask(n, t)]
        for fn in fns:
            fn.metadata = dict(_STATIC)
        cls = ir.DataTypeClass(super=None, functions=fns)
        out.append(irb.template(payloads_name(prefix), [], cls))
    return out
