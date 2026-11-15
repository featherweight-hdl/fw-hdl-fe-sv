"""Module parameters of every kind (fw_value of int, wide vector, string,
enum, packed struct; fw_type) emitted as SV the wrapped module accepts:
the spec's extern_params scenario, emitted and linted against its module."""
import shutil
import subprocess

import pytest

from fw.hdl.fe.sv import elaborate
from fw.hdl.fe.sv.api import spec_dir
from fw.hdl.fe.sv.emit import emit_sv


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
def test_emit_typed_params(tmp_path):
    d = spec_dir() / "conformance" / "extern_params"
    rtl = [str(d / "rtl.sv")]
    r = elaborate([str(d / "top.sv")], top="scenario::top", incdirs=[str(d)], rtl=rtl)
    assert r.ok, "\n".join(map(str, r.diags))
    ctxt, top = r.to_ir()
    res = emit_sv(ctxt, top, tmp_path / "gen", extern_files=rtl)
    text = (tmp_path / "gen" / f"{res.top_module}.sv").read_text()
    for frag in (".OFS(-6)", ".K(40'h123456789a)", '.NAME("u0")', ".MODE(prm_pkg::MODE_FAST)",
                 ".CFG(5'h9)", ".cfg_tt(prm_pkg::cfg_t)", ".data_t(bit [11:0])"):
        assert frag in text, text
    files = res.filelist.read_text().split()
    lint = subprocess.run(["verilator", "--lint-only", "-Wall", "-Wno-UNUSEDPARAM",
                           "-Wno-UNUSEDSIGNAL", "-Wno-DECLFILENAME", "-I" + str(d), "--top-module", res.top_module]
                          + files, capture_output=True, text=True, cwd=tmp_path)
    assert lint.returncode == 0, lint.stderr + "\n" + text
