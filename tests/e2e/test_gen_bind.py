"""K10.3 ``gen_bind`` on examples/wrap_rtl's chain: ``gen_tb(..., bind=True)``
adds a monitor per DUT port to the testbench class; ``gen_bind`` emits the
module bound into the DUT that feeds them its nets (RTL -> class only).
The test attaches a listener to each monitor and checks they see the bytes
at the edges the transactors do; a Verilator lint checks the bound module
drives no DUT net."""
import re
import shutil
import subprocess

import pytest

from fw.hdl.fe.sv.emit import emit_sv
from fw.hdl.gen import gen_bind, gen_tb

from test_gen_tb import RT, RTL, SPEC, STD, _elab, _std_scan

#: test_gen_tb's test, plus a listener on each monitor recording the edge
#: of each handshake it sees.
TEST = """
`include "fw_hdl_macros.svh"
package chain_test_pkg;
    import fw_hdl_pkg::*;
    import fw_hdl_std_pkg::*;
    import chain_tb_pkg::*;

    class mon_l extends fw_rv_mon_listener #(bit [7:0]);
        string name;
        function new(string name); this.name = name; endfunction
        virtual function void put_post(input bit [7:0] t);
            $display("R mon %s %0d @%0d", name, t, $time / 10ns);
        endfunction
    endclass

    class test_c extends chain_tb_c implements fw_run_if;
        `fw_component_utils(test_c)
        virtual function void connect();
            mon_l l_in, l_out;
            super.connect();
            l_in = new("in");
            l_out = new("out");
            mon_in.add_listener(l_in);
            mon_out.add_listener(l_out);
        endfunction
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


def test_gen_bind_text():
    r, s = _elab(), _std_scan()
    sv = gen_bind(r, s, "chain")
    assert "module chain_fw_mon (" in sv
    assert "output" not in sv and "inout" not in sv
    assert 'fw_pin_bridge #(bit, "top.mon_in_pins.valid") b_in_valid(.i(in_valid), .o());' in sv
    assert "bind chain chain_fw_mon u_fw_mon (.in_valid(in_valid)," in sv
    tb = gen_tb(r, s, "chain", bind=True)
    assert "fw_rv_monitor#(bit[7:0]) mon_out;" in tb
    assert "mon_in.p.connect(mon_in_pins);" in tb
    assert "mon_" not in gen_tb(r, s, "chain")


def _files(tmp_path):
    r, s = _elab(), _std_scan()
    tb = tmp_path / "chain_tb.sv"
    tb.write_text(gen_tb(r, s, "chain", bind=True))
    bd = tmp_path / "chain_fw_mon.sv"
    bd.write_text(gen_bind(r, s, "chain"))
    ctxt, top = r.to_ir()
    res = emit_sv(ctxt, top, tmp_path / "gen", extern_files=RTL, top_module="chain")
    test = tmp_path / "test_top.sv"
    test.write_text(TEST)
    pre = [f"-I{RT}", f"-I{SPEC / 'bindings' / 'sv'}", f"-I{STD}",
           str(RT / "fw_hdl_pkg.sv"), str(STD / "fw_hdl_std_pkg.sv"), str(RT / "fw_pin_bridge.sv")]
    return pre + res.filelist.read_text().split() + [str(tb), str(bd), str(test)], bd


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
def test_gen_bind(tmp_path):
    files, _ = _files(tmp_path)
    obj = tmp_path / "obj"
    obj.mkdir()
    cmd = ["verilator", "--binary", "-Wno-fatal", "-Wno-lint", "-Wno-style",
           "--timescale", "1ns/1ns", "--top-module", "test_top", "-Mdir", str(obj), "-o", "Vt"] + files
    b = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp_path)
    (tmp_path / "build.log").write_text(b.stdout + b.stderr)
    assert b.returncode == 0, b.stdout[-3000:] + b.stderr[-3000:]
    run = subprocess.run([str(obj / "Vt"), "+fw_edges=30"], capture_output=True, text=True)
    out = [l for l in run.stdout.splitlines() if l.startswith(("R ", "D "))]
    assert not [l for l in out if l.startswith("D ")], out
    ev = [(m[1], int(m[2]), int(m[3])) for m in
          (re.match(r"R (put|got|mon in|mon out) (\d+) @(\d+)", l) for l in out) if m]
    by = {k: [(v, c) for kk, v, c in ev if kk == k] for k in ("put", "got", "mon in", "mon out")}
    assert [v for v, _ in by["put"]] == [17, 34, 51], out
    # the monitors see what the transactors do, at the same edges
    assert by["mon in"] == by["put"], out
    assert by["mon out"] == by["got"], out
    # as in test_gen_tb (same bench without the monitors): $time/10ns of
    # the n-th posedge (at 10n-5 ns) prints as n
    assert [c for _, c in by["put"]] == [5, 6, 7], out
    assert [c for _, c in by["got"]] == [7, 8, 9], out


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
def test_gen_bind_no_driver(tmp_path):
    """The bound module adds no driver on the DUT's nets: a -Wall lint of
    the whole bench reports no multiple driver and no warning of the bind
    file but the open bridge outputs."""
    files, bd = _files(tmp_path)
    obj = tmp_path / "lint"
    obj.mkdir()
    cmd = ["verilator", "--lint-only", "-Wall", "-Wno-fatal", "--timing",
           "--timescale", "1ns/1ns", "--top-module", "test_top", "-Mdir", str(obj)] + files
    b = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp_path)
    log = b.stdout + b.stderr
    (tmp_path / "lint.log").write_text(log)
    assert b.returncode == 0, log[-3000:]
    assert "MULTIDRIVEN" not in log, log[-3000:]
    warns = [l for l in log.splitlines() if l.startswith("%Warning") and bd.name in l
             and "PINCONNECTEMPTY" not in l]
    assert not warns, warns
