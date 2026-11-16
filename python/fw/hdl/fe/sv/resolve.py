"""Resolution (core-layer1.md §3.2 step 4): nets, connection checks, and
extern pin maps checked against module headers (§3.5, §3.7).

Every check reports all its errors, not only the first.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .objects import (BUNDLE, MIRROR, MONITOR, Conn, FwBundle, FwComponent, FwObject,
                      FwSignal)


class Net:
    """A net formed in ``scope``: its endpoints, and the tie on it if any."""

    def __init__(self, scope: FwComponent):
        self.scope = scope
        self.members: List[FwSignal] = []
        self.tie: Optional[int] = None
        self.tied: List[FwSignal] = []
        self.sources: List[FwSignal] = []
        self.sinks: List[FwSignal] = []


def _role_of(o: FwObject, scope: FwComponent) -> str:
    """``own`` or ``child``: where an endpoint sits relative to ``scope``."""
    return "own" if o.scope is scope else "child"


def endpoint_kind(s: FwSignal, scope: FwComponent) -> str:
    """source | sink | both | none, seen from ``scope`` (§3.5 table)."""
    d = s.effective_dir()
    if d is None:
        return "none"                       # a local signal is the net itself
    if d == "inout":
        return "both"
    own = s.scope is scope
    if own:
        return "source" if d == "in" else "sink"
    return "source" if d == "out" else "sink"


class _UF:
    def __init__(self):
        self.p: Dict[int, int] = {}

    def find(self, x: int) -> int:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        self.p[self.find(a)] = self.find(b)


def _in_scope(o: FwObject, scope: FwComponent) -> bool:
    """Own objects, and the ports and bundles of direct children (§3.5)."""
    s = o.scope
    if s is scope:
        return True
    return s.parent is scope and (not isinstance(o, FwSignal) or o.is_port)


def _expand(rt, c: Conn) -> List[Tuple[FwSignal, FwSignal]]:
    """A connection as signal pairs; checks scope, types and roles."""
    a, b = c.a, c.b
    for o in (a, b):
        if not _in_scope(o, c.scope):
            rt.error("FW-E-CONN-SCOPE", o.path,
                     f"{c.scope.path} cannot connect {o.path}: outside its scope")
            return []
    if isinstance(a, FwSignal) and isinstance(b, FwSignal):
        if not a.dtype.isEquivalent(b.dtype):
            rt.error("FW-E-CONN-TYPE", a.path,
                     f"{a.path} ({a.dtype}) and {b.path} ({b.dtype}) have different types")
            return []
        return [(a, b)]
    if isinstance(a, FwBundle) and isinstance(b, FwBundle):
        if a.btype_name != b.btype_name:
            rt.error("FW-E-CONN-TYPE", a.path,
                     f"{a.path} ({a.btype_name}) and {b.path} ({b.btype_name}) "
                     f"are different bundle types")
            return []
        # Roles are not checked here: a wrong pairing (initiator to
        # initiator) shows up as the net errors it causes (connections.md).
        out = []
        for name, ma in a.children.items():
            mb = b.children.get(name)
            if isinstance(ma, FwBundle):
                out += _expand(rt, Conn(c.scope, ma, mb, c.how))
            else:
                out.append((ma, mb))
        return out
    rt.error("FW-E-CONN-TYPE", a.path, f"cannot connect {a.path} to {b.path}")
    return []


def _signals(o: FwObject) -> List[FwSignal]:
    out: List[FwSignal] = []
    for ch in o.children.values():
        if isinstance(ch, FwSignal):
            out.append(ch)
        elif isinstance(ch, FwBundle):
            out += _signals(ch)
    return out


def scope_endpoints(scope: FwComponent) -> List[FwSignal]:
    """Every signal a net in ``scope`` may contain: own signals and ports,
    and the ports of direct children."""
    out = _signals(scope)
    for ch in scope.children.values():
        if isinstance(ch, FwComponent):
            out += [s for s in _signals(ch) if s.is_port]
    return out


def check_flat_names(rt, c: FwComponent) -> None:
    """Two ports, signals or bundle members of one component that flatten
    to the same name: FW-E-NAME-FLAT on the later one (bundles.md)."""
    seen: Dict[str, FwSignal] = {}
    for s in sorted(_signals(c), key=lambda x: x.seq):
        flat = "_".join(s.rel_path(c))
        if flat in seen:
            rt.error("FW-E-NAME-FLAT", s.path, f"flattens to {flat!r}, as {seen[flat].path} does")
        else:
            seen[flat] = s


def resolve(rt) -> None:
    rt.nets = {}
    for c in rt.components:
        check_flat_names(rt, c)
    by_scope: Dict[int, List[Conn]] = {}
    for c in rt.conns:
        by_scope.setdefault(id(c.scope), []).append(c)
    ties_by_scope: Dict[int, list] = {}
    for t in rt.ties:
        ties_by_scope.setdefault(id(t.scope), []).append(t)

    for scope in rt.components:
        if scope.is_extern:
            if any(isinstance(ch, FwComponent) for ch in scope.children.values()):
                rt.error("FW-E-EXT-PORT", scope.path, "an extern has no children")
            continue
        uf = _UF()
        sig: Dict[int, FwSignal] = {}
        connected = set()
        for c in by_scope.get(id(scope), []):
            for a, b in _expand(rt, c):
                sig[id(a)], sig[id(b)] = a, b
                uf.union(id(a), id(b))
                connected.update((id(a), id(b)))
        tied: Dict[int, int] = {}
        for t in ties_by_scope.get(id(scope), []):
            if not _in_scope(t.sig, scope):
                rt.error("FW-E-CONN-SCOPE", t.sig.path, f"{scope.path} cannot tie {t.sig.path}")
                continue
            sig[id(t.sig)] = t.sig
            uf.find(id(t.sig))
            tied[id(t.sig)] = t.value
        nets: Dict[int, Net] = {}
        for k, s in sig.items():
            r = uf.find(k)
            n = nets.setdefault(r, Net(scope))
            n.members.append(s)
            if k in tied:
                n.tied.append(s)
        for n in nets.values():
            if n.tied:
                n.tied.sort(key=lambda x: x.path)
                n.tie = tied[id(n.tied[0])]
            kinds = [(s, endpoint_kind(s, scope)) for s in n.members]
            n.sources = [s for s, k in kinds if k == "source"]
            n.sinks = [s for s, k in kinds if k == "sink"]
            only_inout = all(k in ("both",) for _, k in kinds)
            if only_inout:
                continue
            drivers = len(n.sources) + len(n.tied)
            tied_m = [s for s in n.members if id(s) in tied]
            if drivers > 1:
                rt.error("FW-E-NET-MULTI", min(s.path for s in n.sources + tied_m),
                         "more than one source: " + ", ".join(
                             sorted(s.path for s in n.sources) + (["tie"] if n.tie is not None else [])))
            elif drivers == 0 and n.sinks and not any(k == "both" for _, k in kinds):
                rt.error("FW-E-NET-UNDRIVEN", min(s.path for s in n.sinks),
                         "no source for " + ", ".join(sorted(s.path for s in n.sinks)))
        rt.nets[id(scope)] = list(nets.values())
        # Unconnected effective inputs of children (§3.5).
        for ch in scope.children.values():
            if not isinstance(ch, FwComponent):
                continue
            for s in _signals(ch):
                if s.is_port and endpoint_kind(s, scope) == "sink" and id(s) not in sig:
                    rt.error("FW-E-IN-OPEN", s.path, "input not connected or tied")

    for c in rt.components:
        if c.is_extern and c.extern is not None:
            check_extern(rt, c)
    from .domains import check as domain_check, passes as domain_passes
    domain_check(rt)
    if not rt.force:          # not an explore() run: its domains are borrowed
        domain_passes(rt)


# -- externs -----------------------------------------------------------------

def _flat(path: List[str]) -> str:
    return "_".join(path)


def extern_ports(c: FwComponent) -> List[Tuple[List[str], FwSignal]]:
    """The extern's ports, with their member paths, in creation order."""
    out = []

    def walk(o, pre):
        for ch in o.children.values():
            if isinstance(ch, FwBundle):
                walk(ch, pre + [ch.name])
            elif isinstance(ch, FwSignal) and ch.is_port:
                out.append((pre + [ch.name], ch))
    walk(c, [])
    return out


def pin_map(rt, c: FwComponent):
    """Pin name → ("port", sig) | ("tie", v) | ("open", None) | ("domain",
    DomPin), from the map
    and the default rule, before checking against a header. Reports a pin
    mapped twice."""
    rec = c.extern
    out: Dict[str, Tuple[str, object]] = {}
    twice: List[str] = []

    def put(pin, v):
        if pin in out:
            twice.append(pin)       # the first mapping stays (netlist.schema.json)
        else:
            out[pin] = v
    # unused(): ports with no pin. Rules and the default rule skip them, so
    # their place among the other entries does not matter (extern.md).
    unused: Dict[int, FwSignal] = {}
    for o in rec.unused:
        if isinstance(o, FwBundle) and o.scope is c:
            leaves = [s for _, s in extern_ports_of(o)]
        elif isinstance(o, FwSignal) and o.is_port and o.scope is c:
            leaves = [o]
        else:
            rt.error("FW-E-EXT-PORT", o.path if o is not None else c.path,
                     f"unused() does not name a port or bundle of {c.path}")
            continue
        for s in leaves:
            if s.effective_dir() != "in":
                rt.error("FW-E-EXT-PORT", s.path,
                         f"an {s.effective_dir()} port cannot be unused: its net would have no driver")
            unused[id(s)] = s
    rec.unused_ports = sorted(unused.values(), key=lambda s: s.path)
    mapped = set(unused)
    for pin, s in rec.pins:
        if not isinstance(s, FwSignal) or not s.is_port or s.scope is not c:
            rt.error("FW-E-EXT-PORT", s.path if s is not None else c.path,
                     f"pin({pin!r}) does not name a port of {c.path}")
            continue
        if id(s) in unused:
            rt.error("FW-E-EXT-PORT", s.path, f"both unused and mapped to pin {pin!r}")
            continue
        put(pin, ("port", s))
        mapped.add(id(s))
    for dp in rec.dpins:
        put(dp.pin, ("domain", dp))
    for r in rec.rules:
        for path, s in extern_ports_of(r.bundle):
            if id(s) in mapped:
                continue
            put(r.prefix + r.renames.get(".".join(path), _flat(path)), ("port", s))
            mapped.add(id(s))
    for path, s in extern_ports(c):
        if id(s) not in mapped:
            put(_flat(path), ("port", s))
            mapped.add(id(s))
    for pin, v in rec.ties.items():
        put(pin, ("tie", v))
    for pin in rec.opens:
        put(pin, ("open", None))
    return out, twice


def extern_ports_of(b: FwBundle) -> List[Tuple[List[str], FwSignal]]:
    out = []

    def walk(o, pre):
        for ch in o.children.values():
            if isinstance(ch, FwBundle):
                walk(ch, pre + [ch.name])
            elif isinstance(ch, FwSignal):
                out.append((pre + [ch.name], ch))
    walk(b, [])
    return out


def check_extern(rt, c: FwComponent) -> None:
    """Check ``c``'s pin map against its module's header, and record the
    resolved map in module order (§3.7)."""
    rec = c.extern
    m, twice = pin_map(rt, c)
    for pin in sorted(set(twice)):
        rt.error("FW-E-EXT-PIN", c.path, f"pin {pin!r} of {rec.module} mapped twice")
    rec.pin_map = m
    from .params import check as param_check, sv_value
    header = rt.headers.get(rec.module, {}) if rt.headers is not None else None
    if header is not None:
        # Parameters first (FW-E-EXT-PARAM); the ones that fit then shape
        # the header the pins are checked against.
        fits = {}
        for name, arg in rec.params.items():
            why = param_check(arg, header.param_info.get(name))
            if why:
                rt.error("FW-E-EXT-PARAM", c.path, f"parameter {name} of {rec.module}: {why}")
            else:
                fits[name] = sv_value(arg)
        if fits:
            header = rt.headers.get(rec.module, fits)
    if header is None:
        # No RTL given for the module: keep the map in the order it was
        # written; nothing to check it against.
        rec.resolved = [(pin, what, v, None) for pin, (what, v) in m.items()]
        return
    rec.header = header
    hpins = {p.name: p for p in header.ports}
    for pin, (what, v) in m.items():
        if pin not in hpins:
            if what == "port":
                rt.error("FW-E-EXT-PORT", v.path, f"maps to pin {pin!r}, which {rec.module} does not have")
            else:
                rt.error("FW-E-EXT-PIN", c.path, f"{rec.module} has no pin {pin!r}")
    resolved = []
    for hp in header.ports:
        if hp.name not in m:
            rt.error("FW-E-EXT-PIN", c.path, f"pin {hp.name!r} of {rec.module} is not mapped")
            continue
        what, v = m[hp.name]
        if what == "port":
            d = v.effective_dir()
            if d != hp.direction:
                rt.error("FW-E-EXT-DIR", v.path,
                         f"is {d}, but pin {hp.name!r} of {rec.module} is {hp.direction}")
            if v.width != hp.width:
                rt.error("FW-E-EXT-WIDTH", v.path,
                         f"is {v.width} bits, but pin {hp.name!r} of {rec.module} is {hp.width}")
        elif what == "tie" and hp.direction != "in":
            rt.error("FW-E-EXT-DIR", c.path, f"tie on {rec.module} pin {hp.name!r}, an {hp.direction}")
        elif what == "open" and hp.direction == "in":
            rt.error("FW-E-EXT-DIR", c.path, f"open on {rec.module} input pin {hp.name!r}")
        elif what == "domain":
            want = "out" if v.out else "in"
            if hp.direction != want:
                rt.error("FW-E-DOM-PIN", c.path, f"domain pin {hp.name!r} of {rec.module} is "
                         f"an {hp.direction}, mapped as an {want}")
            if hp.width != 1:
                rt.error("FW-E-DOM-PIN", c.path, f"domain pin {hp.name!r} of {rec.module} is "
                         f"{hp.width} bits")
        resolved.append((hp.name, what, v, hp.width))
    rec.resolved = resolved
