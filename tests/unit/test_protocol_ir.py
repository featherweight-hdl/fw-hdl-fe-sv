"""Protocols as Zuspec IR (protocol-kit-plan §8.11, the Z7 shapes): what
ir_map writes for protocol hubs, converters, bundle protocols, endpoints,
configuration objects and their connections matches the netlist, on the
conformance scenarios; and it round-trips through the serializer."""
import json

import pytest

import zuspec.ir.core as ir
from zuspec.ir.core.deserializer import IRDeserializer
from zuspec.ir.core.serializer import IRSerializer

from fw.hdl.fe.sv import elaborate
from fw.hdl.fe.sv.api import spec_dir

SCENARIOS = ["sock_basic", "sock_up", "sock_port_export", "cfg_basic",
             "cfg_explicit", "cfg_constref_ok", "proto_basic", "proto_bundle", "proto_param",
             "proto_abs", "proto_xtor", "proto_xtor_param"]


def _elab(name):
    d = spec_dir() / "conformance" / name
    r = elaborate([str(d / "top.sv")], top="scenario::top",
                  incdirs=[str(d), str(d.parent / "_lib")])
    assert r.ok, "\n".join(map(str, r.diags))
    ctxt, top = r.to_ir()
    return r, ctxt, top


def _insts(ctxt, tname, path):
    """(path, component type) of every instance below the top, by fields."""
    t = ctxt.type_m[tname]
    yield path, t
    for f in t.fields:
        dt = f.datatype
        if isinstance(dt, ir.DataTypeRef) and \
                isinstance(ctxt.type_m.get(dt.ref_name), ir.DataTypeComponent):
            yield from _insts(ctxt, dt.ref_name, f"{path}.{f.name}")


def _ref(rt, spec):
    """A protocols-table key as (family, params) in the IR's form."""
    rec = rt.proto.recs[spec]
    return rec.family, {k: v for k, v in rec.params.items()}


def _pref(p):
    return p.family, {k: (v.ref_name if isinstance(v, ir.DataTypeRef) else v.value)
                      for k, v in p.params.items()}


def _flat(r, ctxt, top):
    """What the IR says, with absolute paths, in the netlist's terms."""
    eps, cfgs, binds, bundles, convs = {}, {}, set(), {}, {}
    for p, t in _insts(ctxt, top, "top"):
        for e in t.api_endpoints:
            eps[f"{p}.{e.name}"] = (e.kind, _pref(e.protocol) if e.protocol else e.api)
        for c in t.cfg_publishers:
            cfgs[f"{p}.{c.name}"] = "cfg_src"
        for c in t.cfg_consumers:
            cfgs[f"{p}.{c.name}"] = "cfg_sink"
        for c in t.api_connections:
            binds.add(("api", ".".join([p] + c.src), ".".join([p] + c.dst)))
        for c in t.cfg_connections:
            binds.add(("cfg", ".".join([p] + c.publisher), ".".join([p] + c.consumer)))
        for b in t.bundle_protocols:
            bundles[".".join([p] + b.target)] = (b.role, _pref(b.protocol))
        if t.converter is not None:
            convs[p] = (_pref(t.converter.from_proto), _pref(t.converter.to_proto),
                        t.converter.role)
    return eps, cfgs, binds, bundles, convs


@pytest.mark.parametrize("name", SCENARIOS)
def test_ir_matches_netlist(name):
    r, ctxt, top = _elab(name)
    rt, nl = r.rt, r.netlist()
    eps, cfgs, binds, bundles, convs = _flat(r, ctxt, top)
    objs = nl["objects"]
    want_eps = {o["path"]: o for o in objs if o["kind"] == "api_endpoint"}
    assert sorted(eps) == sorted(want_eps)
    for p, o in want_eps.items():
        if "protocol" in o:
            assert eps[p][1] == _ref(rt, o["protocol"]), p
    assert cfgs == {o["path"]: o["kind"] for o in objs
                    if o["kind"] in ("cfg_src", "cfg_sink")}
    assert binds == {(b["kind"], b["from"], b["to"]) for b in nl.get("bindings", [])}
    # Bundle protocols and converters: the elaborated objects and the
    # protocols table (the netlist's bundle members are K4.6).
    want_b = {o.path: (o.kind, _ref(rt, o.proto.name)) for o in rt._by_sv.values()
              if getattr(o, "proto", None) is not None and o.kind in ("bundle", "mirror",
                                                                      "monitor")}
    assert bundles == want_b
    table = rt.proto.table()
    want_c = {}
    for o in objs:
        if o["kind"] != "component":
            continue
        for v in table.values():
            e = next((x for x in v["converters"] if x["type"] == o["type"]), None)
            if e is not None:
                want_c[o["path"]] = (_ref(rt, e["from"]), _ref(rt, e["to"]), e.get("role", ""))
    assert convs == want_c


@pytest.mark.parametrize("name", ["proto_basic", "proto_param", "proto_abs"])
def test_hubs(name):
    r, ctxt, _ = _elab(name)
    table = r.rt.proto.table()
    fams = {v["family"] for v in table.values()}
    hubs = {k: t for k, t in ctxt.type_m.items() if isinstance(t, ir.DataTypeProtocolHub)}
    assert sorted(hubs) == sorted(fams)
    for v in table.values():
        h = hubs[v["family"]]
        assert [tp.name for tp in h.template_params] == list(v["params"])
        assert [a.dir.name.lower() for a in h.apis] == [a["dir"] for a in v["apis"]]
        for a in h.apis:
            assert isinstance(ctxt.type_m[a.api], ir.DataTypeProtocol)


def test_derived_flag():
    # Every cfg binding is explicit (sink.connect(src)): none is derived.
    for name in ("cfg_basic", "cfg_explicit"):
        _, ctxt, top = _elab(name)
        conns = [c for _, t in _insts(ctxt, top, "top") for c in t.cfg_connections]
        assert conns and not any(c.derived for c in conns), name


@pytest.mark.parametrize("name", ["sock_up", "cfg_basic", "proto_xtor"])
def test_round_trip(name):
    _, ctxt, _ = _elab(name)
    text = IRSerializer().serialize(ctxt, None)
    d = IRDeserializer()
    d.auto_register(ir)
    back, _ = d.deserialize(text)
    assert back.type_m == ctxt.type_m
