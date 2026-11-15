"""End to end on examples/wrap_rtl (core-layer1.md M5): elaborate the fw-hdl
assembly of two wrapped RTL modules, emit Verilog, lint it, and simulate it
against the hand-wired reference."""
import shutil
import subprocess
from pathlib import Path

import pytest

from fw.hdl.fe.sv import elaborate
from fw.hdl.fe.sv.emit import emit_sv

EX = Path(__file__).resolve().parents[2] / "examples" / "wrap_rtl"
RTL = [str(EX / "rtl" / "fw_rv_fifo.sv"), str(EX / "rtl" / "axis_reg.sv")]

needs_verilator = pytest.mark.skipif(shutil.which("verilator") is None,
                                     reason="verilator not on PATH")


def _elab():
    r = elaborate([str(EX / "sv" / "wrap_pkg.sv")], top="wrap_pkg::chain#(bit[7:0])", rtl=RTL)
    assert r.ok, "\n".join(map(str, r.diags))
    return r


def test_elaborate_and_map_pins():
    r = _elab()
    fifo = r.top.children["fifo"]
    slice_ = r.top.children["slice"]
    assert fifo.extern.module == "fw_rv_fifo"
    from fw.hdl.fe.sv.params import netlist_entry
    assert {k: netlist_entry(a) for k, a in fifo.extern.params.items()} == {
        "WIDTH": {"type": "int", "value": "8"}, "DEPTH": {"type": "int", "value": "4"}}
    pins = {p: (w, getattr(v, "path", v)) for p, w, v, _ in fifo.extern.resolved}
    assert pins["in_vld"] == ("port", "top.fifo.in.valid")
    assert pins["out_rdy"] == ("port", "top.fifo.out.ready")
    assert pins["in_data"] == ("port", "top.fifo.in.data")
    spins = {p: (w, getattr(v, "path", v)) for p, w, v, _ in slice_.extern.resolved}
    assert spins["aresetn"] == ("port", "top.slice.rst_n")
    assert spins["s_axis_tvalid"] == ("port", "top.slice.s.valid")
    assert spins["s_axis_tlast"] == ("tie", 1)
    assert spins["m_axis_tlast"] == ("open", None)


@needs_verilator
def test_emit_lint_and_simulate(tmp_path):
    r = _elab()
    ctxt, top = r.to_ir()
    res = emit_sv(ctxt, top, tmp_path / "gen", extern_files=RTL, top_module="chain")
    files = res.filelist.read_text().split()
    assert [Path(f).name for f in files] == ["fw_rv_fifo.sv", "axis_reg.sv", "chain.sv"]
    # The open tlast pin: connected to a net named *_unused, no lint pragma.
    text = (tmp_path / "gen" / "chain.sv").read_text()
    assert "  logic slice_m_axis_tlast_unused;\n" in text
    assert "    .m_axis_tlast(slice_m_axis_tlast_unused)\n" in text and "lint_" not in text

    lint = subprocess.run(["verilator", "--lint-only", "-Wall", "--top-module", "chain"] + files,
                          capture_output=True, text=True, cwd=tmp_path)
    gen_warn = [l for l in lint.stderr.splitlines() if "chain.sv" in l]
    assert not gen_warn, lint.stderr

    sim = tmp_path / "sim"
    build = subprocess.run(
        ["verilator", "--binary", "-Wno-fatal", "--top-module", "chain_tb", "-Mdir", str(sim)]
        + files + [str(EX / "tb" / "chain_ref.sv"), str(EX / "tb" / "chain_tb.sv")],
        capture_output=True, text=True, cwd=tmp_path)
    (tmp_path / "build.log").write_text(build.stdout + build.stderr)
    assert build.returncode == 0, build.stderr[-3000:]
    run = subprocess.run([str(sim / "Vchain_tb")], capture_output=True, text=True, cwd=tmp_path)
    assert "PASS" in run.stdout, run.stdout[-3000:]
