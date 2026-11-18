"""K10.2 ``gen_tb`` on examples/wrap_rtl's chain: elaborate, emit the RTL,
generate the testbench (class with one opposite-role std rv transactor per
DUT port, module with the DUT and the pin bridges), build it in Verilator
with rt-sv and std, and drive bytes through the generated initiator into
the RTL and out of the generated target.

The user-written part is a subclass whose run() puts and gets, and a
one-line top that hands it to the generated module."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from fw.hdl.fe.sv import elaborate
from fw.hdl.fe.sv.emit import emit_sv
from fw.hdl.fe.sv.scan import scan
from fw.hdl.gen.tb import gen_tb

PKGS = Path(__file__).resolve().parents[3]
SPEC = PKGS / "fw-hdl-spec"
STD = SPEC / "std"
RT = PKGS / "fw-hdl-rt-sv" / "sv"
EX = Path(__file__).resolve().parents[2] / "examples" / "wrap_rtl"
RTL = [str(EX / "rtl" / "fw_rv_fifo.sv"), str(EX / "rtl" / "axis_reg.sv")]

#: the test: put three bytes once reset is done; record the clock edge
#: (10ns period) at which each put returns (its handshake) and at which each
#: byte reaches the inbox.
TEST = """
`include "fw_hdl_macros.svh"
package chain_test_pkg;
    import fw_hdl_pkg::*;
    import fw_hdl_std_pkg::*;
    import chain_tb_pkg::*;

    class test_c extends chain_tb_c implements fw_run_if;
        `fw_component_utils(test_c)
        virtual task run();
            fw_reset_domain::wait_reset_done(this);
            fork
                for (int i = 1; i <= 3; i++) begin
                    dut_in.put(8'(i * 17));
                    $display("R put %0d @%0d", i * 17, $time / 10ns);
                end
                repeat (3) begin
                    bit [7:0] d;
                    dut_out_rx.get(d);
                    $display("R got %0d @%0d", d, $time / 10ns);
                end
            join
        endtask
    endclass
endpackage

module test_top;
    chain_tb #(.TB(chain_test_pkg::test_c)) tb();
endmodule
"""


def _elab():
    r = elaborate([str(EX / "sv" / "wrap_pkg.sv")], top="wrap_pkg::chain#(bit[7:0])", rtl=RTL)
    assert r.ok, "\n".join(map(str, r.diags))
    return r


def _std_scan():
    s = scan([str(STD / "fw_hdl_std_pkg.sv")], incdirs=[str(STD), str(SPEC / "bindings" / "sv")])
    assert s.ok, s.diags
    return s


def test_gen_tb_text():
    sv = gen_tb(_elab(), _std_scan(), "chain")
    assert "class chain_tb_c extends fw_component;" in sv
    assert "fw_rv_initiator#(bit[7:0]) dut_in;" in sv      # DUT target port (mirror)
    assert "fw_rv_target#(bit[7:0]) dut_out;" in sv        # DUT initiator port (bundle)
    assert "module chain_tb #(type TB = chain_tb_pkg::chain_tb_c);" in sv
    assert 'fw_pin_bridge #(bit, "top.in.valid") b_in_valid(.i(\'0), .o(in_valid));' in sv
    assert 'fw_pin_bridge #(bit, "top.out.valid") b_out_valid(.i(out_valid), .o());' in sv
    assert ".clk_i(clk)" in sv and ".rst_i(rst)" in sv


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
def test_gen_tb(tmp_path):
    r = _elab()
    tb = tmp_path / "chain_tb.sv"
    tb.write_text(gen_tb(r, _std_scan(), "chain"))
    ctxt, top = r.to_ir()
    res = emit_sv(ctxt, top, tmp_path / "gen", extern_files=RTL, top_module="chain")
    test = tmp_path / "test_top.sv"
    test.write_text(TEST)
    obj = tmp_path / "obj"
    obj.mkdir()
    cmd = ["verilator", "--binary", "-Wno-fatal", "-Wno-lint", "-Wno-style",
           "--timescale", "1ns/1ns", "--top-module", "test_top", "-Mdir", str(obj), "-o", "Vt",
           f"-I{RT}", f"-I{SPEC / 'bindings' / 'sv'}", f"-I{STD}",
           str(RT / "fw_hdl_pkg.sv"), str(STD / "fw_hdl_std_pkg.sv"), str(RT / "fw_pin_bridge.sv")]
    cmd += res.filelist.read_text().split() + [str(tb), str(test)]
    b = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp_path)
    (tmp_path / "build.log").write_text(b.stdout + b.stderr)
    assert b.returncode == 0, b.stdout[-3000:] + b.stderr[-3000:]
    run = subprocess.run([str(obj / "Vt"), "+fw_edges=30"], capture_output=True, text=True)
    out = [l for l in run.stdout.splitlines() if l.startswith(("R ", "D "))]
    assert not [l for l in out if l.startswith("D ")], out
    ev = [(m[1], int(m[2]), int(m[3])) for m in
          (re.match(r"R (put|got) (\d+) @(\d+)", l) for l in out) if m]
    puts = [(v, c) for k, v, c in ev if k == "put"]
    gots = [(v, c) for k, v, c in ev if k == "got"]
    assert [v for v, _ in puts] == [v for v, _ in gots] == [17, 34, 51], out
    # back to back: one handshake per cycle into the FIFO
    assert [c - puts[0][1] for _, c in puts] == [0, 1, 2], out
    # The RTL's latency: a byte taken at edge E is in the FIFO after E
    # (out_vld), the axis_reg samples it at E+1 (m_axis_tvalid) and the
    # target takes it at E+2 -- as in chain_ref, where the stream through
    # fifo and slice is the same pair of modules.
    assert [g - p for (_, p), (_, g) in zip(puts, gots)] == [2, 2, 2], out
