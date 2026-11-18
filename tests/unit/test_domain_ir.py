"""Domains as Zuspec IR (core-domains-plan §6.1, §6.2): what ir_map writes
for domain objects, associations, drivers and extern domain pins, on the
conformance scenarios; and that it round-trips through the serializer."""
import json
import shutil
import subprocess

import pytest

import zuspec.ir.core as ir
from zuspec.ir.core.deserializer import IRDeserializer
from zuspec.ir.core.serializer import IRSerializer

from fw.hdl.fe.sv import elaborate
from fw.hdl.fe.sv.api import spec_dir
from fw.hdl.fe.sv.emit import emit_sv


def _ir(name):
    d = spec_dir() / "conformance" / name
    meta = json.loads((d / "meta.json").read_text())
    r = elaborate([str(d / "top.sv")], top="scenario::top", incdirs=[str(d)],
                  rtl=[str(d / f) for f in meta.get("rtl", [])])
    assert r.ok, "\n".join(map(str, r.diags))
    ctxt, top = r.to_ir()
    return ctxt, top


def _ref(r):
    return None if r is None else (r.decl, r.kind, r.assoc, r.target)


def test_kinds():
    ctxt, _ = _ir("dom_pin")
    clk, rst = ctxt.type_m["fw_clock_kind"], ctxt.type_m["fw_reset_kind"]
    assert isinstance(clk, ir.DataTypeDomainKind) and clk.physical
    assert sorted(clk.derivations) == ["derived", "gated", "muxed", "phase", "root"]
    assert clk.derivations["derived"].sources == ["from"] and clk.derivations["root"].sources == []
    S = ir.DomainSeverity
    assert clk.severities == {"same": S.NONE, "related": S.INFO, "unrelated": S.ERROR}
    assert rst.severities["related"] is S.WARNING


def test_pin_out_wrapper():
    """dom_pin_out: a derived clock driven by a divider's output pin, which
    states the derivation; a sink extern on the derived clock."""
    ctxt, top = _ir("dom_pin_out")
    t = ctxt.type_m[top]
    assert [(d.name, d.deriv, [_ref(s) for s in d.sources]) for d in t.domains] == [
        ("clk", "root", []), ("slow", "derived", [("clk", None, "", [])])]
    slow = t.domains[1]
    assert {k: v.value for k, v in slow.params.items()} == {"div": 2, "mul": 1, "phase_deg": 0, "period": 0}
    assert [(a.target, a.assoc, _ref(a.domain)) for a in t.domain_assocs] == [
        ([], "", ("clk", None, "", [])),
        (["u"], "out", ("slow", None, "", [])),
        (["e"], "", ("slow", None, "", []))]
    assert [(x.signal, _ref(x.domain)) for x in t.domain_drivers] == [
        (["clk_i"], ("clk", None, "", []))]
    div = ctxt.type_m["div2"]
    pins = {p.name: p for p in div.pins}
    # pin(m, "clk_i"): the extern's default; pin_out(m, "clk_o", get(this, "out")).
    assert pins["clk_i"].domain_kind == "fw_clock_kind" and pins["clk_i"].domain is None
    assert not pins["clk_i"].output
    assert pins["clk_o"].output and _ref(pins["clk_o"].domain) == (None, "fw_clock_kind", "out", [])


def test_inherited_domains_and_attrs():
    """dom_lookup: a child derives from what get(this) returns, so its
    class is written over its resolution, not a domain of its parent."""
    ctxt, _ = _ir("dom_lookup")
    leaf = ctxt.type_m["leaf"]
    seen = {d.name: d for d in leaf.domains}
    assert _ref(seen["seen"].sources[0]) == (None, "fw_clock_kind", "", [])
    assert _ref(seen["rseen"].params["clock"]) == (None, "fw_clock_kind", "", [])
    ctxt, top = _ir("dom_pin")
    t = ctxt.type_m[top]
    rst = next(x for x in t.domain_drivers if x.signal == ["rst_i"])
    assert {k: v.value for k, v in rst.attr.items()} == {"active_low": 0, "style": 2}
    assert _ref(next(d for d in t.domains if d.name == "rst").params["clock"]) == \
        ("clk", None, "", [])
    pins = {p.name: p for p in ctxt.type_m["dc"].pins}
    assert _ref(pins["wr_clk"].domain) == (None, "fw_clock_kind", "wr", [])
    assert {k: v.value for k, v in pins["rst_n"].domain_attr.items()} == \
        {"active_low": 1, "style": 0}
    assert pins["d"].path == ["d"] and pins["d"].domain_kind is None


@pytest.mark.parametrize("name", ["dom_pin_out", "dom_pin", "dom_named", "dom_intf",
                                  "dom_lookup", "dom_create"])
def test_round_trip(name):
    ctxt, _ = _ir(name)
    text = IRSerializer().serialize(ctxt, None)
    d = IRDeserializer()
    d.auto_register(ir)
    back, _ = d.deserialize(text)
    assert back.type_m == ctxt.type_m


def test_emit_domain_pins(tmp_path):
    """The divider's output pin drives the derived clock's net; the input
    pins and the sink read the nets of their domains (D6)."""
    ctxt, top = _ir("dom_pin_out")
    emit_sv(ctxt, top, tmp_path)
    text = (tmp_path / "top.sv").read_text()
    assert "  logic clk;\n  logic slow;\n" in text
    assert "  assign clk = clk_i;\n" in text
    assert ".clk_i(clk)" in text and ".clk_o(slow)" in text
    assert "sink_mod e (\n    .clk(slow)\n  );" in text


def test_user_kind_emits_nothing(tmp_path):
    """The toy macro kind (kind_user) is in the IR as a kind, with its
    domains and associations, and emits nothing: it is a marker kind (D7)."""
    ctxt, top = _ir("kind_user")
    k = ctxt.type_m["macro_kind"]
    assert isinstance(k, ir.DataTypeDomainKind) and not k.physical
    assert {r: s.name.lower() for r, s in k.severities.items()} == \
        {"same": "none", "related": "info", "unrelated": "info"}
    t = ctxt.type_m[top]
    assert [d.name for d in t.domains] == ["m0", "m1"]
    emit_sv(ctxt, top, tmp_path)
    text = (tmp_path / "top.sv").read_text()
    assert text.startswith("module top(\n\n);\n\n  logic a_o;\n")
    for name in ("m0", "m1", "macro"):
        assert name not in text


def _cross_reset(sev):
    d = spec_dir() / "conformance" / "dom_cross_reset"
    r = elaborate([str(d / "top.sv")], top="scenario::top", incdirs=[str(d)],
                  domain_severity=sev)
    return sorted((x.id, x.path) for x in r.diags)


def test_domain_severity_option():
    """domain_severity overrides the kinds' crossing severities, as a map
    or as text; a malformed option is a ValueError (D7)."""
    assert _cross_reset(None) == [("FW-E-DOM-CROSS", "top.a1.o"), ("FW-W-DOM-CROSS", "top.a0.o")]
    assert _cross_reset({"fw_reset_kind": {"unrelated": "warning"}}) == \
        [("FW-W-DOM-CROSS", "top.a0.o"), ("FW-W-DOM-CROSS", "top.a1.o")]
    assert _cross_reset("fw_reset_kind.related=none,fw_reset_kind.unrelated=info") == \
        [("FW-I-DOM-CROSS", "top.a1.o")]
    with pytest.raises(ValueError, match="relation"):
        _cross_reset("fw_reset_kind.close=none")


PKG = """
`include "fw_hdl_macros.svh"
package p;
    import fw_hdl_pkg::*;
    class leaf extends fw_component;
        `fw_component_utils(leaf)
        fw_output #(bit) o;
        virtual function void build();
            o = fw_output #(bit)::create("o", this);
        endfunction
    endclass

    // n children; with LOOP, each on a clock of its own made in the loop.
    class arr extends fw_component;
        typedef struct { int n; } params_t;
        static function params_t mk_params(int n = 2);
            params_t p; p.n = n; return p;
        endfunction
        `fw_component_params
        `fw_component_utils(arr)
        leaf ch[$];
        fw_clock_domain clk;
        virtual function void build();
            clk = fw_clock_domain::create("clk", this);
            fw_clock_domain::set(this, clk);
            for (int i = 0; i < params.n; i++) begin
                ch.push_back(leaf::create($sformatf("ch[%0d]", i), this));
`ifdef LOOP
                fw_clock_domain::set(ch[i], fw_clock_domain::create($sformatf("c%0d", i), this,
                                     fw_clock_kind::derived(clk)));
`endif
            end
        endfunction
    endclass

    class top extends fw_component;
        `fw_component_utils(top)
        arr a, b;
        virtual function void build();
            a = arr::create("a", this);
            b = arr::create("b", this, arr::mk_params(3));
        endfunction
    endclass
endpackage
"""


def _arr(tmp_path, loop: bool):
    f = tmp_path / "p.sv"
    f.write_text(PKG)
    r = elaborate([str(f)], top="p::top", defines={"LOOP": "1"} if loop else None)
    assert r.ok, "\n".join(map(str, r.diags))
    ctxt, top = r.to_ir()
    return r, ctxt


def test_general_class_with_domains(tmp_path):
    r, ctxt = _arr(tmp_path, False)
    arrs = [n for n in ctxt.type_m if n.startswith("arr")]
    assert arrs == ["arr"] and not r.ir_diags
    t = ctxt.type_m["arr"]
    assert t.template_params and [d.name for d in t.domains] == ["clk"]
    assert [(a.target, _ref(a.domain)) for a in t.domain_assocs] == [([], ("clk", None, "", []))]


def test_domain_in_loop_is_a_specialization(tmp_path):
    r, ctxt = _arr(tmp_path, True)
    assert sorted(n for n in ctxt.type_m if n.startswith("arr")) == ["arr", "arr__1"]
    assert {d.id for d in r.ir_diags} == {"FW-I-EMIT-SPEC"}
    assert any("loop or branch on parameters" in d.msg for d in r.ir_diags)
    t = next(ctxt.type_m[n] for n in ("arr", "arr__1") if len(ctxt.type_m[n].domains) == 4)
    assert [d.name for d in t.domains] == ["clk", "c0", "c1", "c2"]
    assert [a.target for a in t.domain_assocs] == [[], ["ch[0]"], ["ch[1]"], ["ch[2]"]]


PICK = """
`include "fw_hdl_macros.svh"
package q;
    import fw_hdl_pkg::*;
    class sfifo extends fw_component implements fw_extern_if;
        `fw_extern_utils(sfifo, "sfifo_mod")
        virtual function void extern_map(fw_extern_map m);
            fw_clock_domain::pin(m, "clk");
            fw_reset_domain::pin(m, "rst");
        endfunction
    endclass

    class afifo extends fw_component implements fw_extern_if;
        `fw_extern_utils(afifo, "afifo_mod")
        virtual function void extern_map(fw_extern_map m);
            fw_clock_domain::pin(m, "wr_clk", fw_clock_domain::get(this, "wr"));
            fw_clock_domain::pin(m, "rd_clk", fw_clock_domain::get(this, "rd"));
            fw_reset_domain::pin(m, "rst_n", null, fw_reset_kind::active_low());
        endfunction
    endclass

    // A parameter picks the FIFO; each arm associates its child.
    class pick extends fw_component;
        typedef struct { int async; } params_t;
        static function params_t mk_params(int async = 0);
            params_t p; p.async = async; return p;
        endfunction
        `fw_component_params
        `fw_component_utils(pick)
        sfifo sf;
        afifo af;
        virtual function void build();
            if (params.async != 0) begin
                af = afifo::create("af", this);
                fw_clock_domain::set(af, fw_clock_domain::get(this, "wr"), "wr");
                fw_clock_domain::set(af, fw_clock_domain::get(this, "rd"), "rd");
            end else begin
                sf = sfifo::create("sf", this);
                fw_clock_domain::set(sf, fw_clock_domain::get(this, "wr"));
            end
        endfunction
    endclass

    class top extends fw_component;
        `fw_component_utils(top)
        fw_input #(bit) clk_a_i, clk_b_i, rst_ni;
        fw_clock_domain a, b;
        fw_reset_domain rst;
        pick p0, p1;
        virtual function void build();
            clk_a_i = fw_input #(bit)::create("clk_a_i", this);
            clk_b_i = fw_input #(bit)::create("clk_b_i", this);
            rst_ni  = fw_input #(bit)::create("rst_ni", this);
            a   = fw_clock_domain::create("a", this);
            b   = fw_clock_domain::create("b", this);
            rst = fw_reset_domain::create("rst", this);
            fw_clock_domain::set(this, a);
            fw_reset_domain::set(this, rst);
            p0 = pick::create("p0", this);
            fw_clock_domain::set(p0, a, "wr");
            p1 = pick::create("p1", this, pick::mk_params(1));
            fw_clock_domain::set(p1, a, "wr");
            fw_clock_domain::set(p1, b, "rd");
        endfunction
        virtual function void connect();
            a.drive(clk_a_i);
            b.drive(clk_b_i);
            rst.drive(rst_ni, fw_reset_kind::active_low());
        endfunction
    endclass
endpackage
"""

STUBS = """
module sfifo_mod(input logic clk, input logic rst);
endmodule
module afifo_mod(input logic wr_clk, input logic rd_clk, input logic rst_n);
endmodule
"""


def test_association_under_branch_is_general(tmp_path):
    """An association of a child created under a branch on parameters goes
    in the child's generate arm: the class stays one parameterized module
    (§6.1). Its domain ports are those of both arms; a reset sink of the
    other polarity reads the module's inverted net."""
    f = tmp_path / "q.sv"
    f.write_text(PICK)
    r = elaborate([str(f)], top="q::top")
    assert r.ok, "\n".join(map(str, r.diags))
    ctxt, top = r.to_ir()
    assert not r.ir_diags and [n for n in ctxt.type_m if n.startswith("pick")] == ["pick"]
    t = ctxt.type_m["pick"]
    (g,) = t.generates
    da = [x for x in g.items if isinstance(x, ir.DomainAssoc)]
    de = [x for x in g.orelse if isinstance(x, ir.DomainAssoc)]
    assert [(x.target, x.assoc, _ref(x.domain)) for x in da] == [
        (["af"], "wr", (None, "fw_clock_kind", "wr", [])),
        (["af"], "rd", (None, "fw_clock_kind", "rd", []))]
    assert [(x.target, x.assoc, _ref(x.domain)) for x in de] == [
        (["sf"], "", (None, "fw_clock_kind", "wr", []))]
    assert not t.domain_assocs

    emit_sv(ctxt, top, tmp_path / "gen")
    pick = (tmp_path / "gen" / "pick.sv").read_text()
    assert "  input logic rd_clk,\n  input logic wr_clk,\n  input logic rst\n" in pick
    assert "  logic rst_inv;\n" in pick and "  assign rst_inv = ~rst;\n" in pick
    assert ".rst_n(rst_inv)" in pick and ".rst(rst)" in pick
    # Read in one arm only: also read by an _unused net, for lint.
    assert "  assign rd_clk_unused = rd_clk;\n" in pick and "wr_clk_unused" not in pick
    text = (tmp_path / "gen" / "top.sv").read_text()
    # p0 never sets "rd": its rd_clk falls back to p0's default, a.
    assert "pick #(.async(0)) p0 (\n    .rd_clk(a),\n    .wr_clk(a),\n    .rst(rst_inv)" in text
    assert "pick #(.async(1)) p1 (\n    .rd_clk(b),\n    .wr_clk(a),\n    .rst(rst_inv)" in text

    if shutil.which("verilator"):
        (tmp_path / "stubs.sv").write_text(STUBS)
        files = [str(tmp_path / "stubs.sv")] + sorted(str(p) for p in (tmp_path / "gen").glob("*.sv"))
        lint = subprocess.run(["verilator", "--lint-only", "-Wall", "-Wno-DECLFILENAME",
                               "--top-module", "top"] + files, capture_output=True, text=True)
        assert not [l for l in lint.stderr.splitlines() if "/gen/" in l], lint.stderr


def test_provides_in_ir():
    ctxt, _ = _ir("dom_pin_out")
    p = ctxt.type_m["div2"].domain_provides
    assert [(x.kind, x.assoc, x.deriv) for x in p] == [("fw_clock_kind", "out", "derived")]
    assert _ref(p[0].sources[0]) == (None, "fw_clock_kind", "", [])
    assert {k: v.value for k, v in p[0].params.items()} == {"div": 2, "mul": 1, "phase_deg": 0, "period": 0}


HIER = """
`include "fw_hdl_macros.svh"
package h;
    import fw_hdl_pkg::*;
    class rsink extends fw_component implements fw_extern_if;
        `fw_extern_utils(rsink, "rsink_mod")
        virtual function void extern_map(fw_extern_map m);
            fw_clock_domain::pin(m, "clk");
            fw_reset_domain::pin(m, "rst_n", null, fw_reset_kind::active_low());
        endfunction
    endclass

    // Realizes its default clock and reset through rsink.
    class blk extends fw_component;
        `fw_component_utils(blk)
        rsink s;
        virtual function void build();
            s = rsink::create("s", this);
        endfunction
    endclass

    // The same, naming its clock port.
    class nblk extends fw_component;
        `fw_component_utils(nblk)
        rsink s;
        virtual function void build();
            fw_clock_domain::port_name(this, "", "ck");
            s = rsink::create("s", this);
        endfunction
    endclass

    class top extends fw_component;
        `fw_component_utils(top)
        fw_input #(bit) clk_i, rst_i;
        fw_clock_domain clk;
        fw_reset_domain rst;
        blk  a, b;
        nblk n;
        virtual function void build();
            clk_i = fw_input #(bit)::create("clk_i", this);
            rst_i = fw_input #(bit)::create("rst_i", this);
            clk = fw_clock_domain::create("clk", this);
            rst = fw_reset_domain::create("rst", this, fw_reset_kind::root(.clock(clk)));
            fw_clock_domain::set(this, clk);
            fw_reset_domain::set(this, rst);
            a = blk::create("a", this);
            b = blk::create("b", this);
            n = nblk::create("n", this);
        endfunction
        virtual function void connect();
            clk.drive(clk_i);
            rst.drive(rst_i);
        endfunction
    endclass
endpackage
"""


def test_realization_through_fe_sv(tmp_path):
    """The passes run in elaboration: each class's generated ports, with
    the reset port's polarity its sinks agree on and a class's own port
    name; the kinds' port-name table in the IR."""
    f = tmp_path / "h.sv"
    f.write_text(HIER)
    r = elaborate([str(f)], top="h::top")
    assert r.ok, "\n".join(map(str, r.diags))
    ports = {t: [(p.kind, p.assoc, p.name, p.output) for p in v]
             for t, v in r.rt.dom.realization.ports.items()}
    assert ports == {
        "blk": [("fw_clock_kind", "", "clk", False), ("fw_reset_kind", "", "rst_n", False)],
        "nblk": [("fw_clock_kind", "", "ck", False), ("fw_reset_kind", "", "rst_n", False)]}
    ctxt, _ = r.to_ir()
    rst = ctxt.type_m["fw_reset_kind"]
    assert rst.port_names["/active_low=1,style=2"] == "rst_n"
    assert rst.port_names["/active_low=0,style=2"] == "rst"
    assert {k: v.value for k, v in rst.pin_default.items()} == {"active_low": 0, "style": 2}
    assert [(x.kind, x.assoc, x.port) for x in ctxt.type_m["nblk"].domain_port_names] == \
        [("fw_clock_kind", "", "ck")]


def test_zdc_kinds_match(tmp_path):
    """zdc writes the library kinds itself (zuspec.dataclasses.domain_ir,
    core-domains-plan "D8 design"); they match what fe-sv builds from the
    spec, so a zdc component and an fw-hdl one have the same kinds."""
    domain_ir = pytest.importorskip("zuspec.dataclasses.domain_ir")
    f = tmp_path / "h.sv"
    f.write_text(HIER)
    r = elaborate([str(f)], top="h::top")
    assert r.ok, "\n".join(map(str, r.diags))
    ctxt, _ = r.to_ir()
    for z in (domain_ir.clock_kind(), domain_ir.reset_kind()):
        k = ctxt.type_m[z.name]
        assert (z.physical, z.derivations, z.severities, z.polarity) == \
            (k.physical, k.derivations, k.severities, k.polarity), z.name
        assert {n: v.value for n, v in z.pin_default.items()} == \
            {n: v.value for n, v in k.pin_default.items()}, z.name
        assert z.port_names and all(k.port_names[n] == p for n, p in z.port_names.items())
