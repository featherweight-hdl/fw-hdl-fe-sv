"""K9.5b: kit timed channels are plain components the user places
(protocol-kit prep §3.5). fe-sv records them as components with their
endpoints and cfg bindings; elaboration inserts nothing. The run (delivery
times) is the sim-only spec scenario ``proto_timed``, checked by rt-sv."""
from fw.hdl.fe.sv import elaborate
from fw.hdl.fe.sv.api import spec_dir


def test_timed_chan_components():
    d = spec_dir() / "conformance" / "proto_timed"
    r = elaborate([str(d / "top.sv")], top="scenario::top",
                  incdirs=[str(d), str(d.parent / "_lib")])
    assert r.ok, [str(x) for x in r.diags]
    n = r.netlist()
    comps = {o["path"]: o["type"] for o in n["objects"] if o["kind"] == "component"}
    assert comps["top.f"] == "uart_tx_chan"
    assert comps["top.x"] == "uart_txrx_chan"
    assert set(comps) == {"top", "top.rg", "top.d", "top.f", "top.v", "top.ua", "top.x", "top.ub"}
    cfg = {(b["from"], b["to"]) for b in n["bindings"] if b["kind"] == "cfg"}
    assert cfg == {("top.d.p", "top.f.dev"), ("top.f.vip", "top.v.e"),
                   ("top.ua.s", "top.x.a"), ("top.ub.s", "top.x.b")}
