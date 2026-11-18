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

:func:`walk_port` is the one walk over a port's bundle tree (pins with
their directions, the outermost protocol levels) that the testbench
generators (``fw.hdl.gen.tb``, ``.bind``) share.

The queries that return constructors (``abstractors``,
``abstractor_for``) answer from the converter classes the design names
(protocols.md, "Completeness"), not only those it creates: the first such
query registers every converter class a scan of the compiled packages
visits (:func:`scan.register_named`). The constructor is the class's
``__fw_ctor()`` (fe-sv's macros), an ``fw_ctor_of #(fw_component, C)``
served by ``FeRt.ctor_get``.

``FW-E-PROTO-THIS`` is fe-sv's own check (a ``_param_utils`` class whose
``this_t`` is not its own specialization compiles in SV).
``FW-E-PROTO-XTOR`` (a converter that does not connect what it registers
for) is checked on the class when it registers (rt-sv checks each created
converter's children).
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Dict, List, Optional, Tuple

from zuspec.fe.sv.interp.values import const_to_py, unwrap

from . import types as ty
from . import values as vx
from .objects import BUNDLE, MIRROR, MONITOR, FwBundle, FwComponent, FwSignal

DIRS = ("req", "rsp")                                   # fw_proto_dir_e
ROLES = ("initiator", "target", "monitor")              # fw_proto_role_e
_KIND_ROLE = {BUNDLE: 0, MIRROR: 1, MONITOR: 2}
_PIN_ROLE = {"fw_mirror": 1, "fw_monitor": 2, "fw_bundle": 0}


def xtor_pins(cls) -> Tuple[int, Optional[str]]:
    """A transactor's role (fw_proto_role_e) and the name of its pin member:
    the first member (class chain, most derived first) whose type is an
    API pin class. ``fw_mirror`` -> target, ``fw_monitor`` -> monitor,
    ``fw_bundle`` -> initiator (as rt-sv's ``__fw_rt_proto::xtor_role``).
    ``(initiator, None)`` when it has none."""
    for c in ty.class_chain(cls):
        for m in c:
            if type(m).__name__ != "ClassPropertySymbol" or getattr(m, "isStatic", False):
                continue
            t = ty.canon(m.type)
            if not getattr(t, "isClass", False):
                continue
            n = ty.api_base(t, _PIN_ROLE)
            if n is not None:
                return _PIN_ROLE[n], m.name
    return 0, None


def xtor_role(cls) -> int:
    """A transactor's role (fw_proto_role_e), from its pin member
    (:func:`xtor_pins`). Initiator when it has none."""
    return xtor_pins(cls)[0]


# -- the bundle tree of a port (F10) -------------------------------------------

#: the mixin seen through one more ``fw_mirror``
_FLIP_KIND = {BUNDLE: MIRROR, MIRROR: BUNDLE, MONITOR: MONITOR}


@dc.dataclass(eq=False)
class PortPin:
    """One leaf signal of a port, as its owner sees it."""
    sig: FwSignal
    path: List[str]             # member path from the owner: ["s", "req", "valid"]
    rel: List[str]              # member path within the port: ["req", "valid"]
    dir: str                    # "in", "out" or "inout", for the port's owner

    @property
    def name(self) -> str:
        """The pin's name: the member path joined with ``_`` (the emitter's)."""
        return "_".join(self.path)


@dc.dataclass(eq=False)
class ProtoLevel:
    """A level of a port's bundle tree that carries a protocol."""
    bundle: FwBundle
    rel: List[str]              # member path within the port ([] for the port)
    kind: str                   # its mixin as the owner sees it (bundle, mirror, monitor)
    proto: Any                  # ProtoRec


@dc.dataclass(eq=False)
class PortWalk:
    """A port's bundle tree, walked once (:func:`walk_port`)."""
    port: FwBundle
    path: List[str]             # member path from the owner: ["s"]
    #: the outermost levels that carry a protocol: the port alone when it
    #: has one; else, per branch, the first bundle down that has one.
    levels: List[ProtoLevel]
    pins: List[PortPin]         # every leaf signal, in declaration order

    @property
    def name(self) -> str:
        return "_".join(self.path)


def walk_port(port: FwBundle, owner: Any = None) -> PortWalk:
    """Walk the bundle tree of ``port`` (a bundle, mirror or monitor of
    ``owner``, by default its parent): per leaf signal its member path and
    its direction for the owner (reversed at each ``fw_mirror`` on the path,
    the port included; an input under a monitor); and the outermost levels
    that carry a protocol, each with its mixin as the owner sees it. The one
    query gen_tb and gen_bind build a port's transactors and bridges from."""
    owner = owner if owner is not None else port.parent
    base = port.rel_path(owner)
    levels: List[ProtoLevel] = []
    pins: List[PortPin] = []

    def visit(b: FwBundle, rel: List[str], kind: str, found: bool) -> None:
        if not found and b.proto is not None:
            levels.append(ProtoLevel(b, rel, kind, b.proto))
            found = True
        for c in b.children.values():
            if isinstance(c, FwBundle):
                k = MONITOR if MONITOR in (kind, c.kind) else \
                    (_FLIP_KIND[kind] if c.kind == MIRROR else kind)
                visit(c, rel + [c.name], k, found)
            elif isinstance(c, FwSignal) and c.dir is not None:
                d = c.dir
                if kind == MONITOR:
                    d = "in"
                elif kind == MIRROR and d != "inout":
                    d = "in" if d == "out" else "out"
                pins.append(PortPin(c, base + rel + [c.name], rel + [c.name], d))

    visit(port, [], port.kind, False)
    return PortWalk(port, base, levels, pins)


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
        self._conv_cls: Dict[str, Any] = {}     # converter type name -> its class
        self._named = False                     # register_named() has run
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
                self._conv_cls[type_name] = cls
                rec.conv_cls.append((role, cls))
                rec.converters.append({"type": type_name, "from": rec.name,
                                       "to": rec.name, "role": ROLES[role]})
                self._check_sides(cls, [rec], api=(role != 2), bundle=True)
        elif ty.find_member(cls, "__fw_abs_from")[0]:
            f = self.rec_of(self._static(cls, "__fw_abs_from"))
            t = self.rec_of(self._static(cls, "__fw_abs_to"))
            if f is not None and t is not None:
                self._conv_cls[type_name] = cls
                e = {"type": type_name, "from": f.name, "to": t.name}
                for r in {id(f): f, id(t): t}.values():
                    r.conv_cls.append((None, cls))
                    r.converters.append(e)
                self._check_sides(cls, [f, t])
        return self._conv_done[type_name]

    # -- FW-E-PROTO-XTOR ----------------------------------------------------
    def _check_sides(self, cls, recs, api: bool = True, bundle: bool = True) -> None:
        """A converter must connect what it registers for (protocols.md,
        "Converters"): a transactor a bundle port of its protocol and (but
        a monitor) an endpoint of one of its APIs or of its channel APIs;
        an abstractor an endpoint or bundle port of each side. Checked on
        the class's members (base classes included), once per class."""
        bundles, apis = self._conv_members(cls)

        def api_of(r):
            # Its registered APIs, and the channel APIs derived from its
            # bundles of channels (``<p>_req_if``, ``<p>_rsp_if``).
            pre = self.family(r.family).prefix
            return {ty.type_name(a.cls) for a in r.apis.values()} | \
                {pre + "_req_if", pre + "_rsp_if"}
        missing = []
        for r in recs:
            has_b, has_a = r.name in bundles, bool(api_of(r) & apis)
            if len(recs) == 1:              # a transactor
                if bundle and not has_b:
                    missing.append(f"a bundle port of {r.name}")
                if api and not has_a:
                    missing.append(f"an endpoint of an API of {r.name}")
            elif not (has_b or has_a):
                missing.append(f"an endpoint or bundle port of {r.name}")
        if missing:
            cls = ty.canon(cls)
            g = getattr(cls, "genericClass", None)
            fam = g.name if g is not None else cls.name
            self.rt.error("FW-E-PROTO-XTOR", fam, f"{fam}: missing {' and '.join(missing)}")

    def _conv_members(self, cls):
        """The protocols of a class's bundle ports and the API types of its
        endpoints (canonical spellings)."""
        bundles, apis = set(), set()
        for c in ty.class_chain(cls):
            for m in c:
                if type(m).__name__ != "ClassPropertySymbol" or getattr(m, "isStatic", False):
                    continue
                t = ty.canon(m.type)
                if not getattr(t, "isClass", False):
                    continue
                if ty.api_base(t, _PIN_ROLE) is not None:
                    b = ty.type_param(t, "B")
                    if b is not None and ty.find_member(b, "__fw_bundle_protocol")[0]:
                        r = self.rec_of(self._static(b, "__fw_bundle_protocol"))
                        if r is not None:
                            bundles.add(r.name)
                elif ty.api_base(t, ("fw_port", "fw_export", "fw_socket")) is not None:
                    for pn in ("API", "OUT", "IN"):
                        a = ty.type_param(t, pn)
                        if a is not None:
                            g = getattr(a, "genericClass", None)
                            apis.update((ty.type_name(a), g.name if g is not None else getattr(a, "name", "")))
        return bundles, apis

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

    def _named_convs(self) -> None:
        if not self._named:
            self._named = True
            from .scan import register_named
            register_named(self.rt)

    def _ctor(self, type_name: str) -> Any:
        cls = self._conv_cls[type_name]
        if not ty.find_member(cls, "__fw_ctor")[0]:
            from .rt import ElabError
            raise ElabError(f"{type_name} has no __fw_ctor (not fe-sv's macros)")
        return self._static(cls, "__fw_ctor")

    def abstractors(self, this) -> List[Any]:
        """``p.abstractors()``: constructors of the abstractors with ``p``
        on either side, in registration order."""
        self._named_convs()
        rec = self.rec_of(this)
        return [self._ctor(e["type"]) for e in rec.converters if "role" not in e]

    def abstractor_for(self, f, t) -> Any:
        """``fw_protocol::abstractor_for(from, to)``: a constructor of an
        abstractor between the two, in either direction; None if none."""
        self._named_convs()
        a, b = self.rec_of(f), self.rec_of(t)
        if a is None or b is None:
            return None
        for e in a.converters:
            if "role" not in e and {e["from"], e["to"]} == {a.name, b.name}:
                return self._ctor(e["type"])
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
