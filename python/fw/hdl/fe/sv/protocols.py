"""Protocols on fe-sv (fw-hdl-spec semantics/protocols.md; protocol-kit
plan K4): the protocol registry, the registration macros, and the queries
of protocol and API handles.

:class:`FeRt` hands the calls of the protocol classes here (``rt.proto``).
A specialization is identified by its canonical spelling
(``test_rv_proto#(16)``, :func:`types.type_name`). Its record is made the
first time ``fw_protocol_type_of #(P)::get(family)`` runs (from
``P::get_type()``), which runs ``P::declare(d)`` in the interpreter.

The macros' members (``__fw_bundle_protocol``, ``__fw_xtor_protocol``,
``__fw_abs_from`` / ``_to``) are static SV functions; fe-sv
calls them when a bundle or a component of the class is created. So the
registry, like rt-sv's ``bundles()``, holds what the design instantiates.

A transactor's role is derived from its pin member (:func:`xtor_role`).

The queries that return constructors (``xtors``, ``abstractors``,
``abstractor_for``) are not implemented: fe-sv cannot make
an ``fw_ctor_of #(fw_component, C)`` for a class it did not see named.

``FW-E-PROTO-THIS`` is fe-sv's own check (a ``_param_utils`` class whose
``this_t`` is not its own specialization compiles in SV).
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Dict, List, Optional

from zuspec.fe.sv.interp.values import const_to_py, unwrap

from . import types as ty
from . import values as vx
from .objects import BUNDLE, MIRROR, MONITOR, FwBundle, FwComponent

DIRS = ("req", "rsp")                                   # fw_proto_dir_e
ROLES = ("initiator", "target", "monitor")              # fw_proto_role_e
_KIND_ROLE = {BUNDLE: 0, MIRROR: 1, MONITOR: 2}
_PIN_ROLE = {"fw_mirror": 1, "fw_monitor": 2, "fw_bundle": 0}


def xtor_role(cls) -> int:
    """A transactor's role (fw_proto_role_e), from the API pin class of its
    first such member (class chain, most derived first): ``fw_mirror`` ->
    target, ``fw_monitor`` -> monitor, ``fw_bundle`` -> initiator (as rt-sv's
    ``__fw_rt_proto::xtor_role``). Initiator when it has none."""
    for c in ty.class_chain(cls):
        for m in c:
            if type(m).__name__ != "ClassPropertySymbol" or getattr(m, "isStatic", False):
                continue
            t = ty.canon(m.type)
            if not getattr(t, "isClass", False):
                continue
            n = ty.api_base(t, _PIN_ROLE)
            if n is not None:
                return _PIN_ROLE[n]
    return 0


@dc.dataclass(eq=False)
class ProtoRec:
    """One protocol specialization."""
    name: str                   # canonical spelling, the protocols-table key
    family: str
    cls: Any                    # the protocol ClassType
    handle: Any                 # its fw_protocol_type_of #(P) object
    params: Dict[str, Any] = dc.field(default_factory=dict)
    apis: Dict[int, "ApiRec"] = dc.field(default_factory=dict)   # dir -> API
    bundles: List[str] = dc.field(default_factory=list)
    bundle_cls: Dict[str, Any] = dc.field(default_factory=dict)  # bundle name -> ClassType
    converters: List[dict] = dc.field(default_factory=list)     # netlist form
    conv_cls: List[tuple] = dc.field(default_factory=list)      # (role or None, class)


@dc.dataclass(eq=False)
class ApiRec:
    name: str                   # canonical spelling of the interface class
    cls: Any
    handle: Any
    protocols: List[ProtoRec] = dc.field(default_factory=list)


@dc.dataclass(eq=False)
class FamilyRec:
    name: str
    prefix: str
    handle: Any


class Protocols:
    """The protocol state of one elaboration."""

    def __init__(self, rt):
        self.rt = rt
        self.recs: Dict[str, ProtoRec] = {}        # specialization -> record
        self.families: Dict[str, FamilyRec] = {}
        self.apis: Dict[str, ApiRec] = {}
        self._by_h: Dict[int, Any] = {}           # id(handle object) -> its record
        self._decl: Dict[int, ProtoRec] = {}      # id(fw_protocol_decl object) -> record
        self._conv_done: Dict[str, Any] = {}    # converter class -> its protocol (xtor)
        self._this_checked: set = set()

    # -- handles ------------------------------------------------------------
    def rec_of(self, h) -> Any:
        return self._by_h.get(id(unwrap(h))) if h is not None else None

    def _alloc(self, cls, rec) -> Any:
        h = self.rt.interp.allocate(ty.canon(cls))
        self._by_h[id(h)] = rec
        return h

    def family(self, name: str) -> FamilyRec:
        f = self.families.get(name)
        if f is None:
            pre = name[:-len("_proto")] if name.endswith("_proto") else name
            f = FamilyRec(name, pre, None)
            f.handle = self._alloc(self.rt.pkg.find("fw_protocol_family"), f)
            self.families[name] = f
        return f

    def protocol_get(self, owner, family) -> Any:
        """``fw_protocol_type_of #(P)::get(family)``: P's handle, making its
        record (and running ``P::declare``) the first time."""
        p = ty.type_param(owner, "P")
        family = vx.to_str(family)
        self._check_caller_this(p, family)
        key = ty.type_name(p)
        rec = self.recs.get(key)
        if rec is not None:
            return rec.handle
        rec = ProtoRec(name=key, family=family, cls=ty.canon(p), handle=None,
                       params=class_params(p))
        rec.handle = self._alloc(owner, rec)
        self.recs[key] = rec
        self.family(family)
        it = self.rt.interp
        d = it.allocate(ty.canon(self.rt.pkg.find("fw_protocol_decl")))
        self._decl[id(d)] = rec
        sub = it.find_method(rec.cls, "declare")
        if sub is not None and getattr(sub, "body", None) is not None:
            it.call(sub, None, [d])
        return rec.handle

    def api_get(self, owner) -> Any:
        """``fw_api_type_of #(API)::get()``."""
        t = ty.type_param(owner, "API")
        key = ty.type_name(t, aliases=True)
        a = self.apis.get(key)
        if a is None:
            a = ApiRec(key, ty.canon(t), None)
            a.handle = self._alloc(owner, a)
            self.apis[key] = a
        return a.handle

    # -- fw_protocol_decl ---------------------------------------------------
    def decl_api(self, d, h, dir) -> None:
        rec = self._decl.get(id(unwrap(d)))
        a = self.rec_of(h)
        if rec is None or not isinstance(a, ApiRec):
            return
        dr = vx.to_int(dir)
        if dr in rec.apis or a in rec.apis.values():
            self.rt.error("FW-E-PROTO-API", rec.name,
                          f"{rec.name}: two APIs for {DIRS[dr]}, or one API twice")
            return
        rec.apis[dr] = a
        a.protocols.append(rec)

    def decl_prefix(self, d, s) -> None:
        rec = self._decl.get(id(unwrap(d)))
        if rec is not None:
            self.families[rec.family].prefix = vx.to_str(s)

    # -- registration from the macros' members -------------------------------
    def _static(self, cls, name: str):
        it = self.rt.interp
        return it.call(it.find_method(ty.canon(cls), name), None, [])

    def bundle_created(self, b: FwBundle) -> None:
        """A bundle whose type has ``fw_bundle_protocol``."""
        rec = self.bundle_class(b.btype, b.btype_name)
        if rec is not None:
            b.proto = rec

    def bundle_class(self, btype, btype_name: str) -> Any:
        """Record bundle class ``btype`` on its protocol; the protocol's
        record, or None if the class has no ``fw_bundle_protocol``."""
        if not ty.find_member(btype, "__fw_bundle_protocol")[0]:
            return None
        rec = self.rec_of(self._static(btype, "__fw_bundle_protocol"))
        if rec is None:
            return None
        if btype_name not in rec.bundles:
            rec.bundles.append(btype_name)
            rec.bundle_cls[btype_name] = btype
        return rec

    def component_created(self, c: FwComponent) -> None:
        """A transactor or abstractor class registers as a converter."""
        self.check_this(c.cls)
        c.proto = self.register_converter(c.cls, c.type_name)

    def register_converter(self, cls, type_name: str) -> Any:
        """Register ``cls`` as a converter if it is a transactor (its
        protocol is returned) or an abstractor (None); once per class."""
        if type_name in self._conv_done:
            return self._conv_done[type_name]
        self._conv_done[type_name] = None
        if ty.find_member(cls, "__fw_xtor_protocol")[0]:
            rec = self.rec_of(self._static(cls, "__fw_xtor_protocol"))
            role = xtor_role(cls)
            if rec is not None:
                self._conv_done[type_name] = rec
                rec.conv_cls.append((role, cls))
                rec.converters.append({"type": type_name, "from": rec.name,
                                       "to": rec.name, "role": ROLES[role]})
        elif ty.find_member(cls, "__fw_abs_from")[0]:
            f = self.rec_of(self._static(cls, "__fw_abs_from"))
            t = self.rec_of(self._static(cls, "__fw_abs_to"))
            if f is not None and t is not None:
                e = {"type": type_name, "from": f.name, "to": t.name}
                for r in {id(f): f, id(t): t}.values():
                    r.conv_cls.append((None, cls))
                    r.converters.append(e)
        return self._conv_done[type_name]

    # -- FW-E-PROTO-THIS ----------------------------------------------------
    def check_this(self, cls, family: Optional[str] = None) -> None:
        """A specialization that declares ``this_t`` must be its own."""
        cls = ty.canon(cls)
        key = ty.type_name(cls)
        if key in self._this_checked or getattr(cls, "genericClass", None) is None:
            return
        self._this_checked.add(key)
        m = cls.find("this_t")
        if m is None or type(m).__name__ != "TypeAliasType":
            return
        if ty.type_name(m.targetType.type) != key:
            fam = family or cls.genericClass.name
            self.rt.error("FW-E-PROTO-THIS", fam,
                          f"{fam}: this_t is {ty.type_name(m.targetType.type)}, not {key}")

    def _check_caller_this(self, p, family: str) -> None:
        """``get(family)`` runs in ``P::get_type()``; when the class whose
        ``get_type`` runs is a specialization of the family other than P,
        its ``this_t`` names another specialization."""
        st = self.rt.interp.stack
        if not st or st[-1].sub is None:
            return
        caller = self.rt.interp.owner_class(st[-1].sub)
        if caller is not None:
            self.check_this(caller, family)

    # -- queries --------------------------------------------------------------
    def family_query(self, this, name: str) -> Any:
        f = self.rec_of(this)
        return f.name if name == "name" else f.prefix

    def api_query(self, this, name: str) -> Any:
        a = self.rec_of(this)
        if name == "name":
            return a.name
        if name == "family":
            c = a.cls
            return c.genericClass.name if c.genericClass is not None else c.name
        return [p.handle for p in a.protocols]

    def protocol_query(self, this, name: str, a) -> Any:
        rec = self.rec_of(this)
        if name == "name":
            return rec.name
        if name == "family":
            return self.families[rec.family].handle
        if name == "params":
            return rec.name[rec.name.index("#(") + 2:-1] if "#(" in rec.name else ""
        if name == "api":
            api = rec.apis.get(vx.to_int(a[0]))
            return api.handle if api is not None else None
        if name == "apis":
            return [rec.apis[d].handle for d in sorted(rec.apis)]
        if name == "bundles":
            return list(rec.bundles)
        return None

    def of(self, x) -> Any:
        """``fw_protocol::of``: a bundle's protocol, or a transactor's."""
        o = self.rt.fw(unwrap(x))
        rec = getattr(o, "proto", None) if o is not None else None
        return rec.handle if rec is not None else None

    def role_of(self, b) -> int:
        o = self.rt.fw(unwrap(b))
        return _KIND_ROLE.get(getattr(o, "kind", ""), 0)

    # -- netlist ----------------------------------------------------------
    def table(self) -> Dict[str, Any]:
        """The netlist's ``protocols`` table (protocols.md, Netlist)."""
        out = {}
        for k, r in self.recs.items():
            out[k] = {
                "family": r.family, "params": dict(r.params),
                "prefix": self.families[r.family].prefix,
                "apis": [{"dir": DIRS[d], "type": r.apis[d].name} for d in sorted(r.apis)],
                "bundles": sorted(r.bundles),
                "converters": sorted(r.converters, key=lambda e: (e["type"], e["from"])),
            }
        return out


def class_params(cls) -> Dict[str, Any]:
    """A specialization's parameters by name: values as Python ints (or
    strings), type parameters as their canonical spelling."""
    out = {}
    for m in ty.canon(cls):
        k = type(m).__name__
        if k == "TypeParameterSymbol" and not m.isLocalParam:
            out[m.name] = ty.type_name(m.targetType.type)
        elif k == "ParameterSymbol" and not m.isLocalParam:
            out[m.name] = const_to_py(m.value)
    return out
