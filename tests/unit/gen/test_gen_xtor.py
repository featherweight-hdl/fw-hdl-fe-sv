"""``xtor``: the initiator/target/monitor a kit derives from a bundle of
channels, compiled with Verilator against fw_hdl_pkg, the std package and
the fixture."""
import shutil
import subprocess
from pathlib import Path

import pytest

from fw.hdl.fe.sv.elab import elaborate
from fw.hdl.fe.sv.scan import scan
from fw.hdl.gen import gen_kit

PKGS = Path(__file__).resolve().parents[4]
CONF = PKGS / "fw-hdl-spec" / "conformance"
RT = PKGS / "fw-hdl-rt-sv" / "sv"
STD = PKGS / "fw-hdl-spec" / "std"
SRC = Path(__file__).parent / "src"


def _scan(name):
    d = SRC / name if (SRC / name).is_dir() else CONF / name
    r = scan([str(d / "top.sv")], incdirs=[str(d), str(CONF / "_lib")])
    assert r.ok, r.diags
    return r

TOP = """
module xtor_top;
    import fw_hdl_pkg::*;
    import scenario::*;
    import scenario_gen_pkg::*;
    test_ch_initiator #(8) i;
    test_ch_target #(8)    t;
    test_ch_monitor #(8)   m;
endmodule
"""


#: Runs the b2b (rt-sv only: __fw_rt is rt-sv's elaborator) and prints
#: what the device and host got, and what a listener on the monitor saw.
B2B_TOP = """
module xtor_b2b;
    import fw_hdl_pkg::*;
    import scenario::*;
    import scenario_gen_pkg::*;
    import xtor_b2b_pkg::*;

    // Records each call the monitor's fan-outs make.
    class mon_l implements test_ch_req_if_listener #(8), test_ch_rsp_if_listener #(8);
        string got[$];
        virtual function void req_pre(input logic [7:0] t);
        endfunction
        virtual function void req_post(input logic [7:0] t);
            got.push_back($sformatf("req:%0d@%0d", t, cyc()));
        endfunction
        virtual function void rsp_pre(input test_ch_rsp_t t);
        endfunction
        virtual function void rsp_post(input test_ch_rsp_t t);
            got.push_back($sformatf("rsp:%0d@%0d", t.tag, cyc()));
        endfunction
    endclass

    top_c t;
    mon_l ml;

    initial begin
        string s;
        int nerr;
        t = top_c::create("top", null);
        ml = new();
        nerr = __fw_rt::elaborate(t);
        t.m.add_req_listener(ml);
        t.m.add_rsp_listener(ml);
        if (nerr == 0) __fw_rt_kern::run();
        s = "";
        foreach (t.d.got[i]) s = {s, " ", t.d.got[i]};
        $display("R dev%s", s);
        s = "";
        foreach (t.h.got[i]) s = {s, " ", t.h.got[i]};
        $display("R host%s", s);
        s = "";
        foreach (ml.got[i]) s = {s, " ", ml.got[i]};
        $display("R mon%s", s);
        foreach (__fw_rt::diags[i]) $display("D %s %s", __fw_rt::diags[i].id, __fw_rt::diags[i].path);
        $finish;
    end
endmodule
"""


def test_gen_xtor():
    r = _scan("proto_chan")
    sv = gen_kit(r)
    for name in ("test_ch_initiator", "test_ch_target", "test_ch_monitor"):
        assert f"class {name} " in sv


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
def test_gen_xtor_compiles(tmp_path):
    r = _scan("proto_chan")
    sv = gen_kit(r)
    gen = tmp_path / "scenario_gen_pkg.sv"
    gen.write_text(sv)
    top = tmp_path / "xtor_top.sv"
    top.write_text(TOP)
    d = SRC / "proto_chan"
    cmd = ["verilator", "--lint-only", "-Wno-fatal",
           f"-I{RT}", f"-I{PKGS / 'fw-hdl-spec' / 'bindings' / 'sv'}", f"-I{STD}",
           f"-I{d}", f"-I{CONF / '_lib'}",
           str(RT / "fw_hdl_pkg.sv"), str(STD / "fw_hdl_std_pkg.sv"),
           str(d / "top.sv"), str(gen), str(top), "--top-module", "xtor_top"]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
def test_gen_xtor_b2b(tmp_path):
    # Host -> generated initiator -> pins -> generated target -> device, and
    # each req's rsp back the same way; the run is clock-only (no RTL).
    r = _scan("proto_chan")
    gen = tmp_path / "scenario_gen_pkg.sv"
    gen.write_text(gen_kit(r))
    d = SRC / "proto_chan"
    mod = tmp_path / "xtor_b2b_top.sv"
    mod.write_text(B2B_TOP)
    obj = tmp_path / "obj"
    obj.mkdir()
    cmd = ["verilator", "--binary", "-Wno-fatal", "-Wno-lint", "-Wno-style",
           "--top-module", "xtor_b2b", "-Mdir", str(obj), "-o", "Vx",
           f"-I{RT}", f"-I{PKGS / 'fw-hdl-spec' / 'bindings' / 'sv'}", f"-I{STD}",
           f"-I{d}", f"-I{CONF / '_lib'}",
           str(RT / "fw_hdl_pkg.sv"), str(STD / "fw_hdl_std_pkg.sv"),
           str(d / "top.sv"), str(gen), str(d / "xtor_b2b.sv"), str(mod)]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    res = subprocess.run([str(obj / "Vx"), "+fw_edges=40"], capture_output=True, text=True)
    out = [l for l in res.stdout.splitlines() if l.startswith(("R ", "D "))]
    assert out == [
        "R dev 1@1 2@3 3@5 4@7",                    # each req waits for its rsp
        "R host 1:101@2 1:102@4 1:103@6 1:104@8",   # rsp one cycle after its req
        # the monitor's listener sees each handshake as its channel's call
        "R mon req:1@1 rsp:101@2 req:2@3 rsp:102@4 req:3@5 rsp:103@6 req:4@7 rsp:104@8",
    ]


def test_gen_xtor_b2b_elab(tmp_path):
    # fe-sv's static elaboration accepts the same pair (the target binds its
    # channel xtors' pins to the mirror's with peer connect).
    gen = tmp_path / "scenario_gen_pkg.sv"
    gen.write_text(gen_kit(_scan("proto_chan")))
    d = SRC / "proto_chan"
    r = elaborate([str(d / "top.sv"), str(gen), str(d / "xtor_b2b.sv")],
                  "xtor_b2b_pkg::top_c", incdirs=[str(d), str(CONF / "_lib")])
    assert r.ok, r.diags


def test_gen_xtor_lookup(tmp_path):
    # Static lookup over a scan of the generated package: bundle -> protocol
    # -> the transactor of the opposite mixin.
    gen = tmp_path / "scenario_gen_pkg.sv"
    gen.write_text(gen_kit(_scan("proto_chan")))
    d = SRC / "proto_chan"
    r = scan([str(d / "top.sv"), str(gen), str(d / "xtor_use.sv")],
             incdirs=[str(d), str(CONF / "_lib")])
    assert r.ok, r.diags
    assert r.xtor_for("test_ch_bundle#(8)", "bundle") == "test_ch_target#(8)"
    assert r.xtor_for("test_ch_bundle#(8)", "mirror") == "test_ch_initiator#(8)"
    assert r.xtor_for("test_ch_bundle#(8)", "monitor") == "test_ch_monitor#(8)"
    assert r.xtor_for("test_ch_bundle#(16)", "mirror") is None   # not specialized
    # std rv, through the channels' own transactors
    assert r.xtor_for("fw_rv_if#(logic[7:0])", "mirror") == "fw_rv_initiator#(logic[7:0])"
    assert r.xtor_for("fw_rv_if#(logic[7:0])", "monitor") == "fw_rv_monitor#(logic[7:0])"
