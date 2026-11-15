"""Parameterized emission end to end (core-layer1.md §6.6, M6).

``smoke_pkg::fifo_chain`` (fw-hdl-spec ``tests/sv/smoke``, the §4.8 worked
example) must emit as ONE parameterized module: its params fields and its
type parameter become module parameters, the FIFOs created in a loop over
``params.n`` a generate loop, the creator's depth computation a parameter
expression. The generated file must be lint-clean, and the module must
simulate correctly at several parameter values, against a hand-wired
reference (examples/param_chain)."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from fw.hdl.fe.sv import elaborate
from fw.hdl.fe.sv.api import spec_dir
from fw.hdl.fe.sv.elab import ElabError

ROOT = Path(__file__).resolve().parents[2]
EX = ROOT / "examples" / "param_chain"
RTL = [str(ROOT / "examples" / "wrap_rtl" / "rtl" / "fw_rv_fifo.sv"),
       str(EX / "rtl" / "fw_valid_check.sv")]

needs_verilator = pytest.mark.skipif(shutil.which("verilator") is None,
                                     reason="verilator not on PATH")


def _smoke() -> str:
    return str(spec_dir() / "tests" / "sv" / "smoke" / "smoke_pkg.sv")


def _elab(top="smoke_pkg::fifo_chain#(bit[7:0])"):
    r = elaborate([_smoke()], top=top, rtl=RTL)
    assert r.ok, "\n".join(map(str, r.diags))
    return r


def _emit(tmp_path, r, **kw):
    from fw.hdl.fe.sv.emit import emit_sv
    ctxt, top = r.to_ir(**kw)
    return emit_sv(ctxt, top, tmp_path / "gen", extern_files=RTL)


def test_one_parameterized_module(tmp_path):
    r = _elab()
    res = _emit(tmp_path, r)
    assert r.ir_diags == []                          # nothing had to be specialized
    assert [p.name for p in res.files] == ["fifo_chain.sv"]
    sv = res.files[0].read_text()
    # Module parameters: the type parameter and every params field; not fifo_h.
    # T's default is the class's, as written (two-state bit, §6.3).
    assert re.search(r"module fifo_chain #\(\s*parameter type T = bit \[7:0\],\s*"
                     r"parameter int n = 2,\s*parameter int depth = 2,\s*"
                     r"parameter bit deep_tail = 0\s*\)", sv), sv
    assert "fifo_h" not in sv
    # The FIFOs: a generate loop over n, each the extern's module with
    # parameter expressions.
    assert re.search(r"for \(genvar i = 0; i < n; i = i \+ 1\) begin : u\s+"
                     r"fw_rv_fifo #\(\.WIDTH\(\$bits\(T\)\), "
                     r"\.DEPTH\(\(deep_tail && \(i == \(n-1\)\)\) \? \(2\*depth\) : depth\)\) u \(",
                     sv), sv
    # Array nets and the links between neighbours in a loop of their own.
    assert "T u_in_data [n];" in sv
    assert "assign u_in_data[i+1] = u_out_data[i];" in sv
    assert "assign out_data = u_out_data[n-1];" in sv
    assert "fw_valid_check mon (" in sv


def test_specialization_rule(tmp_path):
    """soc overrides chain.u[0]: fifo_chain is emitted as a specialization,
    with an informational diagnostic, or an error under require_param."""
    r = _elab("smoke_pkg::soc")
    res = _emit(tmp_path, r)
    assert [d.id for d in r.ir_diags] == ["FW-I-EMIT-SPEC"]
    assert r.ir_diags[0].path == "top.chain" and "u[0]" in r.ir_diags[0].msg
    sv = (tmp_path / "gen" / "fifo_chain.sv").read_text()
    assert "genvar" not in sv and ".DEPTH(32)" in sv      # 16, deepened by the edit
    assert {p.name for p in res.files} == {"fifo_chain.sv", "soc.sv"}
    with pytest.raises(ElabError, match="FW-E-EMIT-SPEC"):
        _elab("smoke_pkg::soc").to_ir(require_param=True)


@needs_verilator
def test_lint(tmp_path):
    res = _emit(tmp_path, _elab())
    files = res.filelist.read_text().split()
    lint = subprocess.run(["verilator", "--lint-only", "-Wall", "--top-module", "fifo_chain"]
                          + files, capture_output=True, text=True, cwd=tmp_path)
    gen = [l for l in lint.stderr.splitlines() if "fifo_chain.sv" in l]
    assert not gen, lint.stderr


@needs_verilator
@pytest.mark.parametrize("n,w,deep", [(2, 8, 0), (3, 16, 1), (1, 8, 0)])
def test_simulate(tmp_path, n, w, deep):
    res = _emit(tmp_path, _elab())
    files = res.filelist.read_text().split()
    sim = tmp_path / "sim"
    build = subprocess.run(
        ["verilator", "--binary", "-Wno-fatal", "--top-module", "fifo_chain_tb",
         f"-GN={n}", f"-GW={w}", f"-GDEEP_TAIL={deep}", "-Mdir", str(sim)]
        + files + [str(EX / "tb" / "fifo_chain_ref.sv"), str(EX / "tb" / "fifo_chain_tb.sv")],
        capture_output=True, text=True, cwd=tmp_path)
    (tmp_path / "build.log").write_text(build.stdout + build.stderr)
    assert build.returncode == 0, build.stderr[-3000:]
    run = subprocess.run([str(sim / "Vfifo_chain_tb")], capture_output=True, text=True,
                         cwd=tmp_path)
    assert f"PASS: N={n} W={w} DEEP_TAIL={deep}" in run.stdout, run.stdout[-3000:]
