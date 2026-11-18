"""Proxy, listener and txn bridges run together in Verilator: a proxy
calls listeners' ``<m>_pre`` before and ``<m>_post`` after the target;
the txn listener records each call (outputs and ``__ret`` included) into
a sink; the txn driver replays the records through a second proxy, whose
recording matches the first (the conformance scenarios have no function
with a return value or task with an output)."""
import shutil
import subprocess

import pytest
import zuspec.ir.core as ir

from fw.hdl.gen import irb, listener, proxy, txn
from fw.hdl.gen.emit import to_sv
from fw.hdl.gen.kit import Kit, KitApi


def _kit():
    i32 = ir.DataTypeInt(bits=32, signed=True)
    proto = ir.DataTypeProtocol(name="p_if", methods=[
        irb.method("peek", [irb.arg("a", i32)], returns=i32),
        irb.method("xfer", [irb.arg("a", i32), irb.arg("b", i32, ir.SignalDirection.OUTPUT)],
                   task=True)])
    api = KitApi("p_if", [], proto, ir.DataTypeRef(ref_name="p_if"))
    kit = Kit("p_pkg", [api])
    return [proto] + listener.decls(api, kit) + proxy.decls(api, kit) + txn.decls(api, kit)


TB = """
module tb;
  import p_pkg::*;
  string log [$];

  // The target: peek(a) = a + 1; xfer(a, b) sets b = 2 * a after a delay.
  class target implements p_if;
    virtual function int peek(input int a);
      log.push_back($sformatf("peek %0d", a));
      return a + 1;
    endfunction
    virtual task xfer(input int a, output int b);
      #1;
      b = 2 * a;
      log.push_back($sformatf("xfer %0d", a));
    endtask
  endclass

  // Logs the order of each call's pre and post.
  class order extends p_if_listener_base;
    virtual function void peek_pre(input int a);
      log.push_back($sformatf("peek_pre %0d", a));
    endfunction
    virtual function void peek_post(input int a, input int __ret);
      log.push_back($sformatf("peek_post %0d %0d", a, __ret));
    endfunction
    virtual function void xfer_pre(input int a);
      log.push_back($sformatf("xfer_pre %0d", a));
    endfunction
    virtual function void xfer_post(input int a, input int b);
      log.push_back($sformatf("xfer_post %0d %0d", a, b));
    endfunction
  endclass

  class sink implements p_if_txn_sink;
    p_if_txn q [$];
    virtual function void put(input p_if_txn t);
      q.push_back(t);
    endfunction
  endclass

  int errors = 0;

  task automatic check(input bit ok, input string what);
    if (!ok) begin
      errors++;
      $display("FAIL: %s", what);
    end
  endtask

  initial begin
    target tgt;
    order ord;
    sink rec, rep;
    p_if_proxy p1, p2;
    p_if_txn_listener l1, l2;
    p_if_txn_driver drv;
    int r, b;
    string d;
    string exp [$];

    tgt = new;
    ord = new;
    rec = new;
    rep = new;
    p1 = new(tgt);
    l1 = new(rec);
    p1.add_listener(ord);
    p1.add_listener(l1);

    r = p1.peek(5);
    check(r == 6, "peek return");
    p1.xfer(7, b);
    check(b == 14, "xfer output");

    exp = '{"peek_pre 5", "peek 5", "peek_post 5 6",
            "xfer_pre 7", "xfer 7", "xfer_post 7 14"};
    check(log.size() == exp.size(), $sformatf("log size %0d", log.size()));
    foreach (exp[i]) check(i < log.size() && log[i] == exp[i],
                           $sformatf("log[%0d] %s != %s", i, log[i], exp[i]));

    check(rec.q.size() == 2, "recorded count");
    foreach (rec.q[i]) $display("rec %s", rec.q[i].sprint());

    // Replay with the outputs cleared: the driver fills them back in.
    p2 = new(tgt);
    l2 = new(rep);
    p2.add_listener(l2);
    drv = new(p2);
    foreach (rec.q[i]) begin
      p_if_peek_txn pk, pk2;
      p_if_xfer_txn xf, xf2;
      p_if_txn t;
      if ($cast(pk, rec.q[i])) begin
        pk2 = new;
        pk2.a = pk.a;
        t = pk2;
      end else begin
        void'($cast(xf, rec.q[i]));
        xf2 = new;
        xf2.a = xf.a;
        t = xf2;
      end
      drv.drive(t);
      check(t.compare(rec.q[i], d), $sformatf("driven[%0d]:%s", i, d));
    end
    check(rep.q.size() == rec.q.size(), "replayed count");
    foreach (rep.q[i]) check(i < rec.q.size() && rep.q[i].compare(rec.q[i], d),
                             $sformatf("replayed[%0d]:%s", i, d));
    if (errors == 0) $display("PASS");
    $finish;
  end
endmodule
"""


def test_txn_bridge(tmp_path):
    sv = to_sv("p_pkg", _kit())
    assert "__peek.__ret = m_api.peek(__peek.a);" in sv
    assert "m_api.xfer(__xfer.a, __xfer.b);" in sv

    if shutil.which("verilator") is None:
        pytest.skip("verilator not on PATH")
    f = tmp_path / "tb.sv"
    f.write_text(sv + TB)
    # Verilator reports every ``output T`` argument as UNDRIVEN, even in a
    # pure virtual prototype.
    res = subprocess.run(["verilator", "--binary", "--timing", "-Wno-fatal", "-Wall",
                          "-Wno-DECLFILENAME", "-Wno-UNUSEDSIGNAL", "-Wno-UNDRIVEN",
                          "--top-module", "tb",
                          str(f)],
                         capture_output=True, text=True, cwd=tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    warn = [l for l in res.stderr.splitlines() if l.startswith("%Warning")]
    assert not warn, res.stderr
    run = subprocess.run([str(tmp_path / "obj_dir" / "Vtb")],
                         capture_output=True, text=True, cwd=tmp_path)
    assert "PASS" in run.stdout, run.stdout + run.stderr
