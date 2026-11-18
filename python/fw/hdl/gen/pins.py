"""``<prefix>_pins``: per bundle of channels, a class with the static
pin-map preset ``flat(m, b, pre)``: a prefix rule for bundle ``b`` whose
renames give each channel ``c`` the flat pins ``<pre>cvalid``,
``<pre>cready`` and one pin per payload field ``f``, ``<pre>cf`` (AXI
style: ``awaddr``, ``awlen``). A payload that is not a struct is one pin,
``<pre>cdata``. Role spellings (``m_axi_``, ``s_axi_``) are just ``pre``.

    void'(axi4_pins::flat(m, ports.m, "m_axi_"));

``b`` is an ``fw_object`` as in ``fw_extern_map::prefix``, so the class
needs none of the bundle's parameters. Declared once per kit
(``kit_decls``).
"""
from __future__ import annotations

from typing import Dict, List

import zuspec.ir.core as ir

from . import irb
from .kit import Kit


def pins_name(prefix: str) -> str:
    return prefix + "_pins"


def _rename(rule: ir.Expr, member: str, pin_part: str) -> ir.Expr:
    return ir.ExprCall(func=irb.attr(rule, "rename"),
                       args=[irb.const(member), irb.const(pin_part)])


def _renames(c, structs: Dict[str, ir.DataTypeStruct]) -> List[tuple]:
    out = [(f"{c.name}.valid", f"{c.name}valid"), (f"{c.name}.ready", f"{c.name}ready")]
    st = structs.get(getattr(c.payload, "ref_name", None))
    if st is None:
        out.append((f"{c.name}.data", f"{c.name}data"))
    else:
        out += [(f"{c.name}.data.{f.name}", f"{c.name}{f.name}") for f in st.fields]
    return out


def _flat(b, structs: Dict[str, ir.DataTypeStruct]) -> ir.Function:
    rule = ir.ExprCall(func=irb.attr(irb.ref("m"), "prefix"),
                       args=[irb.ref("b"), irb.ref("pre")])
    for c in b.channels:
        for member, pin_part in _renames(c, structs):
            rule = _rename(rule, member, pin_part)
    fn = irb.method("flat", [irb.arg("m", ir.DataTypeRef(ref_name="fw_extern_map")),
                             irb.arg("b", ir.DataTypeRef(ref_name="fw_object")),
                             irb.arg("pre", ir.DataTypeString())],
                    [irb.ret(rule)], returns=ir.DataTypeRef(ref_name="fw_pin_rule"))
    fn.metadata = {"static": True}
    return fn


def kit_decls(kit: Kit) -> List[ir.DataType]:
    out = []
    for b in kit.bundles:
        structs = {t.ref_name: st for t, st in kit.structs.get(b.prefix, [])}
        cls = ir.DataTypeClass(super=None, functions=[_flat(b, structs)])
        out.append(irb.template(pins_name(b.prefix), [], cls))
    return out
