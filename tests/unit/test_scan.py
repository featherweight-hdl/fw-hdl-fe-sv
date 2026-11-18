"""K6.1: scanning a protocols scenario, with no top, gives the protocols
table elaboration (and rt-sv) gives, and the same FW-E-PROTO
diagnostics."""
import json
from pathlib import Path

import pytest

from fw.hdl.fe.sv.scan import scan

CONF = Path(__file__).resolve().parents[3] / "fw-hdl-spec" / "conformance"
SCENARIOS = sorted(d.name for d in CONF.glob("proto_*"))


@pytest.mark.parametrize("name", SCENARIOS)
def test_scan_protocols(name):
    d = CONF / name
    e = json.loads((d / "expect.json").read_text())
    r = scan([str(d / "top.sv")], incdirs=[str(d), str(CONF / "_lib")])
    assert r.rt is not None, [str(x) for x in r.diags]
    if "protocols" in e:
        assert r.table() == e["protocols"]
    got = sorted({(x.id, x.path) for x in r.diags})
    want = sorted((x["id"], x["path"]) for x in e.get("diagnostics", [])
                  if x["id"].startswith("FW-E-PROTO"))
    assert got == want


@pytest.mark.parametrize("name", ["proto_abs", "proto_param"])
def test_scan_to_ir(name):
    """The scanned IR holds each API interface class as a DataTypeProtocol,
    and the generic API as a template over one, survives the serializer; a 4-state payload is a warning only."""
    import zuspec.ir.core as ir
    from zuspec.ir.core.deserializer import IRDeserializer
    from zuspec.ir.core.serializer import IRSerializer
    d = CONF / name
    r = scan([str(d / "top.sv")], incdirs=[str(d), str(CONF / "_lib")])
    ctx, diags = r.to_ir()
    apis = {a["type"] for p in r.table().values() for a in p["apis"]}
    assert apis and apis <= set(ctx.type_m)
    for a in apis:
        assert isinstance(ctx.type_m[a], ir.DataTypeProtocol)
        assert ctx.type_m[a].methods
    generic = ctx.type_m.get("test_put_if")
    assert isinstance(generic, ir.DataTypeParameterized)
    assert [p.name for p in generic.template_params] == ["T"]
    assert isinstance(generic.body, ir.DataTypeProtocol)
    assert generic.body.methods
    assert all(x.severity == "warning" for x in diags)
    des = IRDeserializer()
    des.auto_register(ir)
    ctx2, _ = des.deserialize(IRSerializer().serialize(ctx, None))
    assert set(ctx2.type_m) == set(ctx.type_m)
