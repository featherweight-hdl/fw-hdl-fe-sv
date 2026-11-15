"""Packed struct and enum port types, open pins and 2/4-state
(core-layer1.md §6.3, §6.5; M8(b)), end to end on examples/packed_types.

fw-hdl's own types (pri_e, tag_t) are declared once in the generated
``<top>_types_pkg``; the RTL's (pt_rtl_pkg::cmd_t, op_e) are referred to by
their own names, so the net on the extern's cmd pin has the pin's type.
The extern's open debug pin, whose width follows a parameter, is connected
to an ``_unused`` net of that width. The result lints clean under
``verilator -Wall`` with no waiver and simulates against a model."""
import shutil
import subprocess
from pathlib import Path

import pytest

from fw.hdl.fe.sv import elaborate
from fw.hdl.fe.sv.emit import emit_sv

EX = Path(__file__).resolve().parents[2] / "examples" / "packed_types"
PKG = str(EX / "rtl" / "pt_rtl_pkg.sv")
RTL = [PKG, str(EX / "rtl" / "pt_acc.sv")]

needs_verilator = pytest.mark.skipif(shutil.which("verilator") is None,
                                     reason="verilator not on PATH")


def _emit(tmp_path, rtl=RTL):
    r = elaborate([PKG, str(EX / "sv" / "pt_pkg.sv")], top="pt_pkg::pt_top#(8)", rtl=rtl)
    assert r.ok, "\n".join(map(str, r.diags))
    ctxt, top = r.to_ir()
    assert r.ir_diags == []                         # every class one module
    return ctxt, emit_sv(ctxt, top, tmp_path / "gen", extern_files=rtl, top_module="pt_top")


def test_ir_types(tmp_path):
    import zuspec.ir.core as ir
    r = elaborate([PKG, str(EX / "sv" / "pt_pkg.sv")], top="pt_pkg::pt_top#(8)", rtl=RTL)
    ctxt, _top = r.to_ir()
    tm = ctxt.type_m
    tag, pri = tm["pt_pkg::tag_t"], tm["pt_pkg::pri_e"]
    assert isinstance(tag, ir.DataTypeStruct) and tag.packed and tag.extern_name is None
    assert [f.name for f in tag.fields] == ["pri", "op", "id"]
    assert tag.fields[1].datatype.ref_name == "pt_rtl_pkg::op_e"
    assert tag.fields[2].datatype.four_state is False            # bit [3:0]
    assert isinstance(pri, ir.DataTypeEnum) and pri.items == {"PRI_LO": 0, "PRI_MID": 1,
                                                              "PRI_HI": 2}
    assert pri.base_type.bits == 2 and pri.base_type.four_state is False
    # Declared by the extern RTL as well: the RTL's type.
    assert tm["pt_rtl_pkg::cmd_t"].extern_name == "pt_rtl_pkg::cmd_t"
    assert tm["pt_rtl_pkg::op_e"].extern_name == "pt_rtl_pkg::op_e"
    # The open pin carries its width, over the extern's parameter.
    (nops,) = [p for p in tm["acc"].pins if p.open]
    assert nops.name == "nops" and nops.datatype.width_expr is not None


def test_emit_text(tmp_path):
    _ctxt, res = _emit(tmp_path)
    gen = tmp_path / "gen"
    assert [Path(f).name for f in res.filelist.read_text().split()] == [
        "pt_rtl_pkg.sv", "pt_acc.sv", "pt_top_types_pkg.sv", "pass.sv", "pt_top.sv"]
    pkg = (gen / "pt_top_types_pkg.sv").read_text()
    assert "  typedef enum bit [1:0] {\n    PRI_LO = 2'd0,\n    PRI_MID = 2'd1,\n" \
           "    PRI_HI = 2'd2\n  } pri_e;\n" in pkg
    assert "  typedef struct packed {\n    pri_e pri;\n    pt_rtl_pkg::op_e op;\n" \
           "    bit [3:0] id;\n  } tag_t;\n" in pkg
    assert "cmd_t" not in pkg and "op_e {" not in pkg     # the RTL's: not re-declared
    top = (gen / "pt_top.sv").read_text()
    for frag in ("  input pt_rtl_pkg::cmd_t cmd,\n", "  output pt_rtl_pkg::cmd_t cmd_out,\n",
                 "  input pt_top_types_pkg::tag_t tag_in,\n",
                 "  output pt_top_types_pkg::pri_e pri_out\n",
                 "  pass #(.T(pt_top_types_pkg::tag_t)) tp (\n",
                 "  pass #(.T(pt_rtl_pkg::cmd_t)) cp (\n",
                 "  logic [($clog2(W)+1)-1:0] a_nops_unused;\n",
                 "    .nops(a_nops_unused)\n"):
        assert frag in top, top
    assert "lint_" not in top and "()" not in top
    assert "  parameter type T = bit [7:0]\n" in (gen / "pass.sv").read_text()


@needs_verilator
def test_lint(tmp_path):
    _ctxt, res = _emit(tmp_path)
    files = res.filelist.read_text().split()
    lint = subprocess.run(["verilator", "--lint-only", "-Wall", "--top-module", "pt_top"]
                          + files, capture_output=True, text=True, cwd=tmp_path)
    assert lint.returncode == 0 and "%Warning" not in lint.stderr, lint.stderr


@needs_verilator
@pytest.mark.parametrize("w", [8, 12, 5])
def test_simulate(tmp_path, w):
    _ctxt, res = _emit(tmp_path)
    files = res.filelist.read_text().split()
    sim = tmp_path / "sim"
    build = subprocess.run(
        ["verilator", "--binary", "-Wno-fatal", "--top-module", "pt_tb", f"-GW={w}",
         "-Mdir", str(sim)] + files + [str(EX / "tb" / "pt_tb.sv")],
        capture_output=True, text=True, cwd=tmp_path)
    (tmp_path / "build.log").write_text(build.stdout + build.stderr)
    assert build.returncode == 0, build.stderr[-3000:]
    run = subprocess.run([str(sim / "Vpt_tb")], capture_output=True, text=True, cwd=tmp_path)
    assert f"PASS: W={w}" in run.stdout, run.stdout[-3000:]


def test_rtl_declares_type_differently(tmp_path):
    """A type of the RTL's package that the RTL declares differently from
    the design's view of it is an error, not a silent copy."""
    bad = tmp_path / "pt_rtl_pkg.sv"
    bad.write_text(Path(PKG).read_text().replace("OP_ADD, OP_SUB", "OP_SUB, OP_ADD"))
    r = elaborate([PKG, str(EX / "sv" / "pt_pkg.sv")], top="pt_pkg::pt_top#(8)",
                  rtl=[str(bad), RTL[1]])
    from fw.hdl.fe.sv.rt import ElabError
    with pytest.raises(ElabError, match="FW-E-EXT-TYPE.*pt_rtl_pkg::op_e"):
        r.to_ir()


def test_without_rtl_types_are_generated(tmp_path):
    """With no extern RTL to declare them, the RTL package's types are
    declared in the generated package like any other."""
    r = elaborate([PKG, str(EX / "sv" / "pt_pkg.sv")], top="pt_pkg::pt_top#(8)")
    assert r.ok, "\n".join(map(str, r.diags))
    ctxt, top = r.to_ir()
    res = emit_sv(ctxt, top, tmp_path / "gen", top_module="pt_top")
    pkg = (tmp_path / "gen" / "pt_top_types_pkg.sv").read_text()
    assert "  } cmd_t;\n" in pkg and "  } op_e;\n" in pkg
    # op_e before the structs that hold it.
    assert pkg.index("} op_e;") < pkg.index("} cmd_t;") and pkg.index("} op_e;") < \
        pkg.index("} tag_t;")
    assert "pt_rtl_pkg" not in (tmp_path / "gen" / "pt_top.sv").read_text()
    assert len(res.files) == 3
