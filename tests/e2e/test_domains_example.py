"""End to end on examples/domains (core-domains-plan §1, D6): two unrelated
clocks, a divided clock from a wrapped divider, a block that picks its FIFO
by how its clocks relate, and resets of both polarities. Elaborate, emit,
lint, simulate against the hand-wired reference; and the variant with no
crossing element fails elaboration with the crossing error."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from fw.hdl.fe.sv import elaborate
from fw.hdl.fe.sv.emit import emit_sv

EX = Path(__file__).resolve().parents[2] / "examples" / "domains"
RTL = [str(EX / "rtl" / f) for f in ("clk_div2.sv", "fw_rv_fifo.sv", "async_fifo.sv")]

needs_verilator = pytest.mark.skipif(shutil.which("verilator") is None,
                                     reason="verilator not on PATH")


def _elab(top="soc"):
    return elaborate([str(EX / "sv" / "domains_pkg.sv")], top=f"domains_pkg::{top}#(bit[7:0])",
                     rtl=RTL)


def test_elaborate():
    r = _elab()
    assert r.ok, "\n".join(map(str, r.diags))
    assert not r.diags
    top = r.top
    # x1 joins one clock to itself: a single-clock FIFO; x2 and x3 cross.
    assert "sf" in top.children["x1"].children
    assert "af" in top.children["x2"].children and "af" in top.children["x3"].children


def test_crossing_variant_fails():
    r = _elab("soc_bad")
    assert not r.ok
    errs = [(d.id, d.path) for d in r.diags if d.severity == "error"]
    assert errs == [("FW-E-DOM-CROSS", "top.out")], r.diags
    assert "from top.clk_a to top.clk_b" in [d for d in r.diags if d.severity == "error"][0].msg


@needs_verilator
def test_emit_lint_and_simulate(tmp_path):
    r = _elab()
    assert r.ok, "\n".join(map(str, r.diags))
    ctxt, top = r.to_ir()
    res = emit_sv(ctxt, top, tmp_path / "gen", extern_files=RTL, top_module="soc")
    files = res.filelist.read_text().split()
    gen = {Path(f).name: Path(f).read_text() for f in files if "/gen/" in f}
    # x2 and x3 are one class: ports named by association, not by instance.
    assert sorted(gen) == ["soc.sv", "xfer.sv", "xfer__1.sv"]
    assert "input logic rd_clk,\n  input logic wr_clk,\n  input logic rst_n," in \
        gen["xfer__1.sv"]
    soc = gen["soc.sv"]
    assert "  assign rst_inv = ~rst;\n" in soc
    assert ".clk_o(slow)" in soc

    lint = subprocess.run(["verilator", "--lint-only", "-Wall", "--top-module", "soc"] + files,
                          capture_output=True, text=True, cwd=tmp_path)
    # The generated modules lint clean (the existing RTL is not ours to fix).
    gen_warn = [l for l in lint.stderr.splitlines() if "/gen/" in l]
    assert not gen_warn, lint.stderr

    sim = tmp_path / "sim"
    build = subprocess.run(
        ["verilator", "--binary", "-Wno-fatal", "--top-module", "soc_tb", "-Mdir", str(sim)]
        + files + [str(EX / "tb" / "soc_ref.sv"), str(EX / "tb" / "soc_tb.sv")],
        capture_output=True, text=True, cwd=tmp_path)
    (tmp_path / "build.log").write_text(build.stdout + build.stderr)
    assert build.returncode == 0, build.stderr[-3000:]
    run = subprocess.run([str(sim / "Vsoc_tb")], capture_output=True, text=True, cwd=tmp_path)
    assert "PASS" in run.stdout, run.stdout[-3000:]


@pytest.mark.skipif(shutil.which("dfm") is None, reason="dfm not on PATH")
def test_domain_severity_through_dfm(tmp_path):
    """Elaborate's domain_severity parameter reaches the crossing check:
    soc_bad's unrelated clock crossing, made a warning, elaborates."""
    (tmp_path / "flow.yaml").write_text(f"""
package:
  name: sev
  tasks:
  - name: rtl
    uses: std.FileSet
    with: {{type: systemVerilogSource, base: "{EX / 'rtl'}",
            include: [clk_div2.sv, fw_rv_fifo.sv, async_fifo.sv]}}
  - name: design
    uses: std.FileSet
    with: {{type: fwHdlSource, base: "{EX / 'sv'}", include: [domains_pkg.sv]}}
  - root: elab
    uses: fw.hdl.fe.sv.Elaborate
    needs: [design, rtl]
    with:
      top: "domains_pkg::soc_bad#(bit[7:0])"
      domain_severity:
        fw_clock_kind: {{unrelated: warning}}
""")
    r = subprocess.run(["dfm", "run", "elab"], capture_output=True, text=True, cwd=tmp_path)
    (tmp_path / "dfm.log").write_text(r.stdout + r.stderr)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    nl = next((tmp_path / "rundir").rglob("netlist.json"))
    diags = [(d["id"], d["path"]) for d in json.loads(nl.read_text())["diagnostics"]]
    assert ("FW-W-DOM-CROSS", "top.out") in diags
