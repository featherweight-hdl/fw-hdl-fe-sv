"""K8 T, the protocol kit's Part A exit (plan §1), on std's rv protocol:

1. std's hand-written source builds through GenKit (as ``gen_kit`` here;
   through dfm in ``test_std_flow.py``) and the generated package compiles
   in Verilator, here as part of a simulation.
2. ``part_a/scenario.sv`` binds a put port to an export and an rv bundle
   to a mirror; ``part_a/tb.sv``
   elaborates it in rt-sv and calls the target through std's generated
   ``fw_put_if_proxy`` with a listener and a txn recorder attached.
3. The protocol queries rt-sv answers at run time (``Q`` lines) match
   fe-sv's IR of the same scenario.
4. The ``protocols`` conformance scenarios: rt-sv's ``tests/test_conformance.py``
   and fe-sv's ``tests/conformance``, not repeated here."""
import shutil
import subprocess
from pathlib import Path

import pytest
import zuspec.ir.core as ir

from fw.hdl.fe.sv import elaborate

PKGS = Path(__file__).resolve().parents[3]
SPEC = PKGS / "fw-hdl-spec"
STD = SPEC / "std"
RT = PKGS / "fw-hdl-rt-sv" / "sv"
HERE = Path(__file__).parent / "part_a"


def _sim(tmp_path):
    """Runs part_a/tb.sv in Verilator; returns its Q, L and T lines."""
    from fw.hdl.fe.sv.scan import scan
    from fw.hdl.gen import gen_kit
    gen = tmp_path / "fw_hdl_std_pkg_gen_pkg.sv"
    gen.write_text(gen_kit(scan([str(STD / "fw_hdl_std_pkg.sv")],
                                incdirs=[str(STD), str(SPEC / "bindings" / "sv")])))
    cmd = ["verilator", "--binary", "-Wno-fatal", "-Wno-lint", "-Wno-style",
           "--top-module", "tb", "-Mdir", str(tmp_path / "obj"),
           f"-I{RT}", f"-I{SPEC / 'bindings' / 'sv'}", f"-I{STD}",
           str(RT / "fw_hdl_pkg.sv"), str(STD / "fw_hdl_std_pkg.sv"), str(gen),
           str(HERE / "scenario.sv"), str(HERE / "tb.sv")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    r = subprocess.run([str(tmp_path / "obj" / "Vtb")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return [l for l in r.stdout.splitlines() if l[:2] in ("Q ", "L ", "T ")]


def _ir_queries():
    """The answers to tb.sv's queries, from fe-sv's IR."""
    r = elaborate([str(HERE / "scenario.sv")], top="scenario::top")
    assert r.ok, "\n".join(map(str, r.diags))
    ctxt, _ = r.to_ir()
    bps = {".".join(b.target): b for n in ("src", "sink")
           for b in ctxt.type_m[n].bundle_protocols}
    s, ts = bps["b"].protocol, bps["m"].protocol
    hub = ctxt.type_m[s.family]
    assert isinstance(hub, ir.DataTypeProtocolHub)
    req = [a.api for a in hub.apis if a.dir == ir.ProtoDir.REQ]
    rsp = [a.api for a in hub.apis if a.dir != ir.ProtoDir.REQ]
    users = [t for t in ctxt.type_m.values() if isinstance(t, ir.DataTypeProtocolHub)
             and any(a.api == req[0] for a in t.apis)]
    params = ",".join(v.ref_name if isinstance(v, ir.DataTypeRef) else str(v.value)
                      for v in s.params.values())
    assert [c for c in ctxt.type_m["top"].api_connections
            if (c.src, c.dst) == (["a", "p"], ["k", "e"])]
    return [
        "Q ndiag 0",
        f"Q same {int(s.family == ts.family and s.params == ts.params)}",
        f"Q family {s.family}",
        f"Q params {params}",
        f"Q req {req[0]}",
        f"Q rsp {int(not rsp)}",
        f"Q api_protocols {len(users)}",
    ]


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
def test_part_a_exit(tmp_path):
    out = _sim(tmp_path)
    q = [l for l in out if l.startswith("Q ")]
    assert q == _ir_queries()
    assert q == ["Q ndiag 0", "Q same 1", "Q family fw_rv_proto", "Q params byte",
                 "Q req fw_put_if", "Q rsp 1", "Q api_protocols 1"]
    # Each put: listeners' pre, then the target, then post.
    assert [l for l in out if l.startswith("L ")] == [
        "L pre 3", "L put 3", "L post 3", "L pre 5", "L put 5", "L post 5"]
    assert [l for l in out if l.startswith("T ")] == ["T put(t=3)", "T put(t=5)"]
