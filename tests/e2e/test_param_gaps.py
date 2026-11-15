"""Parameterized emission beyond fifo_chain (core-layer1.md §6.6, M8(b)).

The designs are in examples/param_gaps/sv/gaps_pkg.sv. Each must emit as
ONE parameterized module (no FW-I-EMIT-SPEC), lint clean with
``verilator -Wall``, and simulate like a hand-wired reference
(examples/param_gaps/tb/gaps_ref.sv) at several parameter values:

- value class parameters (``wpipe #(int W)``) and struct-valued ones, shapes
  (``spipe #(shape_t S)``, ``S.data_w`` -> module parameter ``S_data_w``),
  including a bundle sized by a shape and a child given ``S.data_w``; a
  child's params default computed from its class parameter (``dpipe``);
- ``if`` on parameters as a generate ``if``, both arms, with children and
  connections (``cond_pipe``: the arm not taken at the elaborated values
  is explored; ``pick_pipe``: one child name, a different class in each
  arm, connected after the branch);
- port and bundle arrays made in a loop over parameters, connected per
  index to an instance array (``lanes``) and to a child's bundle arrays
  (``lanes_top``).

Outside the structural subset (``by_case``: a case on a parameter) the
component is still a specialization, with the diagnostic naming why.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from fw.hdl.fe.sv import elaborate
from fw.hdl.fe.sv.elab import ElabError

ROOT = Path(__file__).resolve().parents[2]
EX = ROOT / "examples" / "param_gaps"
PKG = str(EX / "sv" / "gaps_pkg.sv")
RTL = [str(ROOT / "examples" / "wrap_rtl" / "rtl" / "fw_rv_fifo.sv"),
       str(ROOT / "examples" / "param_chain" / "rtl" / "fw_valid_check.sv"),
       str(EX / "rtl" / "rv_wire.sv")]
TB = [str(EX / "tb" / "gaps_ref.sv"), str(EX / "tb" / "rv_lane_check.sv")]

TOPS = {
    "wpipe": "gaps_pkg::wpipe#(8)",
    "spipe": "gaps_pkg::spipe",
    "dpipe": "gaps_pkg::dpipe#(8)",
    "cond_pipe": "gaps_pkg::cond_pipe#(8)",
    "pick_pipe": "gaps_pkg::pick_pipe#(8)",
    "lanes": "gaps_pkg::lanes#(8)",
    "lanes_top": "gaps_pkg::lanes_top#(8)",
}

needs_verilator = pytest.mark.skipif(shutil.which("verilator") is None,
                                     reason="verilator not on PATH")


def _elab(top: str):
    r = elaborate([PKG], top=top, rtl=RTL)
    assert r.ok, "\n".join(map(str, r.diags))
    return r


def _emit(tmp_path, name: str, **kw):
    from fw.hdl.fe.sv.emit import emit_sv
    r = _elab(TOPS.get(name, name))
    ctxt, top = r.to_ir(**kw)
    res = emit_sv(ctxt, top, tmp_path / "gen", extern_files=RTL)
    return r, res


def _text(res, name: str) -> str:
    return next(p for p in res.files if p.name == f"{name}.sv").read_text()


# ----------------------------------------------------------- one module each

def test_value_class_parameter(tmp_path):
    r, res = _emit(tmp_path, "wpipe")
    assert r.ir_diags == []
    assert [p.name for p in res.files] == ["wpipe.sv"]
    sv = _text(res, "wpipe")
    assert re.search(r"module wpipe #\(\s*parameter int W = 8,\s*parameter int depth = 2\s*\)",
                     sv), sv
    assert "input logic [W-1:0] in_data" in sv and "output logic [W-1:0] out_data" in sv
    assert "fw_rv_fifo #(.WIDTH(W), .DEPTH(depth)) a (" in sv
    assert "fw_rv_fifo #(.WIDTH(W), .DEPTH(depth+1)) b (" in sv


def test_shape_class_parameter(tmp_path):
    r, res = _emit(tmp_path, "spipe")
    assert r.ir_diags == []
    assert [p.name for p in res.files] == ["spipe.sv"]
    sv = _text(res, "spipe")
    assert re.search(r"module spipe #\(\s*parameter int S_data_w = 8,\s*"
                     r"parameter int S_depth = 2\s*\)", sv), sv
    # The shaped bundle's data, and the fw_rv_if over S.data_w.
    assert "input logic [S_data_w-1:0] in_data" in sv
    assert "output logic [S_data_w-1:0] out_data" in sv
    # sfifo #(S) and wfifo #(S.data_w): both through the shape's fields.
    assert "fw_rv_fifo #(.WIDTH(S_data_w), .DEPTH(S_depth)) u (" in sv
    assert "fw_rv_fifo #(.WIDTH(S_data_w), .DEPTH(S_depth)) v (" in sv


def test_params_default_from_class_parameter(tmp_path):
    """wdfifo's mk_params defaults depth to W / 2: read in a static call that
    names wdfifo #(W) (create()'s default argument, or mk_params() named),
    it is our W / 2, not its value at the elaborated W."""
    r, res = _emit(tmp_path, "dpipe")
    assert r.ir_diags == []
    sv = _text(res, "dpipe")
    assert "fw_rv_fifo #(.WIDTH(W), .DEPTH(W/2)) a (" in sv
    assert "fw_rv_fifo #(.WIDTH(W), .DEPTH(W/2)) b (" in sv


def test_shape_ir(tmp_path):
    """The shape is flattened in the IR: the bundle type is generic over
    S_data_w, its field in spipe passes spipe's S_data_w."""
    import zuspec.ir.core as ir
    r = _elab(TOPS["spipe"])
    ctxt, top = r.to_ir()
    sp = ctxt.type_m[top]
    assert [tp.name for tp in sp.template_params] == ["S_data_w", "S_depth"]
    b = ctxt.type_m["sh_rv_if"]
    assert [tp.name for tp in b.template_params] == ["S_data_w", "S_depth"]
    (data,) = [f for f in b.fields if f.name == "data"]
    assert data.datatype.width_expr.name == "S_data_w"
    (inp,) = [f for f in sp.fields if f.name == "in"]
    assert inp.inst_params["S_data_w"].name == "S_data_w"
    (v,) = [f for f in sp.fields if f.name == "v"]
    assert isinstance(v.inst_params["W"], ir.ExprRefParam) and v.inst_params["W"].name == "S_data_w"


def test_generate_if(tmp_path):
    r, res = _emit(tmp_path, "cond_pipe")
    assert r.ir_diags == []
    assert [p.name for p in res.files] == ["cond_pipe.sv"]
    sv = _text(res, "cond_pipe")
    assert re.search(r"module cond_pipe #\(\s*parameter int W = 8,\s*parameter bit use_fifo = 1,"
                     r"\s*parameter int depth = 2,\s*parameter bit check = 1\s*\)", sv), sv
    # Both arms of use_fifo in one block: the build() and connect() ifs
    # on it merged; the arm not taken (the wire) explored.
    m = re.search(r"if \(use_fifo\) begin : (g_\d+)(.*?)end else begin : \1_else(.*?)\n  end\n",
                  sv, re.S)
    assert m, sv
    fifo, wire = m.group(2), m.group(3)
    assert "fw_rv_fifo #(.WIDTH(W), .DEPTH(depth)) f (" in fifo and "assign f_in_valid = in_valid;" in fifo
    assert "rv_wire #(.WIDTH(W)) w (" in wire and "assign w_in_valid = in_valid;" in wire
    assert "rv_wire" not in fifo and "fw_rv_fifo" not in wire
    # An if with no else: the checker.
    m = re.search(r"if \(check\) begin : g_\d+(.*?)\n  end\n", sv, re.S)
    assert m and "fw_valid_check chk (" in m.group(1) and "else" not in m.group(1)


def test_generate_if_same_child(tmp_path):
    """u is a stage_fifo or a stage_wire (both stage #(W), W passed through
    their extends clauses); connect() connects u after the branch: the
    connections go in each arm."""
    r, res = _emit(tmp_path, "pick_pipe")
    assert r.ir_diags == []
    assert [p.name for p in res.files] == ["pick_pipe.sv"]
    sv = _text(res, "pick_pipe")
    m = re.search(r"if \(use_fifo\) begin : (g_\d+)(.*?)end else begin : \1_else(.*?)\n  end\n",
                  sv, re.S)
    assert m, sv
    fifo, wire = m.group(2), m.group(3)
    assert "fw_rv_fifo #(.WIDTH(W), .DEPTH(depth)) u (" in fifo
    assert "rv_wire #(.WIDTH(W)) u (" in wire
    for arm in (fifo, wire):
        assert "assign u_in_valid = in_valid;" in arm and "assign out_data = u_out_data;" in arm
    assert "assign u_clk = clk;" in fifo and "u_clk" not in wire   # the wire has no clock pin


def test_port_arrays(tmp_path):
    r, res = _emit(tmp_path, "lanes")
    assert r.ir_diags == []
    assert [p.name for p in res.files] == ["lanes.sv"]
    sv = _text(res, "lanes")
    assert re.search(r"module lanes #\(\s*parameter int W = 8,\s*parameter int n = 2,\s*"
                     r"parameter int depth = 2\s*\)", sv), sv
    for decl in ("input logic in_valid [n]", "output logic in_ready [n]",
                 "input logic [W-1:0] in_data [n]", "output logic [W-1:0] out_data [n]",
                 "output logic fire [n]"):
        assert decl in sv, decl
    assert re.search(r"for \(genvar i = 0; i < n; i = i \+ 1\) begin : u\s+"
                     r"fw_rv_fifo #\(\.WIDTH\(W\), \.DEPTH\(depth\)\) u \(", sv), sv
    assert "assign u_in_valid[i] = in_valid[i];" in sv
    assert "assign out_data[i] = u_out_data[i];" in sv
    assert "assign fire[i] = u_out_valid[i];" in sv


def test_child_port_arrays(tmp_path):
    r, res = _emit(tmp_path, "lanes_top")
    assert r.ir_diags == []
    assert sorted(p.name for p in res.files) == ["lanes.sv", "lanes_top.sv"]
    sv = _text(res, "lanes_top")
    assert "lanes #(.W(W), .n(n), .depth(1)) x (" in sv
    assert "logic [W-1:0] x_in_data [n];" in sv
    assert "assign x_in_valid[i] = in_valid[i];" in sv
    assert "assign out_data[i] = x_out_data[i];" in sv


def test_outside_subset_specializes(tmp_path):
    """A case on a parameter is not in the structural subset: the module is
    emitted per specialization, with an informational diagnostic naming the
    construct; with require_param it is an error."""
    r, res = _emit(tmp_path, "gaps_pkg::by_case#(8)")
    assert [d.id for d in r.ir_diags] == ["FW-I-EMIT-SPEC"]
    assert r.ir_diags[0].path == "top" and "a case on a parameter" in r.ir_diags[0].msg
    sv = res.files[0].read_text()
    assert "parameter" not in sv and "rv_wire #(.WIDTH(8)) w (" in sv
    with pytest.raises(ElabError, match="FW-E-EMIT-SPEC.*a case on a parameter"):
        _elab("gaps_pkg::by_case#(8)").to_ir(require_param=True)


def test_struct_literal_argument_specializes(tmp_path):
    """A shape written as a struct literal over a parameter is not traced:
    the component is a specialization, the diagnostic says which parameter."""
    r, res = _emit(tmp_path, "gaps_pkg::lit_shape#(8)")
    assert [d.id for d in r.ir_diags] == ["FW-I-EMIT-SPEC"]
    assert "class parameter S_data_w of top.u" in r.ir_diags[0].msg
    assert ".WIDTH(8)" in _text(res, "lit_shape")


def test_dependent_default_specializes(tmp_path):
    """dfifo #(W) leaves D to its default W / 4: D cannot be passed as a
    constant, so dflt_dep is a specialization."""
    r, res = _emit(tmp_path, "gaps_pkg::dflt_dep#(8)")
    assert [d.id for d in r.ir_diags] == ["FW-I-EMIT-SPEC"]
    assert "class parameter D of top.u is left to its default" in r.ir_diags[0].msg
    assert ".WIDTH(8), .DEPTH(2)" in _text(res, "dflt_dep")


# ------------------------------------------------------------------ verilator

LINT = [
    ("wpipe", []),
    ("spipe", []),
    ("dpipe", []),
    ("cond_pipe", []),
    # The other arm of use_fifo. (With check=0 too, clk and rst are unused,
    # truly: UNUSEDSIGNAL, as for hand-written RTL.)
    ("cond_pipe", ["-Guse_fifo=0", "-Gcheck=1"]),
    ("pick_pipe", []),
    ("lanes", []),
    ("lanes_top", ["-Gn=3"]),
]


@needs_verilator
@pytest.mark.parametrize("name,gs", LINT, ids=[f"{n}{''.join(g)}" for n, g in LINT])
def test_lint(tmp_path, name, gs):
    _r, res = _emit(tmp_path, name)
    files = res.filelist.read_text().split()
    lint = subprocess.run(["verilator", "--lint-only", "-Wall", "--top-module", name] + gs
                          + files, capture_output=True, text=True, cwd=tmp_path)
    gen = {p.name for p in res.files}
    bad = [l for l in lint.stderr.splitlines() if any(g in l for g in gen)]
    assert not bad, lint.stderr


SIMS = [
    # design, tb, defines, -G parameters
    ("wpipe", "pipe_tb", "DUT_WPIPE", {"W": 8, "D": 2}),
    ("wpipe", "pipe_tb", "DUT_WPIPE", {"W": 16, "D": 3}),
    ("spipe", "pipe_tb", "DUT_SPIPE", {"W": 8, "D": 2}),
    ("spipe", "pipe_tb", "DUT_SPIPE", {"W": 12, "D": 4}),
    ("dpipe", "pipe_tb", "DUT_DPIPE", {"W": 8}),
    ("dpipe", "pipe_tb", "DUT_DPIPE", {"W": 4}),
    ("cond_pipe", "pipe_tb", "DUT_COND_PIPE", {"W": 8, "D": 2, "USE_FIFO": 1, "CHECK": 1}),
    ("cond_pipe", "pipe_tb", "DUT_COND_PIPE", {"W": 8, "D": 2, "USE_FIFO": 0, "CHECK": 0}),
    ("cond_pipe", "pipe_tb", "DUT_COND_PIPE", {"W": 16, "D": 4, "USE_FIFO": 1, "CHECK": 0}),
    ("cond_pipe", "pipe_tb", "DUT_COND_PIPE", {"W": 5, "D": 1, "USE_FIFO": 0, "CHECK": 1}),
    ("pick_pipe", "pipe_tb", "DUT_PICK_PIPE", {"W": 8, "D": 2, "USE_FIFO": 1}),
    ("pick_pipe", "pipe_tb", "DUT_PICK_PIPE", {"W": 12, "D": 3, "USE_FIFO": 0}),
    ("lanes", "lanes_tb", "DUT_LANES", {"W": 8, "N": 2, "D": 2}),
    ("lanes", "lanes_tb", "DUT_LANES", {"W": 16, "N": 3, "D": 1}),
    ("lanes_top", "lanes_tb", "DUT_LANES_TOP", {"W": 8, "N": 2}),
    ("lanes_top", "lanes_tb", "DUT_LANES_TOP", {"W": 12, "N": 4}),
]


@needs_verilator
@pytest.mark.parametrize("name,tb,define,gs", SIMS,
                         ids=[f"{n}-" + "-".join(f"{k}{v}" for k, v in g.items())
                              for n, _t, _d, g in SIMS])
def test_simulate(tmp_path, name, tb, define, gs):
    _r, res = _emit(tmp_path, name)
    files = res.filelist.read_text().split()
    sim = tmp_path / "sim"
    build = subprocess.run(
        ["verilator", "--binary", "-Wno-fatal", "--top-module", tb, f"+define+{define}",
         "-Mdir", str(sim)] + [f"-G{k}={v}" for k, v in gs.items()]
        + files + TB + [str(EX / "tb" / f"{tb}.sv")],
        capture_output=True, text=True, cwd=tmp_path)
    (tmp_path / "build.log").write_text(build.stdout + build.stderr)
    assert build.returncode == 0, build.stderr[-3000:]
    run = subprocess.run([str(sim / f"V{tb}")], capture_output=True, text=True, cwd=tmp_path)
    (tmp_path / "run.log").write_text(run.stdout + run.stderr)
    assert "PASS:" in run.stdout, run.stdout[-3000:]
