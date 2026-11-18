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

SCENARIOS = ["sock_basic", "sock_up", "sock_port_export", "cfg_basic", "cfg_ep_basic",
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
            if not c.derived:       # derived ones are the IR's own (F4)
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
            t = ctxt.type_m[a.api]
            if isinstance(t, ir.DataTypeParameterized):
                t = t.body
            assert isinstance(t, ir.DataTypeProtocol) and t.methods


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


# -- F3: API methods, bundle protocols, set_imp ---------------------------

def test_api_methods():
    """An API type has its methods: a parameterized API as a
    ``DataTypeParameterized`` over a ``DataTypeProtocol`` written over its
    own parameters (as the scan's IR)."""
    _, ctxt, _ = _elab("proto_abs")
    for name, meth, d in (("test_put_if", "put", ir.SignalDirection.INPUT),
                          ("test_get_if", "get", ir.SignalDirection.OUTPUT)):
        t = ctxt.type_m[name]
        assert isinstance(t, ir.DataTypeParameterized)
        assert [p.name for p in t.template_params] == ["T"]
        assert isinstance(t.body, ir.DataTypeProtocol)
        (f,) = t.body.methods
        assert f.name == meth and f.is_async
        (a,) = f.args.args
        assert a.datatype == ir.DataTypeRef(ref_name="T") and a.direction == d
    hubs = [t for t in ctxt.type_m.values() if isinstance(t, ir.DataTypeProtocolHub)]
    assert {a.api for h in hubs for a in h.apis} == {"test_put_if", "test_get_if"}


def test_bundle_protocol():
    """A bundle type names its protocol; in a generic bundle type, over
    its parameters."""
    _, ctxt, _ = _elab("proto_param")
    bs = [t for t in ctxt.type_m.values() if isinstance(t, ir.DataTypeBundle)
          and t.name.startswith("test_rv_bundle")]
    assert bs
    for t in bs:
        assert [p.name for p in t.template_params] == ["W"]
        assert t.bundle_protocol == ir.ProtocolRef(
            family="test_rv_proto", params={"W": ir.ExprRefParam(name="W")})
    _, ctxt, _ = _elab("proto_abs")
    assert ctxt.type_m["test_phy_bundle"].bundle_protocol == \
        ir.ProtocolRef(family="test_phy_proto")


IMP_SV = """
`include "fw_hdl_macros.svh"
package scenario;
    import fw_hdl_pkg::*;
    interface class n_if;
        pure virtual task put(int x);
    endclass
    class n_proto extends fw_protocol;
        `fw_protocol_utils(n_proto)
        static function void declare(fw_protocol_decl d);
            d.api(fw_api_type_of #(n_if)::get(), FW_PROTO_REQ);
        endfunction
    endclass
    class obj_imp implements n_if;
        virtual task put(int x);
        endtask
    endclass
    class kid extends fw_component implements n_if;
        `fw_component_utils(kid)
        virtual task put(int x);
        endtask
    endclass
    class top extends fw_component implements n_if;
        `fw_component_utils(top)
        fw_export #(n_if) derived, self, child, member, loc;
        kid     k;
        obj_imp m;
        virtual function void build();
            obj_imp l = new;
            m = new;
            k = kid::create("k", this);
            derived = fw_export #(n_if)::create("derived", this);
            self    = fw_export #(n_if)::create("self", this);
            child   = fw_export #(n_if)::create("child", this);
            member  = fw_export #(n_if)::create("member", this);
            loc     = fw_export #(n_if)::create("loc", this);
            self.set_imp(this);
            child.set_imp(k);
            member.set_imp(m);
            loc.set_imp(l);
            void'(n_proto::get_type());
        endfunction
        virtual task put(int x);
        endtask
    endclass
endpackage
"""


def test_set_imp(tmp_path):
    """``set_imp`` is ``ApiEndpoint.imp``: a child by its path, a member
    object by the member; derived (``[]``) for the owner and for an object
    no member holds."""
    f = tmp_path / "top.sv"
    f.write_text(IMP_SV)
    r = elaborate([str(f)], top="scenario::top")
    assert r.ok, "\n".join(map(str, r.diags))
    ctxt, top = r.to_ir()
    imps = {e.name: e.imp for e in ctxt.type_m[top].api_endpoints}
    assert imps == {"derived": [], "self": [], "child": ["k"], "member": ["m"], "loc": []}
    # A non-parameterized API: a DataTypeProtocol with its methods.
    t = ctxt.type_m["n_if"]
    assert isinstance(t, ir.DataTypeProtocol) and [m.name for m in t.methods] == ["put"]


# -- F4: cfg bindings and listeners are connected, not run -----------------

def test_cfg_listeners_and_resolved_sources():
    """cfg_ep_basic: each sink's resolved source is a derived connection
    in the scope holding both; each ``add_listener`` is a ``CfgListener``
    in the scope whose code made it. Nothing is pushed: the sinks hold no
    value and no listener ran."""
    r, ctxt, top = _elab("cfg_ep_basic")
    ts = {p: t for p, t in _insts(ctxt, top, "top")}
    derived = [(c.publisher, c.consumer) for c in ts["top"].cfg_connections if c.derived]
    assert derived == [(["d", "p"], ["w", "x", "e"]), (["d", "s"], ["w", "x", "t"])]
    assert not any(c.derived for p, t in ts.items() if p != "top" for c in t.cfg_connections)
    x = ts["top.w.x"]
    assert x.cfg_listeners == [ir.CfgListener(sink=["e"], listener=[]),
                               ir.CfgListener(sink=["t"], listener=[])]
    assert all(not t.cfg_listeners for p, t in ts.items() if p != "top.w.x")
    sinks = [o for o in r.rt._by_sv.values() if getattr(o, "cfg", "") == "sink"]
    assert sinks and all(o.value is None for o in sinks)
    xo = next(o for o in r.rt.components if o.path == "top.w.x")
    assert xo.sv.get("n") == 0          # changed() never ran



# -- F5: constructor queries answer from the classes the design names -----

ABS_SV = """
`include "fw_hdl_macros.svh"
package scenario;
    import fw_hdl_pkg::*;
    import fw_hdl_std_pkg::*;
    `include "test_rv_proto.svh"
    `include "test_pkt_phy.svh"
    class rv_to_pkt #(int W = 8) extends fw_component implements test_put_if #(test_pkt_t);
        typedef rv_to_pkt #(W) this_t;
        `fw_abstractor_param_utils(rv_to_pkt, test_rv_proto #(W), test_pkt_proto)
        fw_export #(test_put_if #(test_pkt_t)) up;
        fw_bundle #(test_rv_bundle #(W))       dn;
        virtual function void build();
            up = fw_export #(test_put_if #(test_pkt_t))::create("up", this);
            dn = fw_bundle #(test_rv_bundle #(W))::create("dn", this);
        endfunction
        virtual task put(test_pkt_t t);
        endtask
    endclass
    typedef rv_to_pkt #(8) rv8_to_pkt;      // names one specialization
    class top extends fw_component;
        `fw_component_utils(top)
        fw_bundle #(test_rv_bundle #(8)) dn;
        fw_component a8[$];
        virtual function void build();
            fw_component_ctor_q q8  = test_rv_proto #(8)::get_type().abstractors();
            fw_component_ctor_q q16 = test_rv_proto #(16)::get_type().abstractors();
            dn = fw_bundle #(test_rv_bundle #(8))::create("dn", this);
            foreach (q8[i])
                a8.push_back(q8[i].create($sformatf("a8_%0d", i), this, fw_component::mk_params()));
            foreach (q16[i])
                void'(q16[i].create($sformatf("a16_%0d", i), this, fw_component::mk_params()));
        endfunction
        virtual function void connect();
            rv_to_pkt #(8) a;
            if (a8.size() > 0 && $cast(a, a8[0])) a.dn.connect_up(dn);
        endfunction
    endclass
endpackage
"""


def test_abstractors_named_specializations(tmp_path):
    """A parameterized abstractor is found for the specializations the
    design names (a typedef), and for no other."""
    f = tmp_path / "top.sv"
    f.write_text(ABS_SV)
    lib = spec_dir() / "conformance" / "_lib"
    r = elaborate([str(f)], top="scenario::top", incdirs=[str(lib)])
    assert r.ok, "\n".join(map(str, r.diags))
    comps = {o["path"]: o["type"] for o in r.netlist()["objects"] if o["kind"] == "component"}
    assert comps == {"top": "top", "top.a8_0": "rv_to_pkt#(8)"}
