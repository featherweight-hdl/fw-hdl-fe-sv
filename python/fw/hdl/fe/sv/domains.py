"""Domains on fe-sv (fw-hdl-spec semantics/domains.md; core-domains-plan
§6.1): domain objects, associations and their lookup, drivers, extern
domain pins, waivers, and the checks made at the end of elaboration.

:class:`FeRt` hands the calls of ``fw_domain #(K)`` and the kind handles
here (``rt.dom``). A kind is identified by its name, the kind class's
canonical spelling (``fw_clock_kind``). A kind's own rules (``physical()``,
``severity(r)``) are the kind's static SV functions, run in the
interpreter. The relation of two domains is zuspec-synth's
(:func:`zuspec.synth.passes.domains.relate`), over fe-sv's objects.

The crossing check and realization are Zuspec passes (zuspec-synth
``passes.domains``, §6.3): :func:`passes` runs them at the end of
elaboration over the design's concrete IR, and reports what they find
(crossings, unmatched waivers, ``FW-E-DOM-NODRIVER``,
``FW-E-DOM-PORTNAME``). Waivers are recorded here and matched there.

Provenance. Two associations can resolve to the same domain at an instance
(a FIFO's "wr" and "rd" clocks, both the parent's clock), while the class's
code means one of them. So ``get`` returns its handle traced
(``Sym(handle, DomainRef)``, the ref it was read through, in the calling
component's terms), and ``set``, ``pin``, ``create`` and ``provides``
record that ref when the component that made it uses it (:meth:`ref_of`).
ir_map writes the recorded ref, and only falls back to finding a ref that
gives the domain when there is none.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import zuspec.ir.core as ir
from zuspec.ir.core.domain import DomainRel
from zuspec.fe.sv.interp.values import Sym, strip, unwrap
from zuspec.synth.passes.domains import relate as _relate

from . import types as ty
from . import values as vx
from .objects import (Assoc, Diag, DomPin, Driver, FwBundle, FwComponent, FwDomain,
                      FwObject, FwSignal, Waiver)

CLOCK = "fw_clock_kind"
RESET = "fw_reset_kind"

# fw_domain_rel_e and fw_severity_e, as the interpreter holds them.
REL_SV = {DomainRel.SAME: 0, DomainRel.RELATED: 1, DomainRel.UNRELATED: 2}
SEV_NAMES = ("none", "info", "warning", "error")
RESET_STYLE_ANY = 2           # fw_reset_kind::ANY


class _Graph:
    """fe-sv's domain objects as zuspec-synth's ``DomainGraph``."""

    @staticmethod
    def sources(d: FwDomain):
        return d.sources

    @staticmethod
    def key(d: FwDomain):
        return id(d)


def relate(a: FwDomain, b: FwDomain) -> DomainRel:
    return _relate(_Graph, a, b)


def kind_name(kclass) -> str:
    return ty.type_name(kclass)


def get(o: Optional[FwObject], kname: str, name: str = "", mark: bool = False
        ) -> Optional[FwDomain]:
    """The domain ``o`` resolves to for kind ``kname`` (domains.md, Lookup).
    ``mark``: a read of a named association."""
    if o is None:
        return None
    if isinstance(o, FwComponent):
        if name and (kname, name) in o.assoc:
            if mark:
                o.aread.add((kname, name))
            return o.assoc[(kname, name)].dom
        c = o
        while c is not None:
            a = c.assoc.get((kname, ""))
            if a is not None:
                return a.dom
            c = c.parent
        return None
    c = o
    while c is not None and not isinstance(c, FwComponent):
        a = c.assoc.get((kname, ""))
        if a is not None:
            return a.dom
        c = c.parent
    return get(o.scope, kname)


def iface(o: FwObject) -> FwObject:
    """An endpoint's interface: its outermost enclosing bundle, else itself."""
    r, p = o, o.parent
    while p is not None and not isinstance(p, FwComponent):
        if isinstance(p, FwBundle):
            r = p
        p = p.parent
    return r


class Domains:
    """The domain state of one elaboration."""

    def __init__(self, rt):
        self.rt = rt
        self._handles: Dict[str, Any] = {}       # kind name -> handle (interpreter object)
        self._hkind: Dict[int, Any] = {}         # id(handle) -> kind class
        self.kinds: Dict[str, Any] = {}          # kind name -> kind class, every kind used
        self.waivers: List[Waiver] = []
        self.realization = None              # zuspec-synth realize(), from passes()
        self.severity_opt = {}               # the crossing-severity option (elaborate())
        self._phys: Dict[str, bool] = {}
        self._sev: Dict[tuple, str] = {}
        self._refs: Dict[int, tuple] = {}        # id(DomainRef) -> (component, ref), from get

    # -- kinds ------------------------------------------------------------
    def _kind(self, kclass) -> str:
        k = kind_name(kclass)
        self.kinds.setdefault(k, ty.canon(kclass))
        return k

    def handle(self, type_of_cls) -> Any:
        """``fw_domain_kind_type_of #(T)::get()``: the kind's handle."""
        t = ty.type_param(type_of_cls, "T")
        k = self._kind(t)
        if k not in self._handles:
            h = self.rt.interp.allocate(ty.canon(type_of_cls))
            self._handles[k] = h
            self._hkind[id(h)] = t
        return self._handles[k]

    def handle_name(self, h) -> str:
        return kind_name(ty.type_param(h.cls, "T"))

    def _static(self, kclass, name: str, args=()):
        it = self.rt.interp
        return it.call(it.find_method(ty.canon(kclass), name), None, list(args))

    def physical(self, kname: str) -> bool:
        if kname not in self._phys:
            self._phys[kname] = bool(vx.to_int(self._static(self.kinds[kname], "physical")))
        return self._phys[kname]

    def severity(self, kname: str, rel: DomainRel) -> str:
        """The kind's default severity of a crossing: none | info | warning
        | error."""
        key = (kname, rel)
        if key not in self._sev:
            v = vx.to_int(self._static(self.kinds[kname], "severity", [REL_SV[rel]]))
            self._sev[key] = SEV_NAMES[v]
        return self._sev[key]

    def pin_default(self, kname: str) -> Any:
        """``K::pin_default()``."""
        return strip(self._static(self.kinds[kname], "pin_default"))

    def kind_port_name(self, kname: str, assoc: str, attr) -> str:
        """``K::port_name(assoc, attr)``: a generated port's name."""
        return vx.to_str(self._static(self.kinds[kname], "port_name", [assoc, vx.copy(attr)]))

    # -- creation ---------------------------------------------------------
    def create(self, owner, sub, name, sv_parent, p) -> Any:
        rt = self.rt
        kclass = ty.type_param(owner, "K")
        kname = self._kind(kclass)
        name_sym = name if hasattr(name, "expr") else None
        name = vx.to_str(name)
        parent = rt._parent(unwrap(sv_parent), name)
        if parent is None:
            from .rt import ElabError
            raise ElabError(f"domain {name!r} has no parent")
        refs = self._source_refs(p)
        p = strip(p)
        ptype = list(sub.arguments)[2].type
        sv = rt.interp.allocate(ty.canon(owner))
        d = FwDomain(name=name, parent=parent, sv=sv, cls=ty.canon(owner),
                     type_name=ty.type_name(owner), kclass=ty.canon(kclass), kname=kname,
                     params=p, params_type=ptype, ctx=rt.ctx(), name_sym=name_sym)
        path = d.path
        bad = False
        for s in vx.elements(vx.field(p, "from")):
            s = unwrap(s)
            src = rt.fw(s) if s is not None else None
            if s is None:
                rt.error("FW-E-DOM-NONE", path, "a derivation source is null")
                bad = True
            elif not isinstance(src, FwDomain) or src.kname != kname:
                rt.error("FW-E-DOM-KIND", path, f"a source is not a domain of kind {kname}")
                bad = True
            else:
                d.sources.append(src)
                d.source_refs.append(refs[len(d.sources) - 1]
                                     if len(refs) >= len(d.sources) else None)
        if kname == RESET:
            ch = unwrap(vx.field(p, "clock"))
            clk = rt.fw(ch) if ch is not None else None
            if ch is not None and (not isinstance(clk, FwDomain) or clk.kname != CLOCK):
                rt.error("FW-E-DOM-KIND", path, "the reset's clock is not a clock domain")
                bad = True
            d.clock = clk
        if bad:
            d.detached = True
            rt._by_sv[id(sv)] = d
        elif not rt._register(d):
            d.detached = True
        rt.interp.run_ctor(sv, ty.canon(owner))
        return sv

    # -- association and lookup -------------------------------------------
    def set(self, owner, obj_sv, dom_sv, name) -> None:
        rt = self.rt
        kname = self._kind(ty.type_param(owner, "K"))
        name = vx.to_str(name)
        o = rt._used(unwrap(obj_sv), "a domain's object")
        if o is None or getattr(o, "detached", False):
            return
        c = rt.current
        if rt.phase != "build" or c is None:
            rt.error("FW-E-DOM-PHASE", o.path, "set outside the build phase")
            return
        if not (o is c or (o.parent is c and (isinstance(o, (FwComponent, FwBundle))
                                              or (isinstance(o, FwSignal) and o.is_port)))):
            rt.error("FW-E-DOM-OBJ", o.path, f"not the calling component ({c.path}), a direct "
                     f"child, or one of its interfaces")
            return
        if name and not isinstance(o, FwComponent):
            rt.error("FW-E-DOM-OBJ", o.path, "a named association on an interface")
            return
        d = rt.fw(unwrap(dom_sv))
        if not isinstance(d, FwDomain) or d.detached:
            return
        if (kname, name) in o.assoc:
            rt.error("FW-E-DOM-BIND", o.path, f"already associated ({kname}/{name})")
            return
        a = Assoc(c, o, kname, name, d, ctx=rt.ctx(), ref=self.ref_of(dom_sv))
        o.assoc[(kname, name)] = a
        c.dassocs.append(a)

    def get(self, owner, obj_sv, name) -> Any:
        rt = self.rt
        kname = self._kind(ty.type_param(owner, "K"))
        name = vx.to_str(name)
        o = rt._used(unwrap(obj_sv), "a domain's object")
        if o is None:
            return None
        if name and not isinstance(o, FwComponent):
            rt.error("FW-E-DOM-OBJ", o.path, "a named association on an interface")
            return None
        d = get(o, kname, name, mark=True)
        if d is None:
            return None
        c = rt.current
        if c is None or not (o is c or o.parent is c or getattr(o, "scope", None) is c):
            return d.sv
        ref = ir.DomainRef(kind=kname, assoc=name,
                           target=[] if o is c else list(o.rel_path(c)))
        self._refs[id(ref)] = (c, ref)
        return Sym(d.sv, ref)

    def ref_of(self, v) -> Optional[ir.DomainRef]:
        """The ``DomainRef`` a domain handle was read through by ``get``, when
        the current component read it; else None."""
        if not isinstance(v, Sym):
            return None
        e = self._refs.get(id(v.expr))
        if e is None or e[0] is not self.rt.current:
            return None
        return e[1]

    def relate(self, a_sv, b_sv) -> int:
        rt = self.rt
        a, b = rt.fw(unwrap(a_sv)), rt.fw(unwrap(b_sv))
        if not isinstance(a, FwDomain) or not isinstance(b, FwDomain):
            rt.error("FW-E-DOM-NONE", rt._where(), "relate of a null domain")
            return REL_SV[DomainRel.UNRELATED]
        return REL_SV[relate(a, b)]

    # -- drivers and pins -------------------------------------------------
    def drive(self, owner, sub, this, sig_sv, attr) -> None:
        rt = self.rt
        d = rt.fw(this)
        if not isinstance(d, FwDomain) or d.detached:
            return
        c = rt.current
        if rt.phase != "connect" or c is None:
            rt.error("FW-E-DOM-PHASE", d.path, "drive outside the connect phase")
            return
        s = rt._used(unwrap(sig_sv), "a domain driver")
        if s is None:
            return
        if not self.physical(d.kname):
            rt.error("FW-E-DOM-PIN", s.path, f"drives a domain of marker kind {d.kname}")
            return
        if s.width > 1:
            rt.error("FW-E-DOM-PIN", s.path, "a domain driver wider than 1 bit")
            return
        if not (s.scope is c or (isinstance(s, FwSignal) and s.is_port
                                 and s.scope.parent is c)):
            rt.error("FW-E-CONN-SCOPE", s.path, f"not in the scope of {c.path}")
            return
        drv = Driver(d, strip(attr), list(sub.arguments)[1].type, scope=c, sig=s, ctx=rt.ctx())
        d.drivers.append(drv)
        d.realized = True
        c.drivers.append(drv)

    def pin(self, owner, sub, m, name, dom_sv, attr, out: bool) -> None:
        rt = self.rt
        kname = self._kind(ty.type_param(owner, "K"))
        rec = rt.map_rec(unwrap(m))
        x = rt.current
        name = vx.to_str(name)
        if x is None:
            return
        if not self.physical(kname):
            rt.error("FW-E-DOM-PIN", x.path, f"pin {name!r} of marker kind {kname}")
            return
        ref = self.ref_of(dom_sv)
        dom_sv = unwrap(dom_sv)
        if dom_sv is None and not out:
            d = get(x, kname, "", mark=True)
        else:
            d = rt.fw(dom_sv)
            d = d if isinstance(d, FwDomain) else None
        if d is not None and d.detached:
            return
        if d is None:
            rt.error("FW-E-DOM-NONE", x.path, f"pin {name!r} has no domain")
        attr = strip(attr)
        dp = DomPin(name, kname, d, out, attr, list(sub.arguments)[3].type,
                    explicit=dom_sv is not None, ref=ref)
        rec.dpins.append(dp)
        if d is None:
            return
        d.realized = True
        if out:
            d.drivers.append(Driver(d, attr, dp.attr_type, extern=x, pin=name))
        elif kname == RESET:
            want = vx.to_int(vx.field(attr, "style"))
            if want != RESET_STYLE_ANY and want != vx.to_int(vx.field(d.params, "style")):
                rt.warning("FW-W-DOM-STYLE", x.path, f"pin {name!r} expects another reset style")

    # -- statements a component makes about itself --------------------------
    def _own(self, comp_sv, what: str) -> Optional[FwComponent]:
        rt = self.rt
        o = rt._used(unwrap(comp_sv), "a component")
        if o is None:
            return None
        if rt.phase != "build":
            rt.error("FW-E-DOM-PHASE", o.path, f"{what} outside the build phase")
            return None
        if o is not rt.current:
            rt.error("FW-E-DOM-OBJ", o.path, f"{what} on a component other than the caller")
            return None
        return o

    def _source_refs(self, p) -> List[Optional[ir.DomainRef]]:
        """The refs of a params value's sources (``from``), in order."""
        p = unwrap(p)
        try:
            return [self.ref_of(x) for x in vx.elements(unwrap(vx.field(p, "from")))]
        except Exception:
            return []

    def port_name(self, owner, comp_sv, assoc, port) -> None:
        kname = self._kind(ty.type_param(owner, "K"))
        o = self._own(comp_sv, "port_name")
        if o is not None:
            o.pnames[(kname, vx.to_str(assoc))] = vx.to_str(port)

    def provides(self, owner, sub, comp_sv, assoc, p) -> None:
        kname = self._kind(ty.type_param(owner, "K"))
        o = self._own(comp_sv, "provides")
        if o is not None:
            o.provs[(kname, vx.to_str(assoc))] = (strip(p), list(sub.arguments)[2].type,
                                                  self._source_refs(p))

    def waive(self, owner, a_sv, b_sv, reason) -> None:
        rt = self.rt
        kname = self._kind(ty.type_param(owner, "K"))
        a = rt._used(unwrap(a_sv), "a waiver's interface")
        b = rt._used(unwrap(b_sv), "a waiver's interface")
        if a is None or b is None:
            return
        if rt.phase != "connect" or rt.current is None:
            rt.error("FW-E-DOM-PHASE", a.path, "waive outside the connect phase")
            return
        reason = vx.to_str(reason)
        if reason == "":
            rt.error("FW-E-DOM-WAIVE", a.path, "a waiver needs a reason")
            return
        w = Waiver(rt.current, kname, a, b, reason, seq=len(self.waivers))
        self.waivers.append(w)
        rt.current.waivers.append(w)

    def params(self, this) -> Any:
        d = self.rt.fw(this)
        return vx.copy(d.params) if isinstance(d, FwDomain) else None

    def kind_of(self, this) -> Any:
        d = self.rt.fw(this)
        if not isinstance(d, FwDomain):
            return None
        return self._static(d.kclass, "get_type")


def value_json(rt, v, t):
    """A domain's params or a pin attribute value as the netlist writes it:
    enums as numbers, domain handles as paths."""
    from .netlist import params_value
    return params_value(v, t, handle=lambda o: _handle_path(rt, o))


def _handle_path(rt, o):
    d = rt.fw(o)
    return d.path if isinstance(d, FwDomain) else None


def _walk(o: FwObject, out: List[FwObject]) -> None:
    out.append(o)
    for c in o.children.values():
        _walk(c, out)


def check(rt) -> None:
    """The checks at the end of elaboration, after the extern maps."""
    if rt.top is None:
        return
    objs: List[FwObject] = []
    _walk(rt.top, objs)
    for o in objs:
        if isinstance(o, FwDomain):
            if len(o.drivers) > 1:
                rt.error("FW-E-DOM-DRIVER", o.path, "more than one driver")
            continue
        if not isinstance(o, FwComponent):
            continue
        for (kname, name) in o.assoc:
            if name and (kname, name) not in o.aread:
                rt.error("FW-E-DOM-UNUSED", o.path, f"named association {name!r} is never read")
        for (kname, name), (p, t, _refs) in o.provs.items():
            d = get(o, kname, name)
            if d is not None and value_json(rt, d.params, d.params_type) != \
                    value_json(rt, p, t):
                rt.error("FW-E-DOM-DERIV", o.path, f"{name!r} resolves to {d.path}, whose "
                         f"derivation is not the one stated")
        rd, cd = get(o, RESET), get(o, CLOCK)
        if rd is not None and cd is not None and rd.clock is not None \
                and relate(rd.clock, cd) is DomainRel.UNRELATED \
                and (o.parent is None or get(o.parent, RESET) is not rd
                     or get(o.parent, CLOCK) is not cd):
            rt.warning("FW-W-DOM-RSTSYNC", o.path, f"reset {rd.path} is synchronous to "
                       f"{rd.clock.path}, unrelated to clock {cd.path}")



def check_binds(rt) -> None:
    """The crossing check of every api and cfg binding (rt-sv's
    ``__fw_rt_proto::check_cross``): the endpoints' domains are their own
    associations, else their component's. Not a Zuspec pass: the IR's nets
    carry no bindings. Matched waivers are marked for :func:`passes`."""
    crossed = set()
    for bd in rt.binds:
        if bd.kind not in ("api", "cfg"):
            continue
        ia, ib = iface(bd.a), iface(bd.b)
        if ia is ib:
            continue
        lo, hi = (ia, ib) if ia.path < ib.path else (ib, ia)
        for kn in sorted(rt.dom.kinds):
            da, db = get(bd.a, kn), get(bd.b, kn)
            pk = (kn, lo.path, hi.path)
            if da is None or db is None or da is db or pk in crossed:
                continue
            rel = relate(da, db)
            sev = rt.dom.severity_opt.get(kn, {}).get(rel.name.lower()) \
                or rt.dom.severity(kn, rel)
            if sev == "none":
                continue
            crossed.add(pk)
            hit = [w for w in rt.dom.waivers if w.scope is bd.scope and w.kname == kn
                   and {w.a.path, w.b.path} == {lo.path, hi.path}]
            for w in hit:
                w.matched = True
            if hit:
                continue
            rt.diags.append(Diag(f"FW-{sev[0].upper()}-DOM-CROSS", lo.path,
                                 f"{kn} crossing from {da.path} to {db.path} ({hi.path})",
                                 sev))


def passes(rt) -> None:
    """The Zuspec domain passes (relation, resolution, crossing,
    realization) over the design's concrete IR; their diagnostics become
    elaboration diagnostics. The realization is kept on ``rt.dom`` for
    emission. ``rt.dom.severity_opt`` overrides kinds' crossing severities."""
    from zuspec.synth.passes.domains import Resolution, check_crossings, realize
    from .ir_map import to_ir
    if rt.top is None or not rt.dom.kinds:
        return
    try:
        ctxt, top, _ = to_ir(rt, parameterize=False)
        res = Resolution(ctxt, top, rt.top.path)
        found = check_crossings(res, rt.dom.severity_opt)
        r = realize(res)
    except Exception as e:      # an earlier error can leave the design unmappable
        if not rt.errors:
            rt.error("FW-E-ELAB", rt.top.path, f"domain passes: {e}")
        return
    rt.dom.realization = r
    bound = {w.a.path for w in rt.dom.waivers if w.matched}     # matched by check_binds
    for d in found + r.diags:
        if d.id == "FW-W-DOM-WAIVE" and d.path in bound:
            continue
        rt.diags.append(Diag(d.id, d.path, d.msg, d.severity))
