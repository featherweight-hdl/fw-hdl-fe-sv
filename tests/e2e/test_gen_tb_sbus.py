"""F10: ``gen_tb`` (with monitors) and ``gen_bind`` on a port that is a
bundle of channels: fw-hdl-spec's sbus example (docs/examples/sbus), whose
port ``s`` is ``fw_mirror #(sbus_bundle)``, a protocol bundle holding a
req rv channel and a mirrored rsp one. The generated bench uses sbus's own
initiator and monitor (the outermost protocol level), not rv transactors
per channel. The test (tb/sbus_rtl_tb.sv, the guide's) is a subclass of the
generated class; it runs against sbus_mem_rtl in Verilator and passes,
and fails with a bug planted in the RTL."""
import shutil
import subprocess
from pathlib import Path

import pytest

from fw.hdl.fe.sv import elaborate
from fw.hdl.fe.sv.emit import emit_sv
from fw.hdl.fe.sv.scan import scan
from fw.hdl.gen import gen_bind, gen_kit, gen_tb

PKGS = Path(__file__).resolve().parents[3]
SPEC = PKGS / "fw-hdl-spec"
STD = SPEC / "std"
BIND = SPEC / "bindings" / "sv"
RT = PKGS / "fw-hdl-rt-sv" / "sv"
EX = SPEC / "docs" / "examples" / "sbus"
INC = [str(STD), str(BIND)]
KIT = str(EX / "src" / "sbus_pkg.sv")


def _gen(out: Path, rtl: Path):
    """GenKit, then the design elaborated with ``rtl``; returns
    ``(r, scan of kit + generated package, generated package path)``."""
    s = scan([KIT], incdirs=INC)
    assert s.ok, s.diags
    gen = out / "sbus_pkg_gen_pkg.sv"
    gen.write_text(gen_kit(s))
    r = elaborate([KIT, str(gen), str(EX / "src" / "sbus_wrap_pkg.sv")],
                  top="sbus_wrap_pkg::sbus_sys", incdirs=INC, rtl=[str(rtl)])
    assert r.ok, "\n".join(map(str, r.diags))
    xs = scan([KIT, str(gen)], incdirs=INC)
    assert xs.ok, xs.diags
    return r, xs, gen


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    out = tmp_path_factory.mktemp("sbus_gen")
    return _gen(out, EX / "rtl" / "sbus_mem_rtl.sv")


def test_sbus_tb_text(made):
    r, xs, _ = made
    tb = gen_tb(r, xs, "sbus_sys", bind=True)
    # the kit's packages: the bundle's and the generated one (the xtors')
    assert "    import sbus_pkg::*;\n    import sbus_pkg_gen_pkg::*;" in tb
    # the outermost protocol level: sbus's initiator and monitor, no rv xtor
    assert "fw_bundle #(sbus_bundle) s;" in tb and "sbus_initiator dut_s;" in tb
    assert "sbus_monitor mon_s;" in tb and "fw_rv_" not in tb
    # each xtor's pin member by its declaration (sbus's monitor: pins)
    assert "dut_s.pins.connect_up(s);" in tb
    assert "mon_s.pins.connect(mon_s_pins);" in tb
    # req: the tb drives valid and data; rsp (mirrored): the DUT does
    assert "b_s_req_valid(.i('0), .o(s_req_valid));" in tb
    assert ('fw_pin_bridge #(sbus_pkg::sbus_req_t, "top.s.req.data") '
            "b_s_req_data(.i('0), .o(s_req_data));") in tb
    assert "b_s_rsp_valid(.i(s_rsp_valid), .o());" in tb
    assert "b_s_rsp_ready(.i('0), .o(s_rsp_ready));" in tb
    assert ('fw_pin_bridge #(sbus_pkg::sbus_rsp_t, "top.s.rsp.data") '
            "b_s_rsp_data(.i(s_rsp_data), .o());") in tb
    bd = gen_bind(r, xs, "sbus_sys")
    assert ('fw_pin_bridge #(sbus_pkg::sbus_rsp_t, "top.mon_s_pins.rsp.data") '
            "b_s_rsp_data(.i(s_rsp_data), .o());") in bd
    assert "output" not in bd


def _run(out: Path, r, xs, gen: Path, rtl: Path) -> str:
    ctxt, top = r.to_ir()
    res = emit_sv(ctxt, top, out / "emit", extern_files=[str(rtl)], top_module="sbus_sys")
    tb, bd = out / "sbus_sys_tb.sv", out / "sbus_sys_fw_mon.sv"
    tb.write_text(gen_tb(r, xs, "sbus_sys", bind=True))
    bd.write_text(gen_bind(r, xs, "sbus_sys"))
    obj = out / "obj"
    obj.mkdir()
    cmd = ["verilator", "--binary", "-Wno-fatal", "-Wno-lint", "-Wno-style",
           "--timescale", "1ns/1ns", "--top-module", "sbus_rtl_tb", "-Mdir", str(obj), "-o", "Vt",
           f"-I{RT}", f"-I{BIND}", f"-I{STD}",
           str(RT / "fw_hdl_pkg.sv"), str(STD / "fw_hdl_std_pkg.sv"), str(RT / "fw_pin_bridge.sv"),
           KIT, str(gen), str(EX / "src" / "sbus_behav_pkg.sv")]
    cmd += res.filelist.read_text().split() + [str(tb), str(bd), str(EX / "tb" / "sbus_rtl_tb.sv")]
    b = subprocess.run(cmd, capture_output=True, text=True, cwd=out)
    (out / "build.log").write_text(b.stdout + b.stderr)
    assert b.returncode == 0, b.stdout[-3000:] + b.stderr[-3000:]
    run = subprocess.run([str(obj / "Vt"), "+fw_edges=60"], capture_output=True, text=True)
    (out / "run.log").write_text(run.stdout + run.stderr)
    return run.stdout


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
def test_sbus_tb(made, tmp_path):
    r, xs, gen = made
    out = _run(tmp_path, r, xs, gen, EX / "rtl" / "sbus_mem_rtl.sv")
    lines = out.splitlines()
    assert not [l for l in lines if l.startswith(("D ", "E "))], out[-2000:]
    # the bound monitor saw each beat on both channels
    assert "M req 3 rsp 3" in lines and "PASS" in lines, out[-2000:]


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
def test_sbus_tb_bug(tmp_path):
    """The same bench against sbus_mem_rtl with its write response
    inverted: the test reports the mismatch and FAIL."""
    src = (EX / "rtl" / "sbus_mem_rtl.sv").read_text()
    bad = src.replace("s_rsprdata     <= s_reqwdata;", "s_rsprdata     <= ~s_reqwdata;")
    assert bad != src
    rtl = tmp_path / "sbus_mem_rtl.sv"
    rtl.write_text(bad)
    r, xs, gen = _gen(tmp_path, rtl)
    lines = _run(tmp_path, r, xs, gen, rtl).splitlines()
    assert any(l.startswith("E mismatch:") and "rdata" in l for l in lines), lines
    assert "FAIL" in lines and "PASS" not in lines, lines
