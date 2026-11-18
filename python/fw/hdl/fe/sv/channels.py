"""The channels of a protocol bundle, for the kit's channel APIs.

A protocol bundle (a class with ``fw_bundle_protocol``) may be a bundle of
channels: members ``fw_bundle #(fw_rv_if #(T))`` (fwd, sent by the
initiator) and ``fw_mirror #(fw_rv_if #(T))`` (rev, sent by the target).
Direction follows from the member's role and the payload from the
channel's type parameter, so nothing is written for them (prep doc §3).
A bundle with no such member (one that *is* an ``fw_rv_if``, or of plain
signals) has no channels.

The payload is mapped over the bundle's own parameters: a width written
``logic [W-1:0]`` keeps ``W`` (``DataTypeInt.width_expr``).
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, List, Optional

import zuspec.ir.core as ir

#: Channel families: the bundle classes a channel member's type may be.
CHANNEL_FAMILIES = ("fw_rv_if",)
_ROLES = {"fw_bundle": True, "fw_mirror": False}     # generic name -> fwd


@dc.dataclass
class Channel:
    name: str
    fwd: bool
    family: str                    # fw_rv_if
    payload: ir.DataType           # over the bundle's parameters


@dc.dataclass
class BundleChannels:
    name: str                      # the generic bundle name: test_ch_bundle
    tmpl: List[ir.TemplateParam]   # its parameters, or []
    prefix: str                    # its protocol's prefix: test_ch
    channels: List[Channel]
    proto: str = ""                # its protocol's generic name: test_ch_proto


def bundle_channels(cm, bcls, prefix: str, proto: str = "") -> BundleChannels:
    """The channels of bundle class ``bcls`` (a slang ClassType), mapped
    with zuspec-fe-sv ClassMapper ``cm``. ``proto`` is the bundle's
    protocol's generic name (``fw_bundle_protocol``'s), for the xtor
    generator's ``fw_xtor_param_utils`` registration."""
    from zuspec.fe.sv.class_mapper import _default_specialization
    g = getattr(bcls, "genericClass", None)
    spec, params, tparams = bcls, [], {}
    if g is not None:
        spec = _default_specialization(g)
        params, tparams = cm._template_params(spec)
    saved = (cm._tparams, cm._body_scope)
    cm._tparams, cm._body_scope = tparams, spec
    try:
        chans = [c for c in (_channel(cm, m, spec) for m in spec) if c is not None]
    finally:
        cm._tparams, cm._body_scope = saved
    return BundleChannels(g.name if g is not None else bcls.name, params, prefix, chans, proto)


def _channel(cm, mem, scope) -> Optional[Channel]:
    if type(mem).__name__ != "ClassPropertySymbol":
        return None
    role = _generic_name(mem.type)
    if role not in _ROLES:
        return None
    inner = _type_arg(mem.type)
    family = _generic_name(inner)
    if family not in CHANNEL_FAMILIES:
        return None
    pt = _type_arg(inner)
    payload = _map_payload(cm, pt)
    tsyn = _arg_syntax(_arg_syntax(mem.declaredType.typeSyntax))
    if isinstance(payload, ir.DataTypeInt) and tsyn is not None:
        from zuspec.fe.sv.module_header import width_expr
        we = width_expr(tsyn, pt, scope)
        if we is not None and not isinstance(we, ir.ExprConstant):
            payload.width_expr = we
    return Channel(mem.name, _ROLES[role], family, payload)


def _map_payload(cm, t) -> Optional[ir.DataType]:
    """Payload type ``t`` in IR; a 4-state integral (``logic [7:0]``) is
    accepted, as the pins carry it, and marked ``four_state``."""
    if t.isIntegral and t.isFourState and not getattr(t, "isAlias", False) \
            and not t.isStruct:
        return ir.DataTypeInt(bits=int(t.bitWidth), signed=bool(t.isSigned),
                              four_state=True)
    return cm._map_type(t)


def _generic_name(t) -> Optional[str]:
    g = getattr(t, "genericClass", None)
    return g.name if g is not None else None


def _type_arg(cls) -> Any:
    """The (first) type parameter's value of specialization ``cls``."""
    for m in cls:
        if type(m).__name__ == "TypeParameterSymbol" and m.isPortParam:
            return m.targetType.type
    return None


def _arg_syntax(tsyn) -> Any:
    """The syntax of the first parameter value in ``C #(<arg>)`` (a
    ``NamedType``, or the ``ClassName`` a nested type argument is)."""
    try:
        cn = tsyn if tsyn.kind.name == "ClassName" else tsyn.name
        return cn.parameters.parameters[0].expr
    except (AttributeError, IndexError):
        return None
