"""The elaborated netlist (core-layer1.md §3.11): the conformance artifact.

The format is ``fw-hdl-spec/semantics/netlist.schema.json``, which is
normative for every spelling and ordering rule; rt-sv writes the same
format.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from zuspec.fe.sv.interp.values import Obj, Struct, const_to_py

from . import types as ty
from .objects import FwBundle, FwCfg, FwComponent, FwDomain, FwEndpoint, FwObject, FwSignal
from .resolve import endpoint_kind


def hexval(v: int) -> str:
    return "'h%x" % v


def spell(t) -> str:
    """A type's canonical spelling (netlist.schema.json ``type``)."""
    if t is None:
        return "?"
    c = ty.canon(t)
    if getattr(c, "isStruct", False) or getattr(c, "isEnum", False) or \
            getattr(c, "isPackedUnion", False) or getattr(c, "isUnpackedUnion", False):
        # Spelled by its typedef's name: the alias nearest the type.
        a, name = t, None
        while getattr(a, "isAlias", False):
            name = a.name
            a = a.targetType.type
        return name or str(c)
    if getattr(c, "isClass", False):
        vals = []
        for m in c:
            k = type(m).__name__
            if k == "TypeParameterSymbol":
                vals.append(spell(m.targetType.type))
            elif k == "ParameterSymbol" and not m.isLocalParam:
                vals.append(param_value(m.value, m.type))
        return f"{c.name}#({','.join(vals)})" if vals else c.name
    return str(c).replace(" signed", "\0").replace(" ", "").replace("\0", " signed")


def param_value(cv, t) -> str:
    """A value parameter in a class type's spelling."""
    v = const_to_py(cv, t)
    return _value_text(v, ty.canon(t))


def _value_text(v, t) -> str:
    if isinstance(v, str):
        return '"' + v + '"'
    if isinstance(v, Struct):
        fts = {f.name: f.type for f in t} if t is not None else {}
        return "'{" + ",".join(_value_text(x, ty.canon(fts.get(k))) for k, x in v.items()) + "}"
    if isinstance(v, int) and t is not None and getattr(t, "isStruct", False):
        # A packed struct: its members from the most significant bits down.
        fs = [f for f in t if type(f).__name__ == "FieldSymbol"]
        parts, lo = [], int(t.bitWidth)
        for f in fs:
            w = int(ty.canon(f.type).bitWidth)
            lo -= w
            parts.append(_value_text((v >> lo) & ((1 << w) - 1), ty.canon(f.type)))
        return "'{" + ",".join(parts) + "}"
    if t is not None and getattr(t, "isEnum", False):
        for m in t:
            if type(m).__name__ == "EnumValueSymbol" and const_to_py(m.value, t) == v:
                return m.name
    if isinstance(v, int) and t is not None and getattr(t, "isIntegral", False):
        w = int(t.bitWidth)
        if t.isSigned:
            v &= (1 << w) - 1
            return str(v - (1 << w) if v >> (w - 1) else v)
        return str(v & ((1 << w) - 1))
    return str(v)


def params_value(v, t, handle=None) -> Any:
    """A params field's value (netlist.schema.json ``params``). ``handle``,
    if given, writes a class handle it knows (a domain, as its path), or
    returns None to leave it to the default."""
    c = ty.canon(t)
    if isinstance(v, Obj) or v is None and c is not None and getattr(c, "isClass", False):
        if v is None:
            return None
        h = handle(v) if handle is not None else None
        if h is not None:
            return h
        d = ty.type_param(v.cls, "D")
        return spell(d) if d is not None else spell(v.cls)
    if isinstance(v, Struct):
        fts = {f.name: f.type for f in c}
        return {k: params_value(x, fts.get(k), handle) for k, x in v.items()}
    if isinstance(v, list):
        et = getattr(c, "arrayElementType", None)
        return [params_value(x, et, handle) for x in v]
    if isinstance(v, str):
        return v
    if isinstance(v, int) and c is not None and getattr(c, "isIntegral", False):
        w = int(c.bitWidth)
        if w > 64:
            return hexval(v & ((1 << w) - 1))
        return v & ((1 << w) - 1)
    return v


def _by_kind(m: Dict[tuple, Any], f) -> Dict[str, Dict[str, Any]]:
    """A map keyed by (kind name, association name) as kind -> name -> f(v)."""
    out: Dict[str, Dict[str, Any]] = {}
    for (k, n), v in m.items():
        out.setdefault(k, {})[n] = f(v)
    return out


def _domain_members(rt, o: FwObject, d: Dict[str, Any]) -> Dict[str, Any]:
    """The domain members of ``o``'s record (domains.md, Netlist), each
    present only when non-empty."""
    from .domains import value_json
    if o.assoc:
        d["assoc"] = _by_kind(o.assoc, lambda a: a.dom.path)
    if not isinstance(o, FwComponent):
        return d
    if o.waivers:
        ws = sorted(o.waivers, key=lambda w: (w.a.path, w.b.path, w.kname, w.seq))
        d["waivers"] = [{"kind": w.kname, "a": w.a.path, "b": w.b.path, "reason": w.reason}
                        for w in ws]
    if o.pnames:
        d["port_names"] = _by_kind(o.pnames, lambda n: n)
    if o.provs:
        d["provides"] = _by_kind(o.provs, lambda pt: value_json(rt, pt[0], pt[1]))
    if o.is_extern and o.extern is not None and o.extern.dpins:
        pins: Dict[str, Any] = {}
        for p in o.extern.dpins:
            pins.setdefault(p.pin, {"kind": p.kname,
                                    "domain": p.dom.path if p.dom is not None else None,
                                    "dir": "out" if p.out else "in",
                                    "attr": value_json(rt, p.attr, p.attr_type)})
        d.setdefault("extern", {})["domain_pins"] = pins
    return d


def _object(rt, o: FwObject) -> Dict[str, Any]:
    return _domain_members(rt, o, _object_l1(rt, o))


def _object_l1(rt, o: FwObject) -> Dict[str, Any]:
    if isinstance(o, FwDomain):
        from .domains import value_json
        drvs = sorted(o.drivers, key=lambda x: x.key)
        out = []
        for x in drvs:
            e = {"signal": x.sig.path} if x.sig is not None else \
                {"extern": x.extern.path, "pin": x.pin}
            e["attr"] = value_json(rt, x.attr, x.attr_type)
            out.append(e)
        return {"path": o.path, "kind": "domain", "type": spell(o.cls),
                "params": value_json(rt, o.params, o.params_type), "drivers": out}
    if isinstance(o, FwComponent):
        d: Dict[str, Any] = {"path": o.path, "kind": "component", "type": spell(o.cls)}
        if o.params_type is not None:
            fts = {f.name: f.type for f in ty.canon(o.params_type)}
            d["params"] = {k: params_value(x, fts.get(k)) for k, x in o.params.items()}
        else:
            d["params"] = {}
        d["param_src"] = dict(o.param_src)
        if o.is_extern and o.extern is not None:
            rec = o.extern
            pins = {}
            for pin, (what, s) in getattr(rec, "pin_map", {}).items():
                if what == "port":
                    pins[pin] = ".".join(s.rel_path(o))
            from .params import netlist_entry
            d["extern"] = {"module": rec.module,
                           "params": {k: netlist_entry(a) for k, a in rec.params.items()},
                           "pins": pins,
                           "ties": {k: hexval(v) for k, v in rec.ties.items()},
                           "open": sorted(rec.opens),
                           "unused": sorted(".".join(u.rel_path(o))
                                            for u in getattr(rec, "unused_ports", []))}
        return d
    if isinstance(o, FwBundle):
        d = {"path": o.path, "kind": o.kind, "type": spell(o.btype)}
        if getattr(o, "proto", None) is not None:
            d["protocol"] = o.proto.name
            d["role"] = _ROLE[o.kind]
        return d
    if isinstance(o, (FwEndpoint, FwCfg)):
        d = {"path": o.path, "kind": o.kind, "type": spell(o.cls)}
        if o.proto is not None:
            d["protocol"] = o.proto.name
        return d
    s: FwSignal = o
    if s.is_port:
        return {"path": s.path, "kind": "port", "type": spell(s.dtype),
                "dir": s.effective_dir(), "width": s.width}
    return {"path": s.path, "kind": "signal", "type": spell(s.dtype), "width": s.width}


_ROLE = {"bundle": "initiator", "mirror": "target", "monitor": "monitor"}


def _walk(o: FwObject, out: List[FwObject]) -> None:
    out.append(o)
    for c in o.children.values():
        _walk(c, out)


def netlist(rt) -> Dict[str, Any]:
    objs: List[FwObject] = []
    if rt.top is not None:
        _walk(rt.top, objs)
    nets = []
    for scope in rt.components:
        for n in rt.nets.get(id(scope), []):
            kinds = {id(s): endpoint_kind(s, scope) for s in n.members}
            sources = sorted(s.path for s in n.sources)
            nets.append({
                "scope": scope.path,
                "source": sources[0] if sources else None,
                "tie": hexval(n.tie) if n.tie is not None else None,
                "sinks": sorted(s.path for s in n.sinks),
                "signals": sorted(s.path for s in n.members if kinds[id(s)] == "none"),
                "inouts": sorted(s.path for s in n.members if kinds[id(s)] == "both"),
            })
    nets.sort(key=lambda n: (n["scope"], min(
        [p for p in [n["source"]] if p] + n["sinks"] + n["signals"] + n["inouts"])))
    diags = sorted({(d.id, d.path) for d in rt.diags}, key=lambda x: (x[1], x[0]))
    out = {
        "top": spell(rt.top.cls) if rt.top is not None else "",
        "objects": sorted((_object(rt, o) for o in objs), key=lambda d: d["path"]),
        "nets": nets,
        "diagnostics": [{"id": i, "path": p} for i, p in diags],
    }
    binds = sorted({(b.a.path, b.b.path, b.kind) for b in rt.binds})
    if binds:
        out["bindings"] = [{"kind": k, "from": a, "to": b} for a, b, k in binds]
    protos = rt.proto.table()
    if protos:
        out["protocols"] = protos
    return out


def canonical(nl) -> str:
    return json.dumps(nl, indent=2, sort_keys=True) + "\n"
