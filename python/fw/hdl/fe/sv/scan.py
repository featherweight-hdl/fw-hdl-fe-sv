"""Scan sources for their protocols, bundles and converters, with no top.

    from fw.hdl.fe.sv.scan import scan
    r = scan(["my_proto_pkg.sv"], incdirs=["inc"])
    if r.ok:
        table = r.table()       # as the netlist's ``protocols``

The registration members the macros declare (``get_type``,
``__fw_bundle_protocol``, ``__fw_xtor_protocol``, ``__fw_abs_from``) are
run through the interpreter, as elaboration runs them, but no component is
constructed. The classes visited are the non-generic classes of the
sources' packages and every class specialization their members, typedefs,
type parameters and base classes name. A generic class is seen only
through such specializations: an unspecialized one is skipped.
"""
from __future__ import annotations

import dataclasses as dc
import os
from typing import Any, Dict, List, Optional, Sequence

from zuspec.fe.sv import compile as sv_compile
from zuspec.fe.sv.interp import InterpError, Interpreter

from . import types as ty
from .api import Api
from .objects import Diag
from .rt import ElabError, FeIntrinsics, FeRt


@dc.dataclass
class ScanResult:
    rt: Optional[FeRt]
    diags: List[Diag]
    classes: List[str] = dc.field(default_factory=list)   # the classes visited
    #: generic bundle name -> its channels (``channels.BundleChannels``);
    #: filled by ``to_ir``.
    bundles: Dict[str, Any] = dc.field(default_factory=dict)
    #: the API (interface) classes declared in the sources' packages, by
    #: name: a kit's APIs, whether or not a protocol uses them (std's
    #: neutral APIs use none). Generic ones are the generic class.
    apis: Dict[str, Any] = dc.field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.rt is not None and not any(d.severity == "error" for d in self.diags)

    def table(self) -> Dict:
        """The protocols found, as the netlist's ``protocols`` table."""
        return self.rt.proto.table() if self.rt is not None else {}

    def xtor_for(self, bundle: str, kind: str = "bundle") -> Optional[str]:
        """The transactor that pairs with a ``kind`` (``bundle``, ``mirror``
        or ``monitor``) of the bundle type ``bundle`` (canonical spelling,
        ``test_ch_bundle#(8)``): the bundle's protocol, then its registered
        transactor of the opposite role (a monitor for a monitor tap).
        ``None`` if there is none among the scanned classes."""
        return find_xtor(self.table(), bundle, kind)

    def api_packages(self) -> List[str]:
        """The packages the protocols' API classes are declared in (from
        their generic class, if parameterized), in order found."""
        out: List[str] = []
        for rec in (self.rt.proto.recs.values() if self.rt is not None else []):
            for a in rec.apis.values():
                c = getattr(a.cls, "genericClass", None) or a.cls
                path = str(getattr(c, "lexicalPath", ""))
                if "::" in path and path.split("::", 1)[0] not in out:
                    out.append(path.split("::", 1)[0])
        for c in self.apis.values():
            path = str(getattr(c, "lexicalPath", ""))
            if "::" in path and path.split("::", 1)[0] not in out:
                out.append(path.split("::", 1)[0])
        return out

    def api_homes(self) -> Dict[str, str]:
        """Each protocol API's generic name -> the package it is declared in."""
        out: Dict[str, str] = {}
        for rec in (self.rt.proto.recs.values() if self.rt is not None else []):
            for a in rec.apis.values():
                c = getattr(a.cls, "genericClass", None) or a.cls
                path = str(getattr(c, "lexicalPath", ""))
                if "::" in path:
                    out[c.name] = path.split("::", 1)[0]
        return out

    def to_ir(self):
        """Zuspec IR of what was found: ``(Context, diags)``. ``type_m``
        holds each protocol's API interface classes, by their canonical
        spelling, as ``DataTypeProtocol``; a parameterized API's generic
        class, by its name, as a ``DataTypeParameterized`` over a
        ``DataTypeProtocol`` (zuspec-fe-sv ``map_generic_api``); then the
        named types their
        methods use (zuspec-fe-sv ``SVMapper``). A type the mapper rejects
        (a 4-state payload) is a warning: the protocols table stands."""
        import zuspec.ir.core as ir
        from zuspec.fe.sv.mapper import SVMapper
        m = SVMapper()
        m.class_mapper.compilation = self.rt.comp
        m.function_mapper.compilation = self.rt.comp
        ctx = ir.Context()
        for rec in self.rt.proto.recs.values():
            for a in rec.apis.values():
                if a.name not in ctx.type_m:
                    ctx.type_m[a.name] = m.class_mapper.map_protocol(a.cls, a.name)
                g = getattr(a.cls, "genericClass", None)
                if g is not None and g.name not in ctx.type_m:
                    ctx.type_m[g.name] = m.class_mapper.map_generic_api(g)
        for name, c in self.apis.items():
            if name not in ctx.type_m:
                ctx.type_m[name] = (m.class_mapper.map_generic_api(c)
                                    if type(c).__name__ == "GenericClassDefSymbol"
                                    else m.class_mapper.map_protocol(c, name))
        from .channels import bundle_channels
        table = self.table()
        for rec in self.rt.proto.recs.values():
            for bname, bcls in rec.bundle_cls.items():
                g = getattr(bcls, "genericClass", None)
                gname = g.name if g is not None else bname
                if gname not in self.bundles:
                    self.bundles[gname] = bundle_channels(
                        m.class_mapper, bcls, table[rec.name]["prefix"],
                        rec.name.split("#(", 1)[0])
        for k, t in m.get_types().items():
            ctx.type_m.setdefault(k, t)
        diags = [Diag("FW-W-SCAN-IR", "<scan>", str(e).strip(), "warning")
                 for e in m.error_reporter.errors]
        return ctx, diags


#: the transactor role that pairs with each bundle mixin
_OPPOSITE = {"bundle": "target", "mirror": "initiator", "monitor": "monitor"}


def find_xtor(table: Dict, bundle: str, kind: str = "bundle") -> Optional[str]:
    """In a ``protocols`` table, the transactor type for a ``kind`` of the
    bundle type ``bundle`` (see :meth:`ScanResult.xtor_for`)."""
    role = _OPPOSITE[kind]
    for p in table.values():
        if bundle in p.get("bundles", []):
            for c in p.get("converters", []):
                if c.get("role") == role and c["from"] == c["to"]:
                    return c["type"]
    return None


def scan(files: Sequence[str], incdirs: Sequence[str] = (),
         defines: Optional[Dict[str, str]] = None,
         spec: Optional[str] = None) -> ScanResult:
    api = Api(spec)
    cres = sv_compile(api.sources(files), incdirs=api.incdirs + list(incdirs),
                      defines=defines or {}, timescale="1ns/1ps")   # as elaborate()
    comp, sv_diags = cres
    if sv_diags.has_errors:
        return ScanResult(None, [Diag("FW-E-SV", "<compile>", sv_diags.report())])
    rt = FeRt(None, comp)
    rt.extern_methods = api.extern_methods
    rt.compile_result = cres
    rt.interp = Interpreter(comp, FeIntrinsics(rt))
    s = _Scan(rt)
    apis: Dict[str, Any] = {}
    # std's own APIs only when std is what is scanned.
    std = os.path.realpath(api.std_file)
    skip = {ty.API_PACKAGE, "std"}
    if not any(os.path.realpath(f) == std for f in files):
        skip.add(ty.STD_PACKAGE)
    for pkg in comp.getPackages():
        if pkg.name not in skip:
            for m in pkg:
                if getattr(m, "isInterface", False) and type(m).__name__ in (
                        "GenericClassDefSymbol", "ClassType"):
                    apis[m.name] = m
                s.visit_type(m)
    for unit in comp.getCompilationUnits():
        for m in unit:
            s.visit_type(m)
    return ScanResult(rt, rt.diags, s.order, apis=apis)


class _Scan:
    def __init__(self, rt: FeRt):
        self.rt = rt
        self.seen: set = set()
        self.order: List[str] = []
        self.base_only: set = set()

    def visit_type(self, t) -> None:
        """Visit the class ``t`` names, if any: a class, a typedef of one, a
        member or parameter whose type is one."""
        k = type(t).__name__
        if k == "GenericClassDefSymbol":
            return
        if k in ("TypeParameterSymbol", "TypeAliasType"):
            t = t.targetType.type
        elif k == "ClassPropertySymbol":
            t = t.type
        if t is None:
            return
        c = ty.canon(t)
        if getattr(c, "isClass", False):
            self.visit_class(c)

    def visit_class(self, cls, as_base: bool = False) -> None:
        """Visit ``cls``. A class reached only as another's base is not
        recorded as a bundle of its protocol (``test_phy_bundle extends
        fw_rv_if #(byte)`` does not make ``fw_rv_proto #(byte)``)."""
        name = ty.type_name(cls)
        if name in self.seen:
            if not as_base and name in self.base_only:
                self.base_only.discard(name)
                self.bundle(cls, name)
            return
        self.seen.add(name)
        if not ty.is_api_class(cls):
            self.order.append(name)
            self.register(cls, name)
            if as_base:
                self.base_only.add(name)
            else:
                self.bundle(cls, name)
        for m in cls:
            if type(m).__name__ in ("TypeParameterSymbol", "ClassPropertySymbol",
                                    "TypeAliasType"):
                self.visit_type(m)
        base = ty.canon(getattr(cls, "baseClass", None))
        if base is not None and getattr(base, "isClass", False):
            self.visit_class(base, as_base=True)

    def bundle(self, cls, name: str) -> None:
        try:
            self.rt.proto.bundle_class(cls, name)
        except (ElabError, InterpError) as e:
            self.rt.error("FW-E-ELAB", name, str(e))

    def register(self, cls, name: str) -> None:
        p = self.rt.proto
        try:
            if ty.api_base(cls, ("fw_protocol",)) and \
                    ty.find_member(cls, "get_type")[0]:
                p.check_this(cls)
                p._static(cls, "get_type")
            p.register_converter(cls, name)
        except (ElabError, InterpError) as e:
            self.rt.error("FW-E-ELAB", name, str(e))
