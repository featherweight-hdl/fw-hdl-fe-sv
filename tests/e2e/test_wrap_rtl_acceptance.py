"""K10.4 wrap_rtl acceptance (decision 15): random traffic with gaps and
back-pressure from class code through the generated testbench
(``gen_tb(..., bind=True)`` + ``gen_bind``) on the emitted chain, scored on
the bound monitors' listeners (out = in, in order); the clock edge of each
in/out handshake must equal that of the hand-written all-RTL reference
(examples/wrap_rtl/tb/chain_accept_tb.sv on chain_ref) driven with the same
sequence: the transactors add no cycle.

The sequence is one package, chain_seq_pkg, written from a seeded Python
random and compiled into both benches."""
import random
import re
import shutil
import subprocess

import pytest

from fw.hdl.fe.sv.emit import emit_sv
from fw.hdl.gen import gen_bind, gen_tb

from test_gen_tb import EX, RT, RTL, SPEC, STD, _elab, _std_scan

SEED = 10
N = 48

#: the class side: gaps before each put, a held inbox for back-pressure,
#: a scoreboard on the monitors' listeners.
TEST = """
`include "fw_hdl_macros.svh"
package chain_test_pkg;
    import fw_hdl_pkg::*;
    import fw_hdl_std_pkg::*;
    import chain_tb_pkg::*;
    import chain_seq_pkg::*;

    // Holds the target busy (ready low) out_hold[n] cycles after beat n;
    // c is the target, whose process calls put.
    class hold_inbox extends fw_inbox #(bit [7:0]);
        fw_component c;
        int n;
        function new(fw_component c); this.c = c; endfunction
        virtual task put(input bit [7:0] t);
            int k = out_hold[n++];
            super.put(t);
            if (k > 0) fw_clock_domain::tick(c, k);
        endtask
    endclass

    class sb_c;
        bit [7:0] q[$];
        int errors;
    endclass

    class mon_l extends fw_rv_mon_listener #(bit [7:0]);
        string name;
        sb_c sb;
        function new(string name, sb_c sb); this.name = name; this.sb = sb; endfunction
        virtual function void put_post(input bit [7:0] t);
            $display("A %s %0d @%0d", name, t, $time / 10ns);
            if (name == "in")
                sb.q.push_back(t);
            else if (sb.q.size() == 0 || sb.q.pop_front() != t) begin
                $display("E order %0d", t);
                sb.errors++;
            end
        endfunction
    endclass

    class test_c extends chain_tb_c implements fw_run_if;
        `fw_component_utils(test_c)
        hold_inbox h;
        sb_c sb = new();

        virtual function void build_xtors();
            super.build_xtors();
            h = new(dut_out);   // put runs in the target's process
            dut_out_rx = h;
            dut_out_e.set_imp(h);
        endfunction

        virtual function void connect();
            mon_l l_in, l_out;
            super.connect();
            l_in = new("in", sb);
            l_out = new("out", sb);
            mon_in.add_listener(l_in);
            mon_out.add_listener(l_out);
        endfunction

        virtual task run();
            fw_reset_domain::wait_reset_done(this);
            for (int i = 0; i < N; i++) begin
                if (in_gap[i] > 0) fw_clock_domain::tick(this, in_gap[i]);
                dut_in.put(data[i]);
            end
        endtask
    endclass
endpackage

module test_top;
    chain_tb #(.TB(chain_test_pkg::test_c)) tb();
endmodule
"""


def _seq():
    rnd = random.Random(SEED)
    gap = [rnd.choice([0, 0, 0, 1, 2]) for _ in range(N)]
    hold = [rnd.choice([0, 0, 1, 2, 3]) for _ in range(N)]
    data = [rnd.randrange(256) for _ in range(N)]
    arr = lambda xs: "'{" + ", ".join(str(x) for x in xs) + "}"
    sv = (f"package chain_seq_pkg;\n    localparam int N = {N};\n"
          f"    int in_gap[N] = {arr(gap)};\n    int out_hold[N] = {arr(hold)};\n"
          f"    bit [7:0] data[N] = {arr(data)};\nendpackage\n")
    return sv, gap, hold, data


def _run(tmp_path, name, files, top, args):
    obj = tmp_path / name
    obj.mkdir()
    cmd = ["verilator", "--binary", "-Wno-fatal", "-Wno-lint", "-Wno-style",
           "--timescale", "1ns/1ns", "--top-module", top, "-Mdir", str(obj), "-o", "Vt"] + files
    b = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp_path)
    (tmp_path / f"{name}.log").write_text(b.stdout + b.stderr)
    assert b.returncode == 0, b.stdout[-3000:] + b.stderr[-3000:]
    run = subprocess.run([str(obj / "Vt")] + args, capture_output=True, text=True)
    (tmp_path / f"{name}.out").write_text(run.stdout + run.stderr)
    lines = run.stdout.splitlines()
    assert not [l for l in lines if l.startswith(("D ", "E "))], lines
    return [(m[1], int(m[2]), int(m[3])) for m in
            (re.match(r"A (in|out) (\d+) @(\d+)", l) for l in lines) if m]


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
def test_wrap_rtl_acceptance(tmp_path):
    seq_sv, gap, hold, data = _seq()
    seq = tmp_path / "chain_seq.sv"
    seq.write_text(seq_sv)

    # the class bench: generated tb + bind on the emitted chain
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
    cls = _run(tmp_path, "cls", pre + res.filelist.read_text().split()
               + [str(seq), str(tb), str(bd), str(test)], "test_top", ["+fw_edges=200"])

    # the all-RTL reference: chain_ref driven by hand
    ref = _run(tmp_path, "ref", RTL + [str(EX / "tb" / "chain_ref.sv"), str(seq),
               str(EX / "tb" / "chain_accept_tb.sv")], "chain_accept_tb", ["+edges=200"])

    ins = [(v, c) for k, v, c in cls if k == "in"]
    outs = [(v, c) for k, v, c in cls if k == "out"]
    # scoreboard: every byte through, in order (the SV scoreboard prints
    # E lines on a mismatch; _run rejects them)
    assert [v for v, _ in ins] == data, cls
    assert [v for v, _ in outs] == data, cls
    # the DUT pushed back at least once: a put offered back to back that
    # was not taken on the next edge
    assert any(gap[i] == 0 and ins[i][1] > ins[i - 1][1] + 1 for i in range(1, N)), ins
    # same accept trace as the all-RTL reference: no added latency
    key = lambda e: (e[2], e[0])
    cls, ref = sorted(cls, key=key), sorted(ref, key=key)
    assert cls == ref, "\n".join(f"{a} | {b}" for a, b in zip(cls, ref))
