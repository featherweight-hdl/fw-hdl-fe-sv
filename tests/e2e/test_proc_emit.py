"""Process blocks lowered to RTL (layer-2 plan P3.4a/b): the lowerable
fw-hdl-spec ``proc_*`` scenarios get their step and comb blocks as
``always_ff`` / ``always_comb`` in the emitted modules, which lint clean
under ``verilator -Wall``; those that expect diagnostics get them.

The top's root clock, which nothing in the design drives, is the top
module's input port ``clk``. The test's reset generator drives ``rst``
whether or not a block reads it, so the top's ``rst`` UNUSEDSIGNAL warning
is expected in scenarios without a reset.

The lowered RTL runs the scenario's trace (layer-2 plan P3.4c): a bench
drives each root clock input with edges at multiples of its period and
writes the trace signals just before each of the ``trace.clock``'s first
``trace.edges`` edges, which is where fw-hdl-rt-sv samples (after the
commits and settle of every edge at that time, before its steps run)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from fw.hdl.fe.sv import elaborate
from fw.hdl.fe.sv.api import spec_dir
from fw.hdl.fe.sv.emit import emit_sv
from fw.hdl.fe.sv.rt import ElabError

needs_verilator = pytest.mark.skipif(shutil.which("verilator") is None,
                                     reason="verilator not on PATH")

SCENARIOS = ["proc_cdc", "proc_comb_chain", "proc_counter", "proc_mux", "proc_reset_state",
             "proc_rv", "proc_step_domains"]


def _root_clocks(ctxt, top):
    """The top's root clocks (the top module's clock inputs) by name, with
    their periods in ns."""
    return {d.name: d.params["period"].value for d in ctxt.type_m[top].domains
            if d.kind == "fw_clock_kind" and d.deriv == "root"}


def _emit(tmp_path, name, src=None):
    """Lowers scenario ``name``, or the source ``src`` in its directory."""
    sc = spec_dir() / "conformance" / name
    r = elaborate([str(src or sc / "top.sv")], top="scenario::top",
                  incdirs=[str(sc), str(sc.parent / "_lib")])
    assert r.ok, "\n".join(map(str, r.diags))
    ctxt, top = r.to_ir()
    assert r.ir_diags == []
    clocks = _root_clocks(ctxt, top)  # before emit_sv renames the types
    return ctxt, clocks, emit_sv(ctxt, top, tmp_path / "gen", top_module="ptop")


def test_counter_blocks(tmp_path):
    ctxt, _, res = _emit(tmp_path, "proc_counter")
    assert [b.name for b in ctxt.type_m["counter"].proc_blocks] == ["step"]
    # test_rst_gen's state n becomes a field (clk: the flattened domain port)
    assert [f.name for f in ctxt.type_m["test_rst_gen"].fields] == ["clk", "rst_n", "n"]
    text = (res.files[0].parent / "counter.sv").read_text()
    assert "input logic clk,\n  input logic rst," in text, text
    assert "always_ff @(posedge clk) begin" in text, text
    top = (res.files[0].parent / "ptop.sv").read_text()
    assert "input logic clk" in top, top


@needs_verilator
@pytest.mark.parametrize("name", SCENARIOS)
def test_lint(tmp_path, name):
    _, _, res = _emit(tmp_path, name)
    files = res.filelist.read_text().split()
    lint = subprocess.run(["verilator", "--lint-only", "-Wall", "--top-module", "ptop"]
                          + files, capture_output=True, text=True, cwd=tmp_path)
    bad = [ln for ln in lint.stderr.splitlines()
           if ln.startswith("%") and not ln.startswith("%Error: Exiting")
           and not ("ptop.sv" in ln and "not used: 'rst'" in ln)]
    assert bad == [], lint.stderr


@pytest.mark.parametrize("name", ["proc_write_in", "proc_write_multi"])
def test_expected_diags(name):
    """A scenario that expects diagnostics gets them, by id and path, from
    lowering (and lowering stops)."""
    sc = spec_dir() / "conformance" / name
    r = elaborate([str(sc / "top.sv")], top="scenario::top",
                  incdirs=[str(sc), str(sc.parent / "_lib")])
    assert r.ok, "\n".join(map(str, r.diags))
    with pytest.raises(ElabError):
        r.to_ir()
    want = json.loads((sc / "expect.json").read_text())["diagnostics"]
    assert sorted((d.id, d.path) for d in r.ir_diags if d.severity == "error") == \
        sorted((d["id"], d["path"]) for d in want)


TRACED = ["proc_cdc", "proc_comb_chain", "proc_counter", "proc_mux", "proc_reset_state",
          "proc_rv"]


def _ref(sig: str) -> str:
    """A trace signal (``top.<inst>.<member path>``) as the lowered net under
    the bench's ``top`` instance: the member path flattened with ``_``."""
    p = sig.split(".")
    return f"top.{p[1]}.{'_'.join(p[2:])}"


@needs_verilator
@pytest.mark.parametrize("name", TRACED)
def test_trace(tmp_path, name):
    _, clocks, res = _emit(tmp_path, name)
    tr = json.loads((spec_dir() / "conformance" / name / "expect.json").read_text())["trace"]
    assert _bench(tmp_path, clocks, res, tr["clock"], tr["signals"], tr["edges"]) == tr["rows"]


def _bench(tmp_path, clocks, res, clock, signals, edges):
    """Runs the lowered RTL and returns ``signals`` sampled just before each
    of the first ``edges`` edges of ``clock``, one row per edge."""
    period = clocks[clock.split(".")[1]]
    refs = [_ref(s) for s in signals]
    decls = "".join(f"  logic {c} = 0;\n" for c in clocks)
    conns = ", ".join(f".{c}({c})" for c in clocks)
    gens = "".join(f"  initial begin #{p}; forever begin {c} = 1; #{p / 2} {c} = 0; #{p / 2}; end end\n"
                   for c, p in clocks.items())
    (tmp_path / "tb.sv").write_text(f"""module tb;
{decls}  ptop top({conns});
{gens}  integer fd;
  initial begin
    fd = $fopen("trace.txt", "w");
    #{period - 0.1};
    repeat ({edges}) begin
      $fwrite(fd, "{' '.join(['%0d'] * len(refs))}\\n", {', '.join(refs)});
      #{period};
    end
    $fclose(fd);
    $finish;
  end
endmodule
""")
    b = subprocess.run(["verilator", "--binary", "-Wno-fatal", "-Wno-lint", "-Wno-style",
                        "--timescale", "1ns/1ps", "--top-module", "tb", "-Mdir", "obj",
                        "-o", "Vtb"] + res.filelist.read_text().split() + ["tb.sv"],
                       capture_output=True, text=True, cwd=tmp_path)
    assert b.returncode == 0, b.stderr
    subprocess.run([str(tmp_path / "obj" / "Vtb")], check=True, capture_output=True,
                   cwd=tmp_path)
    got = [[int(x) for x in ln.split()]
           for ln in (tmp_path / "trace.txt").read_text().splitlines()]
    return got


def _mon_core(tmp_path):
    """Lowers proc_rv with a std ``fw_rv_mon_core`` m tapping its link."""
    sc = spec_dir() / "conformance" / "proc_rv"
    src = (sc / "top.sv").read_text()
    for old, new in [("rv_snk k;\n", "rv_snk k;\n        fw_rv_mon_core #(bit [7:0]) m;\n"),
                     ('k = rv_snk::create("k", this);\n',
                      'k = rv_snk::create("k", this);\n'
                      '            m = fw_rv_mon_core #(bit [7:0])::create("m", this);\n'),
                     ("s.out.connect(k.in);\n", "s.out.connect(k.in);\n            m.p.connect(s.out);\n")]:
        assert old in src
        src = src.replace(old, new)
    (tmp_path / "top.sv").write_text(src)
    return _emit(tmp_path, "proc_rv", tmp_path / "top.sv")


@needs_verilator
def test_rv_mon_core(tmp_path):
    """fw-hdl-spec's std ``fw_rv_mon_core`` (layer-2 plan P5.1b) lowers to a
    parameterized module; tapping proc_rv's link, its ``fire`` and
    ``first`` follow that link's expected valid/ready trace."""
    _, clocks, res = _mon_core(tmp_path)
    text = (res.files[0].parent / "fw_rv_mon_core.sv").read_text()
    assert "parameter type T = bit" in text, text
    assert "input T p_data," in text, text
    tr = json.loads((spec_dir() / "conformance" / "proc_rv" / "expect.json").read_text())["trace"]
    sigs = ["top.s.out.valid", "top.s.out.ready", "top.m.fire", "top.m.first"]
    got = _bench(tmp_path, clocks, res, tr["clock"], sigs, tr["edges"])
    held, want = 0, []
    for v, r in ((row[0], row[1]) for row in tr["rows"]):
        want.append([v, r, int(v and r), int(v and not held)])
        held = int(v and not r)
    assert got == want


@needs_verilator
def test_rv_mon_mixed(tmp_path):
    """The lowered ``fw_rv_mon_core`` matches the class core cycle by cycle
    under fw-hdl-rt-sv's pin bridge, with the class side's run() tasks
    (initiator puts, monitor hooks) driving an RTL target (sv/rv_mon_mixed.sv)."""
    _, _, res = _mon_core(tmp_path)
    # Verilator 5.055 fails (internal error) on a module named like a class
    # in a compiled package, so the bench takes the module as rv_mon_core_rtl.
    gen = tmp_path / "rv_mon_core_rtl.sv"
    gen.write_text((res.files[0].parent / "fw_rv_mon_core.sv").read_text()
                   .replace("module fw_rv_mon_core", "module rv_mon_core_rtl"))
    spec = spec_dir()
    rt = spec.parent / "fw-hdl-rt-sv" / "sv"
    if not (rt / "fw_pin_bridge.sv").is_file():
        pytest.skip("needs fw-hdl-rt-sv beside fw-hdl-spec")
    tb = Path(__file__).parent / "sv" / "rv_mon_mixed.sv"
    (tmp_path / "obj").mkdir()
    b = subprocess.run(["verilator", "--binary", "-Wno-fatal", "-Wno-lint", "-Wno-style",
                        "--top-module", "rv_mon_mixed", "-Mdir", "obj", "-o", "Vx",
                        f"-I{rt}", f"-I{spec / 'bindings' / 'sv'}", f"-I{spec / 'std'}",
                        str(rt / "fw_hdl_pkg.sv"), str(spec / "std" / "fw_hdl_std_pkg.sv"),
                        str(rt / "fw_pin_bridge.sv"), str(gen), str(tb)],
                       capture_output=True, text=True, cwd=tmp_path)
    assert b.returncode == 0, b.stdout[-3000:] + b.stderr[-3000:]
    r = subprocess.run([str(tmp_path / "obj" / "Vx")], capture_output=True, text=True,
                       cwd=tmp_path)
    assert [ln for ln in r.stdout.splitlines() if ln.startswith(("R ", "D "))] == [
        "R got 20 span 26 stall 6 drops 0 bad 0",
        "R done 20 mon 20 20 bad 0",
        "R core fire 20 first 20 mism 0",   # RTL core == class core every cycle
    ]


@pytest.mark.skipif(shutil.which("sby") is None, reason="sby not on PATH")
def test_rv_formal(tmp_path):
    """The lowered ``fw_rv_mon_core`` proves the monitor's contract by BMC
    (depth 20, ``smtbmc z3``) in a harness with free pins
    (sv/rv_formal.sv), and covers a stalled beat being accepted."""
    _, _, res = _mon_core(tmp_path)
    # yosys has no type parameters: the payload becomes a width parameter.
    text = (res.files[0].parent / "fw_rv_mon_core.sv").read_text()
    for old, new in [("parameter type T = bit", "parameter int W = 1"),
                     ("input T p_data", "input logic [W-1:0] p_data")]:
        assert old in text, text
        text = text.replace(old, new)
    f = tmp_path / "formal"
    f.mkdir()
    (f / "fw_rv_mon_core.sv").write_text(text)
    shutil.copy(Path(__file__).parent / "sv" / "rv_formal.sv", f)
    (f / "rv.sby").write_text("""[tasks]
bmc
cover
[options]
bmc: mode bmc
cover: mode cover
depth 20
[engines]
smtbmc z3
[script]
read -formal fw_rv_mon_core.sv rv_formal.sv
prep -top rv_formal
[files]
fw_rv_mon_core.sv
rv_formal.sv
""")
    r = subprocess.run(["sby", "-f", "rv.sby"], capture_output=True, text=True, cwd=f)
    assert r.returncode == 0, r.stdout[-3000:]
    assert "[rv_bmc] DONE (PASS" in r.stdout and "[rv_cover] DONE (PASS" in r.stdout, r.stdout
