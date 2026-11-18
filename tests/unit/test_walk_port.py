"""F10: ``protocols.walk_port``, the one walk over a port's bundle tree
that gen_tb and gen_bind share, on a nested port (src/deep_bundle_pkg.sv):
a protocol bundle holding two bundles of rv channels, one mirrored, the
port itself mirrored. And the error when the outermost protocol level has
no transactor: gen_tb never falls back to the inner channels'."""
import re
from pathlib import Path

import pytest

from fw.hdl.fe.sv import elaborate
from fw.hdl.fe.sv.emit import emit_sv
from fw.hdl.fe.sv.protocols import walk_port
from fw.hdl.fe.sv.scan import scan
from fw.hdl.gen import gen_bind, gen_tb
from fw.hdl.gen.kit import GenError

PKGS = Path(__file__).resolve().parents[3]
SPEC = PKGS / "fw-hdl-spec"
INC = [str(SPEC / "std"), str(SPEC / "bindings" / "sv")]
SRC = Path(__file__).parent / "src" / "deep_bundle_pkg.sv"

#: port p (fw_mirror #(deep_bundle)): each pin's name, its member path in
#: the port, and its direction for the DUT. A pin's declared direction is
#: reversed once per mirror on its path: p, b and y are mirrors.
P_PINS = [
    ("p_en",        "en",          "in"),    # p
    ("p_a_x_valid", "a.x.valid",   "in"),    # p
    ("p_a_x_ready", "a.x.ready",   "out"),
    ("p_a_x_data",  "a.x.data",    "in"),
    ("p_a_y_valid", "a.y.valid",   "out"),   # p, y
    ("p_a_y_ready", "a.y.ready",   "in"),
    ("p_a_y_data",  "a.y.data",    "out"),
    ("p_b_x_valid", "b.x.valid",   "out"),   # p, b
    ("p_b_x_ready", "b.x.ready",   "in"),
    ("p_b_x_data",  "b.x.data",    "out"),
    ("p_b_y_valid", "b.y.valid",   "in"),    # p, b, y
    ("p_b_y_ready", "b.y.ready",   "out"),
    ("p_b_y_data",  "b.y.data",    "in"),
]


@pytest.fixture(scope="module")
def rq():
    rq = elaborate([str(SRC)], top="deep_pkg::dut_q", incdirs=INC)
    assert rq.ok, "\n".join(map(str, rq.diags))
    return rq


@pytest.fixture(scope="module")
def r():
    r = elaborate([str(SRC)], top="deep_pkg::dut", incdirs=INC)
    assert r.ok, "\n".join(map(str, r.diags))
    return r


def test_walk_nested(r):
    w = walk_port(r.top.children["p"])
    assert w.name == "p" and w.path == ["p"]
    assert [(p.name, ".".join(p.rel), p.dir) for p in w.pins] == P_PINS
    # the outermost protocol level is the port: one level, not the four
    # rv channels below it
    assert [(l.rel, l.kind, l.proto.name) for l in w.levels] == [([], "mirror", "deep_proto")]
    # the same directions as the object model's own rule
    assert all(p.dir == p.sig.effective_dir() for p in w.pins)


def test_walk_levels_below(rq):
    """A port with no protocol at its own level: the outermost protocol
    levels are its channels, each with its mixin as the DUT sees it."""
    w = walk_port(rq.top.children["q"])
    assert [(l.rel, l.kind, l.proto.name) for l in w.levels] == [
        (["x"], "bundle", "fw_rv_proto#(bit[7:0])"),
        (["y"], "mirror", "fw_rv_proto#(bit[3:0])")]
    assert [(p.name, p.dir) for p in w.pins] == [
        ("q_x_valid", "out"), ("q_x_ready", "in"), ("q_x_data", "out"),
        ("q_y_valid", "in"), ("q_y_ready", "out"), ("q_y_data", "in")]


def test_walk_matches_emitter(r, tmp_path):
    """Each pin's name and direction is the emitted module's port."""
    ctxt, top = r.to_ir()
    res = emit_sv(ctxt, top, tmp_path, top_module="dut")
    text = "\n".join(Path(f).read_text() for f in res.filelist.read_text().split()
                     if Path(f).name.startswith("dut"))
    ports = dict((m[2], m[1]) for m in re.finditer(r"\b(input|output)\b[^,;)]*?\b(p_\w+)", text))
    assert {n: ("in" if d == "input" else "out") for n, d in ports.items()} == \
        {n: d for n, _, d in P_PINS}


def test_missing_xtor(r):
    """deep_proto has no transactor in the scan (std's rv ones are there):
    gen_tb reports the protocol, the role and the port."""
    s = scan([str(SPEC / "std" / "fw_hdl_std_pkg.sv")], incdirs=INC)
    assert s.ok, s.diags
    with pytest.raises(GenError) as e:
        gen_tb(r, s, "dut")
    assert e.value.code == "FW-E-TB-XTOR"
    msg = str(e.value)
    assert "deep_proto" in msg and "initiator" in msg and "port p" in msg
    with pytest.raises(GenError) as e:
        gen_bind(r, s, "dut")
    assert e.value.code == "FW-E-TB-MON"
    assert "deep_proto" in str(e.value) and "monitor" in str(e.value) and "port p" in str(e.value)


def test_port_without_protocol(rq):
    """A port whose own bundle has no protocol, grouping protocol bundles,
    is an error: gen_tb neither skips it nor drives its channels."""
    s = scan([str(SPEC / "std" / "fw_hdl_std_pkg.sv")], incdirs=INC)
    assert s.ok, s.diags
    for gen in (gen_tb, gen_bind):
        with pytest.raises(GenError) as e:
            gen(rq, s, "dut_q")
        assert e.value.code == "FW-E-TB-NOPROTO"
        msg = str(e.value)
        assert "port q" in msg and "pair_bundle" in msg and "fw_rv_proto" in msg
