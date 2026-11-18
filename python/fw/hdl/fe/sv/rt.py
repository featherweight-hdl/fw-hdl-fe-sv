"""FeRt: the fw-hdl API's methods as interpreter intrinsics, and the
elaboration phases (core-layer1.md §3.2, §6.2).

fe-sv runs the user's classes, and the API's declarations from the spec, in
zuspec-fe-sv's SV interpreter. When the interpreted code calls a method of
the API, :class:`FeIntrinsics` runs the Python method here in its place.
User code (constructors, ``build()``, ``connect()``, ``extern_map()``,
``mk_params``, ``init_params``, param edits) runs as SV.

Phases: create the top, build top-down (a component before its children,
children in creation order), connect top-down, then resolve
(:mod:`.resolve`).
"""
from __future__ import annotations

import fnmatch
import re
from typing import Any, Dict, List, Optional

import zuspec.ir.core as ir
from zuspec.fe.sv.interp import InterpError, Intrinsics, NOT_HANDLED
from zuspec.fe.sv.interp.trace import TAINTED, SynTracer, TType
from zuspec.fe.sv.interp.values import Struct, Sym, const_to_py, norm, strip, unwrap

from . import types as ty
from . import values as vx
from .params import ParamArg
from .objects import (BUNDLE, CFG_CONS, CFG_PUB, COMPONENT, MIRROR, MONITOR, Assoc, Binding, Conn,
                      Diag, ExternRec, FwBundle, FwCfg, FwComponent, FwEndpoint, FwObject, FwSignal,
                      Listen, PinRule, Tie)

_SIGNAL_DIR = {"fw_signal": None, "fw_input": "in", "fw_output": "out", "fw_inout": "inout"}
_PROTO_API = {"fw_protocol_type_of", "fw_api_type_of", "fw_protocol_decl", "fw_protocol_family",
              "fw_api_type", "fw_protocol_type", "fw_protocol"}
_ROLE = {"fw_bundle": BUNDLE, "fw_mirror": MIRROR, "fw_monitor": MONITOR}
# API endpoint classes: (port side, socket).
_ENDPOINT = {"fw_socket": (True, True), "fw_port": (True, False), "fw_export": (False, False)}
# Configured endpoints: (base endpoint class, "src" or "sink").
_CFG_EP = {"fw_port_cfg_src": ("fw_port", "src"), "fw_port_cfg_sink": ("fw_port", "sink"),
           "fw_export_cfg_src": ("fw_export", "src"), "fw_export_cfg_sink": ("fw_export", "sink"),
           "fw_socket_cfg_src": ("fw_socket", "src"), "fw_socket_cfg_sink": ("fw_socket", "sink")}
_CFG = {"fw_cfg_src": CFG_PUB, "fw_cfg_sink": CFG_CONS}


class ElabError(Exception):
    """Elaboration stopped: an error that leaves nothing sensible to do."""


class _Skip(Exception):
    """An API call that reported its error and has nothing to return."""


class Override:
    def __init__(self, seq, scope, glob, ptype, action, value):
        self.seq, self.scope, self.glob = seq, scope, glob
        self.ptype, self.action, self.value = ptype, action, value
        self.used = False


def _glob_match(glob: str, path: str) -> bool:
    """Match a path against an override glob (params.md): segments split on
    ``.``; ``*`` matches any characters within one segment; a ``**`` segment
    matches zero or more whole segments."""
    gs, ps = glob.split("."), path.split(".")

    def m(i, j):
        if i == len(gs):
            return j == len(ps)
        if gs[i] == "**":
            return any(m(i + 1, k) for k in range(j, len(ps) + 1))
        return j < len(ps) and fnmatch.fnmatchcase(ps[j], gs[i].replace("[", "[[]")) and m(i + 1, j + 1)
    return m(0, 0)


class FeRt:
    """One elaboration."""

    def __init__(self, interp, compilation):
        self.interp = interp
        self.comp = compilation
        self.pkg = compilation.getPackage(ty.API_PACKAGE)
        self.phase = "init"          # init | create | build | connect | resolve | done
        self.current: Optional[FwComponent] = None
        self.top: Optional[FwComponent] = None
        self.components: List[FwComponent] = []
        self.conns: List[Conn] = []
        self.ties: List[Tie] = []
        self.binds: List[Binding] = []
        # Configuration (rt-sv's __fw_rt_cfg): connect_up inner -> outer, and
        # connect sink side -> source side, over plain and configured ones.
        self.cfg_up: Dict[int, FwObject] = {}
        self.cfg_src: Dict[int, FwObject] = {}
        self.listens: List[Listen] = []       # add_listener, in call order
        self.overrides: List[Override] = []
        self.diags: List[Diag] = []
        self._by_sv: Dict[int, FwObject] = {}
        self._ctors: Dict[str, Any] = {}      # fw_ctor_of specialization -> its object
        self._rules: Dict[int, PinRule] = {}
        self._maps: Dict[int, ExternRec] = {}
        self._seq = 0
        self.extern_methods = set()  # (class, method) of the API's extern methods: intrinsics
        self.param_args: Dict[int, Any] = {}   # fw_value/fw_type objects -> ParamArg
        self.headers = None          # elab.Headers: module headers for the extern checks
        self.nets: Dict[int, list] = {}
        # Tracing (§6.6). The objects whose class parameters are traced, the
        # innermost last: the component whose code runs, and a bundle while
        # its build() runs.
        self._tstack: List[Any] = []
        # Arms to force at the top's traced ifs (explore()), by condition
        # (ir_map.cond_key); on while the top's own code runs.
        self.force: Dict[str, bool] = {}
        self._force_on = False
        from .domains import Domains
        self.dom = Domains(self)
        from .protocols import Protocols
        self.proto = Protocols(self)

    # -- diagnostics ----------------------------------------------------
    def error(self, id: str, path: str, msg: str) -> None:
        self.diags.append(Diag(id, path, msg))

    def warning(self, id: str, path: str, msg: str) -> None:
        self.diags.append(Diag(id, path, msg, "warning"))

    @property
    def errors(self) -> List[Diag]:
        return [d for d in self.diags if d.severity == "error"]

    # -- object map -----------------------------------------------------
    def fw(self, sv) -> Optional[FwObject]:
        sv = unwrap(sv)
        if sv is None:
            return None
        return self._by_sv.get(id(sv))

    def _register(self, o: FwObject) -> bool:
        """Puts ``o`` in the tree; False (with a diagnostic) if it cannot be:
        created late, a bad name, or a duplicate name."""
        self._by_sv[id(o.sv)] = o
        self._seq += 1
        o.seq = self._seq
        if self.phase not in ("create", "build"):
            self.error("FW-E-ELAB-LATE", o.path, "object created after the build phase")
            return False
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*(\[\d+\])*", o.name):
            self.error("FW-E-NAME-BAD", o.path, f"{o.name!r} is not a legal name")
            return False
        p = o.parent
        if p is not None:
            if o.name in p.children:
                self.error("FW-E-NAME-DUP", o.path, f"duplicate name {o.name!r} under {p.path}")
                return False
            p.children[o.name] = o
        return True

    def ctx(self) -> tuple:
        """The traced control frames active now (FwObject.ctx)."""
        if self.interp is None:
            return ()
        return tuple((f, f.iteration, f.value, f.taken) for f in self.interp.ctl)

    @property
    def tscope(self):
        """The object (component or bundle) whose class parameters the code
        running now is traced over."""
        if self._tstack:
            return self._tstack[-1]
        return self.current

    def _where(self) -> str:
        return self.current.path if self.current else ""

    def _used(self, sv, what: str) -> Optional[FwObject]:
        """The fw object behind a handle the API was given. A structural
        object made with ``new`` is FW-E-ELAB-NEW on first use."""
        if sv is None:
            return None
        o = self.fw(sv)
        if o is None:
            self.error("FW-E-ELAB-NEW", self._where(),
                       f"{ty.type_name(sv.cls)} constructed with new(), used as {what}")
            raise _Skip()
        return o

    def _parent(self, sv_parent, what: str) -> Optional[FwObject]:
        return self._used(sv_parent, "a parent")

    # -- elaboration ----------------------------------------------------
    def elaborate(self, top_cls, name: str = "top", params=None,
                  path_alias: Optional[str] = None, seed=None) -> Optional[FwComponent]:
        """Create ``top_cls`` as the top and run every phase. ``params``, if
        given, is the top's params value (else ``mk_params()``); ``seed(top)``
        runs after the top is created, before its build."""
        create = self.interp.find_method(top_cls, "create")
        if create is None:
            raise ElabError(f"{ty.type_name(top_cls)} has no create(): "
                            f"is it missing `fw_component_utils?")
        self.phase = "create"
        self._alias = path_alias
        args = [name, None] if params is None else [name, None, params]
        top_sv = self.interp.call(create, None, args)
        self.top = self.fw(top_sv)
        if seed is not None and self.top is not None:
            seed(self.top)
        self.phase = "build"
        self._walk(self.top, "build")
        for o in self.overrides:
            if not o.used:
                self.warning("FW-W-PARAM-UNUSED", o.scope.path if o.scope else "",
                             f"override {o.glob!r} matched nothing")
        self.phase = "connect"
        self._walk(self.top, "connect")
        self.phase = "resolve"
        self.current = None
        for c in self.components:
            if ty.implements(c.cls, "fw_extern_if"):
                self._run_extern_map(c)
        from .resolve import resolve
        resolve(self)
        self.phase = "done"
        return self.top

    def _run(self, c: FwComponent, fn) -> None:
        """Run user code of ``c`` (its constructor or a phase method) with
        ``c`` as the current component; then check that it left its params
        alone (FW-E-PARAM-WRITE)."""
        prev, self.current = self.current, c
        prev_force = self._force_on
        # Forced arms (explore()) apply to the top's own code only.
        self._force_on = c.parent is None
        try:
            fn()
        except InterpError as e:
            if "null object access" in str(e) and self._null_ctor(c):
                self.error("FW-E-PARAM-NULL", c.path, "a null constructor factory was used")
            else:
                raise
        finally:
            self.current = prev
            self._force_on = prev_force
        if c.params_type is not None and not vx.equal(c.sv.get("params"), c.params):
            self.error("FW-E-PARAM-WRITE", c.path, "params is read-only")
            vx.set_field(c.sv, "params", vx.copy(c.params_sym))

    def _null_ctor(self, c: FwComponent) -> bool:
        """``c``'s params hold a constructor factory that is null."""
        if c.params_type is None:
            return False
        for f in ty.canon(c.params_type):
            t = ty.canon(getattr(f, "type", None))
            if t is not None and getattr(t, "isClass", False) and \
                    ty.implements(t, "fw_ctor") or (t is not None and getattr(t, "name", "") == "fw_ctor"):
                if vx.field(c.params, f.name) is None:
                    return True
        return False

    def _walk(self, c: FwComponent, method: str) -> None:
        self._run(c, lambda: self.interp.call_method(c.sv, method))
        for ch in list(c.children.values()):
            if isinstance(ch, FwComponent):
                self._walk(ch, method)

    # -- creation -------------------------------------------------------
    def create_component(self, cls, name, sv_parent, p) -> Any:
        name_sym = name if isinstance(name, Sym) else None
        name = vx.to_str(name)
        parent = self._parent(unwrap(sv_parent), name)
        targs = self._created_as(cls)
        sv = self.interp.allocate(cls)
        c = FwComponent(name=name, parent=parent, sv=sv, cls=ty.canon(cls),
                        type_name=ty.type_name(cls), ctx=self.ctx(), name_sym=name_sym,
                        targs=targs)
        if parent is None:
            c.path_alias = getattr(self, "_alias", None)
        if not self._register(c):
            return sv
        self.components.append(c)
        c.is_extern = ty.implements(cls, "fw_extern_if")
        self.proto.component_created(c)
        self._class_leaves(c, c.cls)
        self._resolve_params(c, p)
        self._run(c, lambda: self.interp.run_ctor(sv, cls))
        return sv

    def create_signal(self, cls, api: str, name, sv_parent) -> Any:
        name_sym = name if isinstance(name, Sym) else None
        name = vx.to_str(name)
        parent = self._parent(unwrap(sv_parent), name)
        if parent is None:
            raise ElabError(f"signal {name!r} has no parent")
        t = ty.type_param(cls, "T", raw=True)
        ct = self._call_class(cls)
        ttype = None
        if ct is not None:
            ttype = TType(t, tainted=True) if ct.tainted else ct.args.get("T", TType(t))
        sv = self.interp.allocate(cls)
        s = FwSignal(name=name, parent=parent, sv=sv, cls=ty.canon(cls), ttype=ttype,
                     type_name=ty.type_name(cls), kind="port" if _SIGNAL_DIR[api] else "signal",
                     dir=_SIGNAL_DIR[api], dtype=t,
                     width=int(t.bitWidth) if t is not None else 1,
                     signed=bool(t.isSigned) if t is not None else False,
                     ctx=self.ctx(), name_sym=name_sym)
        if isinstance(parent, FwBundle) and s.dir is None:
            self.error("FW-E-CONN-TYPE", s.path, "a bundle member must be a port")
        self._register(s)
        self.interp.run_ctor(sv, cls)
        return sv

    def create_bundle(self, cls, api: str, name, sv_parent) -> Any:
        name_sym = name if isinstance(name, Sym) else None
        name = vx.to_str(name)
        parent = self._parent(unwrap(sv_parent), name)
        if parent is None:
            raise ElabError(f"bundle {name!r} has no parent")
        b = ty.type_param(cls, "B")
        ct = self._call_class(cls)
        targs = None
        if ct is not None:
            targs = TType(b, tainted=True) if ct.tainted else ct.args.get("B", TType(b, args={}))
        sv = self.interp.allocate(cls)
        o = FwBundle(name=name, parent=parent, sv=sv, cls=ty.canon(cls),
                     type_name=ty.type_name(b), kind=_ROLE[api], btype=b,
                     btype_name=ty.type_name(b), ctx=self.ctx(), name_sym=name_sym,
                     targs=targs)
        self._register(o)
        self.proto.bundle_created(o)
        self._class_leaves(o, b)
        self._tstack.append(o)
        try:
            self.interp.run_ctor(sv, cls)
            self.interp.call_method(sv, "build")
        finally:
            self._tstack.pop()
        return sv

    # -- API endpoints and sockets (protocols.md) ------------------------
    def create_endpoint(self, cls, api: str, name, sv_parent) -> Any:
        name = vx.to_str(name)
        parent = self._parent(unwrap(sv_parent), name)
        if parent is None:
            raise ElabError(f"endpoint {name!r} has no parent")
        base, role = _CFG_EP.get(api, (api, ""))
        out, sock = _ENDPOINT[base]
        sv = self.interp.allocate(cls)
        e = FwEndpoint(name=name, parent=parent, sv=sv, cls=ty.canon(cls),
                       type_name=ty.type_name(cls), ctx=self.ctx(), out=out, sock=sock,
                       must=out, api=base, cfg=role,
                       tkey=ty.type_name(ty.type_param(cls, "CFG")) if role else "")
        self._register(e)
        return sv

    def set_imp(self, e_sv, imp) -> None:
        """``set_imp(imp)``: recorded on the endpoint, for the IR's
        ``ApiEndpoint.imp``."""
        e = self._used(unwrap(e_sv), "an endpoint")
        if e is not None:
            e.imp = unwrap(imp)

    def link(self, a_sv, b_sv, up: bool) -> None:
        """``connect`` (up False) or ``connect_up``: binds ``a`` to ``b``;
        a configured endpoint's also records the cfg binding."""
        a, b = self._link(a_sv, b_sv, up)
        if a is not None and a.cfg:
            self.bind_cfg(a, b, up, self.binds[-1].scope)

    def _link(self, a_sv, b_sv, up: bool):
        a = self._used(unwrap(a_sv), "an endpoint")
        b = self._used(unwrap(b_sv), "an endpoint")
        if a is None or b is None:
            raise ElabError("connect: an endpoint is null")
        scope = self._conn_scope("connect_up" if up else "connect", a.path)
        if scope is None:
            return None, None
        from .resolve import _in_scope
        for o in (a, b):
            if not _in_scope(o, scope):
                self.error("FW-E-CONN-SCOPE", o.path, f"not in the scope of {scope.path}")
                return None, None
        for x in ((a, b) if a.sock and not up else (a,)):
            if x.up is not None or x.peer is not None:
                self.error("FW-E-SOCK-MULTI", x.path,
                           f"already bound to {(x.up or x.peer).path}")
                return None, None
        if a.sock and not up:
            a.peer, b.peer = b, a           # socket to peer socket: both ways
        else:
            a.up = b
        self.binds.append(Binding("api", a, b, scope, up))
        return a, b

    def check_api(self) -> None:
        """FW-E-SOCK-UNBOUND: a port that reaches no export; a socket the
        outermost socket of whose connect_up chain has no peer."""
        for e in sorted((o for o in self._by_sv.values() if isinstance(o, FwEndpoint)),
                        key=lambda o: o.seq):
            if not e.must:
                continue
            x, n = e, 0
            if e.sock:
                while x.peer is None and x.up is not None and n < len(self._by_sv):
                    x, n = x.up, n + 1
                if x.peer is not None:
                    continue
            else:
                while x is not None and x.out and n < len(self._by_sv):
                    x, n = x.up, n + 1
                if x is not None and not x.out:
                    continue
            self.error("FW-E-SOCK-UNBOUND", e.path, "resolves to no export")

    # -- configuration (protocols.md, "Configuration") --------------------
    def create_cfg(self, cls, api: str, name, sv_parent) -> Any:
        name = vx.to_str(name)
        parent = self._parent(unwrap(sv_parent), name)
        if parent is None:
            raise ElabError(f"configuration {name!r} has no parent")
        sv = self.interp.allocate(cls)
        o = FwCfg(name=name, parent=parent, sv=sv, cls=ty.canon(cls), kind=_CFG[api],
                  type_name=ty.type_name(cls), ctx=self.ctx(),
                  tkey=ty.type_name(ty.type_param(cls, "T")))
        self._register(o)
        return sv

    def cfg_set(self, p_sv, v) -> None:
        p = self._used(unwrap(p_sv), "a cfg source")
        p.value = strip(v)

    def cfg_get(self, p_sv) -> Any:
        p = self._used(unwrap(p_sv), "a cfg source")
        if p.value is None:
            raise ElabError(f"{p.path}: get() before set()")
        return p.value

    def add_listener(self, s_sv, l_sv) -> None:
        """``add_listener(l)`` on a sink: recorded for the IR's
        ``cfg_listeners`` (Q2: static elaboration connects a change to its
        action; it neither pushes values nor calls ``changed``)."""
        s = self._used(unwrap(s_sv), "a cfg sink")
        l = unwrap(l_sv)
        if s is None or l is None:
            raise ElabError("add_listener: the sink or the listener is null")
        if self.current is not None:
            self.listens.append(Listen(self.current, s, l))

    @staticmethod
    def _is_src(o) -> bool:
        return o.kind == CFG_PUB if isinstance(o, FwCfg) else o.cfg == "src"

    def cfg_link(self, a_sv, b_sv, up: bool) -> None:
        """A plain sink's or source's ``a.connect(b)`` (up False) or
        ``a.connect_up(b)``."""
        a = self._used(unwrap(a_sv), "a cfg endpoint")
        b = self._used(unwrap(b_sv), "a cfg endpoint")
        if a is None or b is None:
            raise ElabError("connect: a configuration endpoint is null")
        scope = self._conn_scope("connect_up" if up else "connect", a.path)
        if scope is None:
            return
        from .resolve import _in_scope
        for o in (a, b):
            if not _in_scope(o, scope):
                self.error("FW-E-CONN-SCOPE", o.path, f"not in the scope of {scope.path}")
                return
        self.bind_cfg(a, b, up, scope)

    def bind_cfg(self, a, b, up: bool, scope) -> None:
        """Records a checked cfg connection and its "cfg" binding, source to
        sink (rt-sv's ``__fw_rt_cfg::bind_cfg``)."""
        if up:
            self.cfg_up[id(a)] = b
            p, s = (a, b) if self._is_src(a) else (b, a)
        else:
            p, s = (a, b) if self._is_src(a) else (b, a)
            if id(s) in self.cfg_src:
                self.error("FW-E-SOCK-MULTI", s.path,
                           f"already configured by {self.cfg_src[id(s)].path}")
                return
            self.cfg_src[id(s)] = p
        self.binds.append(Binding("cfg", p, s, scope, up))

    def check_cfg(self) -> None:
        """Binds each sink to its source (rt-sv's ``__fw_rt_cfg::resolve``):
        out along its connect_up chain to a connect, then in along the
        source's connect_up chain to the innermost source. FW-E-CFG-UNBOUND
        on an innermost sink no source reaches. Then the static
        FW-E-CFG-WRITE check."""
        objs = sorted(self._by_sv.values(), key=lambda o: o.seq)
        sinks = [o for o in objs if (isinstance(o, FwCfg) and o.kind == CFG_CONS)
                 or (isinstance(o, FwEndpoint) and o.cfg == "sink")]
        outer = {id(self.cfg_up[id(c)]) for c in sinks if id(c) in self.cfg_up}
        inner: Dict[int, FwObject] = {}       # source connect_up: outer -> inner
        for o in objs:
            if self._is_cfg(o) and self._is_src(o) and id(o) in self.cfg_up:
                inner.setdefault(id(self.cfg_up[id(o)]), o)
        n = len(objs)
        for c in sinks:
            x, k = c, 0
            while id(x) not in self.cfg_src and id(x) in self.cfg_up and k < n:
                x, k = self.cfg_up[id(x)], k + 1
            if id(x) not in self.cfg_src:
                if id(c) not in outer:
                    self.error("FW-E-CFG-UNBOUND", c.path, "no source reaches this sink")
                continue
            s = self.cfg_src[id(x)]
            while id(s) in inner and k < n:
                s, k = inner[id(s)], k + 1
            c.src = s
        from .cfgcheck import check_writes
        check_writes(self)

    @staticmethod
    def _is_cfg(o) -> bool:
        return isinstance(o, FwCfg) or (isinstance(o, FwEndpoint) and bool(o.cfg))

    # -- params (§3.9) ---------------------------------------------------
    def _resolve_params(self, c: FwComponent, p) -> None:
        decl = next((k for k in ty.class_chain(c.cls) if ty.declares(k, "params")), None)
        if decl is None:
            return
        mk = self.interp.find_method(decl, "mk_params")
        dflt = self.interp.call(mk, None, []) if mk is not None else None
        c.params_type = ty.canon(decl.find("params").type)
        value = vx.copy(p)
        src = {f: "creator" for f in vx.fields(value)
               if dflt is None or not vx.equal(vx.field(value, f), vx.field(dflt, f))}
        # Ancestors' overrides, nearest first, then global ones (scope None);
        # each later one sees the result of the ones before.
        def rank(o):
            if o.scope is None:
                return (1, 0, o.seq)
            return (0, -len(o.scope.path.split(".")), o.seq)
        for o in sorted(self.overrides, key=rank):
            if not self._override_matches(o, c):
                continue
            o.used = True
            before = vx.copy(value)
            if o.action == "set":
                value = vx.copy(o.value)
            else:
                value = vx.copy(self.interp.call_method(o.value, "apply", [value]))
            tag = "global" if o.scope is None else "override"
            for f in vx.fields(value):
                if not vx.equal(vx.field(value, f), vx.field(before, f)):
                    src[f] = tag
        if ty.declares(decl, "__fw_init_params"):
            init = self.interp.find_method(decl, "__fw_init_params")
            before = vx.copy(value)
            ref = vx.Ref(value)
            self.interp.call(init, None, [ref])
            value = ref.get()
            for f in vx.fields(value):
                if not vx.equal(vx.field(value, f), vx.field(before, f)):
                    src[f] = "init"
        c.params = strip(value)
        c.param_src = src
        c.params_dflt = strip(dflt) if dflt is not None else None
        self._trace_params(c, value)
        vx.set_field(c.sv, "params", vx.copy(c.params_sym))

    def _trace_params(self, c: FwComponent, value) -> None:
        """The module parameters of ``c`` (§6.6): every integral field of its
        params struct, nested struct fields flattened with ``_``. ``c``'s
        code sees each as a Sym over an ``ExprRefParam`` leaf of its own;
        ``c.param_exprs`` keeps the value the creator computed for each, as an
        expression over the creator's parameters (None where an override set
        it, or it was not traced)."""
        c.mparams = _mparams(c.params_type)
        sym = vx.copy(c.params)
        for flat, path, _t in c.mparams:
            leaf = ir.ExprRefParam(name=flat)
            c.leaves[id(leaf)] = flat
            _set_path(sym, path, Sym(_get_path(c.params, path), leaf))
            src = c.param_src.get(path[0])
            raw = _get_path(value, path)
            if src in ("override", "global"):
                c.param_exprs[flat] = None
            elif isinstance(raw, Sym):
                c.param_exprs[flat] = raw.expr
            else:
                c.param_exprs[flat] = ir.ExprConstant(value=int(raw))
        c.params_sym = sym

    def type_leaf(self, t) -> Any:
        """The leaf to trace type ``t`` (as written) by in the code running
        now: its type parameter, if ``t`` is one (or follows from one)."""
        o = self.tscope
        if o is None:
            return None
        name = ty.type_param_name(t, _traced_cls(o))
        if name is None:
            return None
        return self._type_leaf(o, name)

    @staticmethod
    def _type_leaf(o, name: str):
        leaf = o.tr.type_leaves.get(name)
        if leaf is None:
            leaf = o.tr.type_leaves[name] = ir.ExprRefParam(name=name)
            o.tr.leaves[id(leaf)] = name
        return leaf

    def symbolic_param(self, sym) -> Any:
        """Intrinsics.symbolic_param: a class parameter of the object whose
        code runs is traced over its leaf; one of a base class, by what the
        ``extends`` clause passes it (tainted when it cannot tell); any other
        is a constant."""
        o = self.tscope
        if o is None:
            return None
        chain = list(ty.class_chain(_traced_cls(o)))
        for i, k in enumerate(chain):
            m = k.find(sym.name)
            if m is None or not (m is sym or m == sym):
                continue
            is_type = type(sym).__name__ == "TypeParameterSymbol"
            if i == 0:
                if is_type:
                    return self._type_leaf(o, sym.name)
                return o.tr.cvals.get(sym.name)
            args = o.tr.base_args.get(ty.type_name(k))
            if args is None:
                return TAINTED if is_type else Sym(const_to_py(sym.value, sym.type), None)
            v = args.get(sym.name)
            if v is None:
                # Left to its default: constant unless the base has traced
                # arguments its default may be computed from.
                traced = any(isinstance(x, Sym) or (isinstance(x, TType) and x.traced)
                             for x in args.values())
                if not traced:
                    return None
                return TAINTED if is_type else Sym(const_to_py(sym.value, sym.type), None)
            if is_type:
                if not isinstance(v, TType) or v.tainted:
                    return TAINTED
                if v.leaf is not None:
                    return v.leaf
                return TAINTED if v.traced else None
            return v if isinstance(v, Sym) else None
        return self._foreign_param(sym)

    def _foreign_param(self, sym) -> Any:
        """A parameter of another class, read in a static function of it: the
        value the call that named the class passed (``wfifo #(W)::mk_params()``,
        or ``wfifo #(W)::create(...)`` whose default arguments call
        ``mk_params()``), traced over the caller's leaves; None (its constant
        value) when no call on the stack names it so."""
        it = self.interp
        is_type = type(sym).__name__ == "TypeParameterSymbol"
        for idx in range(len(it.stack) - 1, -1, -1):
            fr = it.stack[idx]
            owner = it.owner_class(fr.sub) if fr.sub is not None else None
            if owner is None or fr.this is not None:
                return None
            m = ty.canon(owner).find(sym.name)
            if m is None or not (m is sym or m == sym):
                return None
            call = fr.call
            syn = getattr(call, "syntax", None) if call is not None else None
            left = getattr(syn, "left", None) if syn is not None else None
            if left is None or left.kind.name != "ScopedName":
                continue                  # called from within the class: look further out
            caller = it.stack[idx - 1].sub if idx > 0 else None
            if caller is None:
                return None
            try:
                t = SynTracer(it).call_class(call, caller, owner)
            except Exception:             # pragma: no cover - be safe
                t = TType(owner, tainted=True)
            if t is None:
                return None
            if t.tainted:
                return TAINTED if is_type else Sym(const_to_py(sym.value, sym.type), None)
            v = (t.args or {}).get(sym.name)
            if v is None:
                return None
            if is_type:
                if not isinstance(v, TType) or v.tainted:
                    return TAINTED
                return v.leaf if v.leaf is not None else (TAINTED if v.traced else None)
            return v if isinstance(v, Sym) else None
        return None

    def _class_leaves(self, o, cls) -> None:
        """The value class parameters of ``o`` (created as ``cls``), each a
        Sym over a leaf of its own (a shape: a struct leaf and a leaf per
        field, §6.6); and what its base classes are passed."""
        t = o.tr
        for m in ty.canon(cls):
            if type(m).__name__ != "ParameterSymbol" or m.isLocalParam or not m.isPortParam:
                continue
            mt = ty.canon(m.type)
            v = const_to_py(m.value, m.type)
            if getattr(mt, "isStruct", False):
                sleaf = ir.ExprRefParam(name=m.name)
                fl = {}
                for path, ft, fv in _struct_fields(mt, v, (m.name,)):
                    flat = "_".join(path)
                    leaf = ir.ExprRefParam(name=flat)
                    t.leaves[id(leaf)] = flat
                    fl[path[1:]] = leaf
                    t.cparams.append((flat, path, ft, fv))
                t.sleaves[id(sleaf)] = (m.name, fl)
                t.cvals[m.name] = Sym(v, sleaf)
            elif getattr(mt, "isIntegral", False):
                leaf = ir.ExprRefParam(name=m.name)
                t.leaves[id(leaf)] = m.name
                t.cparams.append((m.name, (m.name,), mt, v))
                t.cvals[m.name] = Sym(v, leaf)
        # Base classes: their parameters as the extends clauses write them,
        # outermost (the class itself) first, each traced in the scope of
        # the class that names it.
        self._tstack.append(o)
        try:
            tracer = SynTracer(self.interp)
            for d in list(ty.class_chain(cls))[:-1]:
                b = ty.canon(d.baseClass)
                syn = getattr(d, "syntax", None)
                ec = getattr(syn, "extendsClause", None) if syn is not None else None
                if b is None or ec is None or not getattr(b, "isClass", False):
                    continue
                try:
                    tt = tracer.type(ec.baseName, d, b)
                except Exception:
                    tt = None
                if tt is None or tt.tainted:
                    continue                  # no entry: tainted when read
                t.base_args[ty.type_name(b)] = tt.args or {}
        finally:
            self._tstack.pop()

    def _call_class(self, cls) -> Optional[TType]:
        """The class of the API call being handled, as the call writes it
        (``fw_input #(bit [W-1:0])::create``), traced; None if the call
        does not name its class."""
        it = self.interp
        e = it.call_expr
        if e is None or not it.stack:
            return None
        try:
            return SynTracer(it).call_class(e, it.stack[-1].sub, cls)
        except Exception:                      # pragma: no cover - be safe
            return TType(cls, tainted=True)

    def _created_as(self, cls) -> Optional[TType]:
        """For ``__fw_create`` (called by the ``create`` the component's
        macro declares), the class as the caller of ``create`` wrote it."""
        it = self.interp
        if len(it.stack) < 2 or it.stack[-1].call is None:
            return None
        try:
            return SynTracer(it).call_class(it.stack[-1].call, it.stack[-2].sub, cls)
        except Exception:                      # pragma: no cover
            return TType(cls, tainted=True)

    # -- exploring the arms of branches (§6.6) ----------------------------
    def ctl_force(self, frame) -> Optional[bool]:
        """The arm to take at a traced if of the top's own code: what
        ``force`` says for its condition (or for the condition it negates)."""
        if not self._force_on or not self.force or frame.cond is None:
            return None
        from .ir_map import cond_key
        k, neg = cond_key(frame.cond)
        if k not in self.force:
            return None
        return self.force[k] != neg

    def explore(self, c: FwComponent, force: Dict[str, bool]):
        """Elaborate ``c``'s class on its own, as a top, with ``c``'s params
        value, taking the arms in ``force`` at the traced ifs of its own code
        (:func:`ir_map.cond_key` of the condition -> its value). Returns ``(top, errors)``: the top stands for
        ``c`` (its path is ``c``'s); ``errors`` are the elaboration's
        error diagnostics (the run is unusable if there are any)."""
        alt = FeRt(None, self.comp)
        alt.extern_methods = self.extern_methods
        alt.headers = self.headers
        alt.compile_result = getattr(self, "compile_result", None)
        from zuspec.fe.sv.interp import Interpreter
        alt.interp = Interpreter(self.comp, FeIntrinsics(alt))
        alt.force = dict(force)
        alt.dom.kinds.update(self.dom.kinds)

        def seed(top):
            # The domains c sees: its named associations and each kind's
            # default, as the same domain objects, so that its code reads
            # what it read in the design. They count as read: an arm that
            # does not read one is not FW-E-DOM-UNUSED.
            from . import domains as dm
            for (kn, n), a in c.assoc.items():
                if n:
                    top.assoc[(kn, n)] = a
                    top.aread.add((kn, n))
            for kn in self.dom.kinds:
                d = dm.get(c, kn)
                if d is not None:
                    top.assoc[(kn, "")] = c.assoc.get((kn, "")) or Assoc(
                        c.parent or c, top, kn, "", d)
            for d in {id(a.dom): a.dom for a in top.assoc.values()}.values():
                alt._by_sv.setdefault(id(d.sv), d)
        try:
            alt.elaborate(c.cls, c.name, params=vx.copy(c.params) if c.params is not None
                          else None, path_alias=c.path, seed=seed)
        except (ElabError, InterpError) as e:
            alt.error("FW-E-ELAB", c.path, str(e))
        return alt.top, alt.errors

    def _override_matches(self, o: Override, c: FwComponent) -> bool:
        if c.params_type is None or not o.ptype.isMatching(c.params_type):
            return False
        if o.scope is None:
            return _glob_match(o.glob, c.path)
        pre = o.scope.path + "."
        if not c.path.startswith(pre):        # strict descendants only
            return False
        return _glob_match(o.glob, c.path[len(pre):])

    def add_override(self, ptype, action: str, sv_scope, glob: str, value) -> None:
        scope = self._used(sv_scope, "an override scope")
        if sv_scope is not None and not isinstance(scope, FwComponent):
            raise ElabError("fw_param_override: the scope is not a component")
        if sv_scope is None and self.phase not in ("init", "create"):
            # A global override from inside the design applies like one
            # registered before the top was created, only to what follows.
            pass
        o = Override(len(self.overrides), scope, glob, ty.canon(ptype), action,
                     vx.copy(value) if action == "set" else value)
        for c in self.components:
            if self._override_matches(o, c):
                o.used = True
                self.error("FW-E-PARAM-LATE", c.path,
                           f"override {glob!r} registered after {c.path} was created")
        self.overrides.append(o)

    # -- constructor factories -------------------------------------------
    def ctor_get(self, cls) -> Any:
        key = ty.type_name(cls)
        if key not in self._ctors:
            b, d = ty.type_param(cls, "B"), ty.type_param(cls, "D")
            if b is not None and d is not None and not (d.isDerivedFrom(b) or d.isMatching(b)):
                self.error("FW-E-PARAM-CTOR", self.current.path if self.current else "<global>",
                           f"{ty.type_name(d)} is not a subclass of {ty.type_name(b)}")
            self._ctors[key] = self.interp.allocate(cls)
        return self._ctors[key]

    def ctor_create(self, this, name, sv_parent, p) -> Any:
        d = ty.type_param(this.cls, "D")
        create = self.interp.find_method(d, "create")
        if create is None:
            raise ElabError(f"{ty.type_name(d)} has no create()")
        # SV run on behalf of the caller: its traced frames still apply.
        return self.interp.call(create, None, [name, sv_parent, p], keep_ctl=True)

    # -- connections -----------------------------------------------------
    def _conn_scope(self, what: str, path: str) -> Optional[FwComponent]:
        if self.phase != "connect" or self.current is None:
            self.error("FW-E-CONN-PHASE", path, f"{what} outside connect()")
            return None
        return self.current

    def connect(self, a_sv, b_sv, how: str, a_sym=None) -> None:
        if isinstance(a_sv, Sym):
            a_sym, a_sv = a_sv, a_sv.value
        b_sym = b_sv if isinstance(b_sv, Sym) else None
        b_sv = unwrap(b_sv)
        a, b = self._used(a_sv, "an endpoint"), self._used(b_sv, "an endpoint")
        if a is None or b is None:
            raise ElabError(f"{how}: an endpoint is null")
        scope = self._conn_scope(how, a.path)
        if scope is not None:
            conn = Conn(scope, a, b, how, ctx=self.ctx(), a_sym=a_sym, b_sym=b_sym)
            self.conns.append(conn)
            scope.conns.append(conn)

    def connect_all(self, pairs) -> None:
        for pair in vx.elements(pairs):
            a, b = vx.elements(pair)
            self.connect(a, b, "connect_all")

    def tie(self, sig_sv, value, sig_sym=None) -> None:
        s = self._used(unwrap(sig_sv), "a tie endpoint")
        scope = self._conn_scope("tie", s.path)
        if scope is not None:
            t = Tie(scope, s, vx.to_int(value), ctx=self.ctx(), sig_sym=sig_sym,
                    value_sym=value if isinstance(value, Sym) else None)
            self.ties.append(t)
            scope.ties.append(t)

    # -- extern maps (§3.7) ---------------------------------------------
    def _run_extern_map(self, c: FwComponent) -> None:
        module = vx.to_str(self.interp.call_method(c.sv, "extern_module"))
        rec = ExternRec(module=module)
        m = self.interp.allocate(ty.canon(self.pkg.find("fw_extern_map")))
        self._maps[id(m)] = rec
        self._run(c, lambda: self.interp.call_method(c.sv, "extern_map", [m]))
        c.extern = rec

    def map_rec(self, m) -> ExternRec:
        return self._maps[id(m)]

    def map_prefix(self, m, b_sv, pre: str, rule_cls) -> Any:
        b = self.fw(unwrap(b_sv))
        rec = self.map_rec(m)
        if not isinstance(b, FwBundle):
            raise ElabError(f"{rec.module}: prefix() takes a bundle")
        r = PinRule(b, pre)
        rec.rules.append(r)
        sv = self.interp.allocate(rule_cls)
        self._rules[id(sv)] = r
        return sv

    def rule_rename(self, r_sv, member: str, part: str):
        self._rules[id(r_sv)].renames[member] = part
        return r_sv


class FeIntrinsics(Intrinsics):
    """Dispatches calls of the API's methods to :class:`FeRt`."""

    def __init__(self, rt: FeRt):
        self.rt = rt

    def _call(self, interp, sub, this, args, named=None):
        name = sub.name
        owner = interp.owner_class(sub)
        rt = self.rt
        if name == "__fw_create":
            return rt.create_component(owner, args[0], args[1], args[2])
        this_sym = interp.call_this
        api = ty.api_name(owner) if owner is not None else None
        if api is None:
            return NOT_HANDLED
        a = list(args)
        if api in _SIGNAL_DIR:
            if name == "create":
                return rt.create_signal(owner, api, a[0], a[1])
            if name == "connect":
                return rt.connect(this, a[0], "connect", this_sym)
            if name == "tie":
                return rt.tie(this, a[0], this_sym)
        elif api in _ENDPOINT or api in _CFG_EP:
            if name == "create":
                return rt.create_endpoint(owner, api, a[0], a[1])
            if name in ("connect", "connect_up"):
                return rt.link(this, a[0], name == "connect_up")
            if name == "set_imp":
                return rt.set_imp(this, a[0])
            if name == "add_listener":
                return rt.add_listener(this, a[0])
            if name == "set" and api in _CFG_EP:
                return rt.cfg_set(this, a[0])
            if name == "get" and api in _CFG_EP:
                return rt.cfg_get(this)
        elif api in _CFG:
            if name == "create":
                return rt.create_cfg(owner, api, a[0], a[1])
            if name == "set":
                return rt.cfg_set(this, a[0])
            if name == "get":
                return rt.cfg_get(this)
            if name in ("connect", "connect_up"):
                return rt.cfg_link(this, a[0], name == "connect_up")
            if name == "add_listener":
                return rt.add_listener(this, a[0])
        elif api in _ROLE:
            if name == "create":
                return rt.create_bundle(owner, api, a[0], a[1])
            if name in ("connect", "connect_up"):
                return rt.connect(this, a[0], name, this_sym)
        elif api == "fw_component":
            if name in ("build", "connect", "mk_params"):
                return NOT_HANDLED            # empty, and the default params_t
            if name == "connect_all":
                return rt.connect_all(a[0])
        elif api == "fw_bundle_base" and name == "build":
            return NOT_HANDLED
        elif api == "fw_ctor_of":
            if name == "get":
                return rt.ctor_get(owner)
            if name == "create":
                return rt.ctor_create(this, a[0], a[1], a[2])
        elif api == "fw_param_override":
            if name in ("set", "edit"):
                return rt.add_override(ty.type_param(owner, "P"), name, a[0], vx.to_str(a[1]), a[2])
        elif api == "fw_extern_map":
            rec = rt.map_rec(this)
            rec.ctl = rec.ctl or bool(interp.ctl)
            if name == "param":
                arg = rt.param_args.get(id(unwrap(a[1])))
                if arg is None:
                    raise ElabError(f"param({vx.to_str(a[0])!r}): not a value from "
                                    f"fw_value::of or fw_type::get")
                rec.params[vx.to_str(a[0])] = arg
                return None
            if name == "pin":
                rec.pins.append((vx.to_str(a[0]), rt.fw(unwrap(a[1]))))
                return None
            if name == "prefix":
                return rt.map_prefix(this, a[0], vx.to_str(a[1]), ty.canon(sub.returnType))
            if name == "tie":
                rec.ties[vx.to_str(a[0])] = vx.to_int(a[1])
                rec.tie_syms[vx.to_str(a[0])] = a[1]
                return None
            if name == "open":
                rec.opens.append(vx.to_str(a[0]))
                return None
            if name == "unused":
                rec.unused.append(rt.fw(a[0]))
                return None
        elif api == "fw_value" and name == "of":
            sv = interp.allocate(owner)
            rt.param_args[id(sv)] = ParamArg("value", ty.type_param(owner, "T", raw=True), a[0])
            return sv
        elif api == "fw_type" and name == "get":
            sv = interp.allocate(owner)
            t = ty.type_param(owner, "T", raw=True)
            ct = rt._call_class(owner)
            tt = None
            if ct is not None:
                tt = TType(t, tainted=True) if ct.tainted else ct.args.get("T")
            rt.param_args[id(sv)] = ParamArg("type", t, ttype=tt)
            return sv
        elif api == "fw_pin_rule" and name == "rename":
            return rt.rule_rename(this, vx.to_str(a[0]), vx.to_str(a[1]))
        elif api == "fw_domain":
            r = self._domain(owner, sub, this, name, a)
            if r is not NOT_HANDLED:
                return r
        elif api == "fw_domain_base" and name == "kind":
            return rt.dom.kind_of(this)
        elif api == "fw_domain_kind_type_of":
            if name == "get":
                return rt.dom.handle(owner)
            if name == "name":
                return rt.dom.handle_name(this)
        elif api in _PROTO_API:
            r = self._protocol(owner, this, api, name, a)
            if r is not NOT_HANDLED:
                return r
        if (api, name) in rt.extern_methods:
            raise ElabError(f"fe-sv does not implement {api}::{name}")
        return NOT_HANDLED        # written in SV in the API itself (fw_rv_if::build)

    def _domain(self, owner, sub, this, name, a):
        """``fw_domain #(K)``'s functions (domains.py)."""
        d = self.rt.dom
        if name == "create":
            return d.create(owner, sub, a[0], a[1], a[2])
        if name == "set":
            return d.set(owner, a[0], a[1], a[2])
        if name == "get":
            return d.get(owner, a[0], a[1])
        if name == "relate":
            return d.relate(a[0], a[1])
        if name in ("pin", "pin_out"):
            return d.pin(owner, sub, a[0], a[1], a[2], a[3], name == "pin_out")
        if name == "waive":
            return d.waive(owner, a[0], a[1], a[2])
        if name == "port_name":
            return d.port_name(owner, a[0], a[1], a[2])
        if name == "provides":
            return d.provides(owner, sub, a[0], a[1], a[2])
        if name == "drive":
            return d.drive(owner, sub, this, a[0], a[1])
        if name == "params":
            return d.params(this)
        return NOT_HANDLED

    def _protocol(self, owner, this, api, name, a):
        """The protocol classes' functions (protocols.py)."""
        p = self.rt.proto
        if api == "fw_protocol_type_of" and name == "get":
            return p.protocol_get(owner, a[0])
        if api == "fw_api_type_of" and name == "get":
            return p.api_get(owner)
        if api == "fw_protocol_decl":
            if name == "api":
                return p.decl_api(this, a[0], a[1])
            if name == "prefix":
                return p.decl_prefix(this, a[0])
        if api == "fw_protocol_family":
            return p.family_query(this, name)
        if api in ("fw_api_type", "fw_api_type_of") and name in ("name", "family", "protocols"):
            return p.api_query(this, name)
        if api in ("fw_protocol_type", "fw_protocol_type_of") and name != "get":
            if name == "abstractors":
                return p.abstractors(this)
            return p.protocol_query(this, name, a)
        if api == "fw_protocol":
            if name == "declare":
                return None           # the default: no APIs
            if name == "of":
                return p.of(a[0])
            if name == "role_of":
                return p.role_of(a[0])
            if name == "abstractor_for":
                return p.abstractor_for(a[0], a[1])
        return NOT_HANDLED

    def call(self, interp, sub, this, args, named=None):
        try:
            return self._call(interp, sub, this, args, named)
        except _Skip:
            return None

    def symbolic_type(self, interp, t):
        return self.rt.type_leaf(t)

    def symbolic_param(self, interp, sym):
        return self.rt.symbolic_param(sym)

    def ctl_force(self, interp, frame):
        return self.rt.ctl_force(frame)

    def ctl_end(self, interp, frame):
        if self.rt.current is not None:
            self.rt.current.frames.append(frame)


def _mparams(t, prefix=()) -> list:
    """``(flat name, path, type)`` of each integral field of struct type
    ``t``, nested unpacked structs flattened."""
    out = []
    if t is None:
        return out
    for f in ty.canon(t):
        if type(f).__name__ != "FieldSymbol":
            continue
        ft = ty.canon(f.type)
        path = prefix + (f.name,)
        if getattr(ft, "isIntegral", False):
            out.append(("_".join(path), path, ft))
        elif getattr(ft, "isUnpackedStruct", False):
            out.extend(_mparams(ft, path))
    return out


def _traced_cls(o):
    """The class whose parameters ``o``'s code is traced over: a
    component's class, a bundle's bundle type."""
    return o.btype if isinstance(o, FwBundle) else o.cls


def _struct_fields(t, v, prefix) -> list:
    """``(path, type, value)`` of each integral field of struct type ``t``
    with value ``v`` (a ``Struct`` or, packed, an int), nested structs
    flattened."""
    out = []
    for m in ty.canon(t):
        if type(m).__name__ != "FieldSymbol":
            continue
        ft = ty.canon(m.type)
        if isinstance(v, Struct):
            fv = unwrap(v[m.name])
        else:
            fv = norm(int(v) >> int(m.bitOffset), m.type)
        path = prefix + (m.name,)
        if getattr(ft, "isStruct", False):
            out.extend(_struct_fields(ft, fv, path))
        elif getattr(ft, "isIntegral", False):
            out.append((path, ft, fv))
    return out


def _get_path(v, path):
    for p in path:
        v = unwrap(v)[p]
    return v


def _set_path(v, path, x) -> None:
    for p in path[:-1]:
        v = v[p]
    v[path[-1]] = x
