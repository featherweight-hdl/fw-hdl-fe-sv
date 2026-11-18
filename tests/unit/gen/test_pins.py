"""The ``pins`` generator end to end: an extern maps a bundle of channels
with the generated preset ``test_ch_pins::flat(m, a, "s_")``, and the
wrapper fe-sv emits connects one pin per valid, ready and payload field
(``.s_rspcode(...)``) and lints clean under ``verilator -Wall``."""
import shutil
import subprocess
from pathlib import Path

import pytest

from fw.hdl.fe.sv import elaborate
from fw.hdl.fe.sv.emit import emit_sv
from fw.hdl.fe.sv.scan import scan
from fw.hdl.gen import gen_kit, pins

PKGS = Path(__file__).resolve().parents[4]
LIB = PKGS / "fw-hdl-spec" / "conformance" / "_lib"
SRC = Path(__file__).parent / "src" / "proto_chan"

USER = """`include "fw_hdl_macros.svh"
package user;
    import fw_hdl_pkg::*;
    import scenario::*;
    import scenario_gen_pkg::*;

    class ext extends fw_component implements fw_extern_if;
        `fw_extern_utils(ext, "ch_sink")
        fw_input  #(bit)                   clk;
        fw_mirror #(test_ch_bundle #(8))   a;
        virtual function void build();
            clk = fw_input  #(bit)::create("clk", this);
            a   = fw_mirror #(test_ch_bundle #(8))::create("a", this);
        endfunction
        virtual function void extern_map(fw_extern_map m);
            void'(test_ch_pins::flat(m, a, "s_"));
        endfunction
    endclass

    class top extends fw_component;
        `fw_component_utils(top)
        fw_input  #(bit)                   clk;
        fw_mirror #(test_ch_bundle #(8))   a;
        ext                                e;
        virtual function void build();
            clk = fw_input  #(bit)::create("clk", this);
            a   = fw_mirror #(test_ch_bundle #(8))::create("a", this);
            e   = ext::create("e", this);
        endfunction
        virtual function void connect();
            e.clk.connect(clk);
            e.a.connect_up(a);
        endfunction
    endclass
endpackage
"""

RTL = """module ch_sink (
    input  logic       clk,
    input  logic       s_reqvalid,
    output logic       s_reqready,
    input  logic [7:0] s_reqdata,
    output logic       s_rspvalid,
    input  logic       s_rspready,
    output logic [1:0] s_rspcode,
    output logic [7:0] s_rsptag
);
endmodule
"""

PINS = ["s_reqvalid", "s_reqready", "s_reqdata", "s_rspvalid", "s_rspready",
        "s_rspcode", "s_rsptag"]


def _emit(tmp_path):
    r = scan([str(SRC / "top.sv")], incdirs=[str(SRC), str(LIB)])
    assert r.ok, r.diags
    gen = tmp_path / "scenario_gen_pkg.sv"
    gen.write_text(gen_kit(r, generators=[pins]))
    user, rtl = tmp_path / "user.sv", tmp_path / "rtl.sv"
    user.write_text(USER)
    rtl.write_text(RTL)
    e = elaborate([str(SRC / "top.sv"), str(gen), str(user)], top="user::top",
                  incdirs=[str(SRC), str(LIB)], rtl=[str(rtl)])
    assert e.ok, "\n".join(map(str, e.diags))
    ctxt, top = e.to_ir()
    assert e.ir_diags == []
    return emit_sv(ctxt, top, tmp_path / "out", extern_files=[str(rtl)],
                   top_module="pins_top")


def test_connections(tmp_path):
    text = (_emit(tmp_path).files[0].parent / "pins_top.sv").read_text()
    for p in PINS:
        assert f".{p}(" in text, text
    assert ".s_rspcode(a_rsp_data.code)" in text, text


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
def test_lint(tmp_path):
    res = _emit(tmp_path)
    files = res.filelist.read_text().split()
    lint = subprocess.run(["verilator", "--lint-only", "-Wall", "--top-module", "pins_top"]
                          + files, capture_output=True, text=True, cwd=tmp_path)
    # The RTL is an empty stub: only its own warnings are expected.
    gen = [ln for ln in lint.stderr.splitlines()
           if ln.startswith("%") and "rtl.sv" not in ln and not ln.startswith("%Error: Exiting")]
    assert gen == [], lint.stderr
