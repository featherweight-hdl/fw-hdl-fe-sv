"""Field-wise extern pins (fw-hdl-spec extern.md rule 2 "Fields"), end to
end on the conformance scenarios extern_map_fields{,_dflt}: each field pin
is connected to its field of the payload net (``.awaddr(aw_data.addr)``),
and the generated wrapper lints clean under ``verilator -Wall``."""
import shutil
import subprocess

import pytest

from fw.hdl.fe.sv import elaborate
from fw.hdl.fe.sv.api import spec_dir
from fw.hdl.fe.sv.emit import emit_sv

needs_verilator = pytest.mark.skipif(shutil.which("verilator") is None,
                                     reason="verilator not on PATH")

CONNS = {
    "extern_map_fields": [".awaddr(aw_data.addr)", ".awlen(aw_data.len)",
                          ".awsize(aw_data.size)", ".ardata(ar_data)"],
    "extern_map_fields_dflt": [".r_addr(r_data.addr)", ".r_tid(r_data.tag.id)",
                               ".r_data_tag_user(r_data.tag.user)",
                               ".r_data_len(r_data.len)"],
}


def _emit(tmp_path, name):
    sc = spec_dir() / "conformance" / name
    rtl = [str(sc / "rtl.sv")]
    r = elaborate([str(sc / "top.sv")], top="scenario::top", rtl=rtl)
    assert r.ok, "\n".join(map(str, r.diags))
    ctxt, top = r.to_ir()
    assert r.ir_diags == []
    return emit_sv(ctxt, top, tmp_path / "gen", extern_files=rtl, top_module="fld_top")


@pytest.mark.parametrize("name", sorted(CONNS))
def test_connections(tmp_path, name):
    text = (_emit(tmp_path, name).files[0].parent / "fld_top.sv").read_text()
    for c in CONNS[name]:
        assert c in text, text


@needs_verilator
@pytest.mark.parametrize("name", sorted(CONNS))
def test_lint(tmp_path, name):
    res = _emit(tmp_path, name)
    files = res.filelist.read_text().split()
    lint = subprocess.run(["verilator", "--lint-only", "-Wall", "--top-module", "fld_top"]
                          + files, capture_output=True, text=True, cwd=tmp_path)
    # The scenario's module is an empty stub: only its own warnings are
    # expected.
    gen = [ln for ln in lint.stderr.splitlines()
           if ln.startswith("%") and "rtl.sv" not in ln and not ln.startswith("%Error: Exiting")]
    assert gen == [], lint.stderr
