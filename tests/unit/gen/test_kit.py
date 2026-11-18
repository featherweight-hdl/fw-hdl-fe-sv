"""The kit model and the (still empty) generated kit package: it compiles
in Verilator with the kit's core package and fw_hdl_pkg."""
import shutil
import subprocess
from pathlib import Path

import pytest

import zuspec.ir.core as ir
from fw.hdl.fe.sv.scan import scan
from fw.hdl.gen import GEN_VERSION, gen_kit
from fw.hdl.gen.kit import GenError, Kit

PKGS = Path(__file__).resolve().parents[4]
CONF = PKGS / "fw-hdl-spec" / "conformance"
RT = PKGS / "fw-hdl-rt-sv" / "sv"
STD = PKGS / "fw-hdl-spec" / "std"
#: Kit scenarios beyond the conformance suite's.
SRC = Path(__file__).parent / "src"


def _scan(name):
    d = SRC / name if (SRC / name).is_dir() else CONF / name
    r = scan([str(d / "top.sv")], incdirs=[str(d), str(CONF / "_lib")])
    assert r.ok, r.diags
    return r


def test_kit_proto_abs():
    r = _scan("proto_abs")
    ctx, _ = r.to_ir()
    kit = Kit.from_scan(ctx, r.table(), r.api_packages())
    assert kit.pkg == "scenario"
    apis = {a.name: a for a in kit.apis}
    assert sorted(apis) == ["test_get_if", "test_put_if"]
    put = apis["test_put_if"]
    assert [p.name for p in put.tmpl] == ["T"]
    assert isinstance(put.proto, ir.DataTypeProtocol)
    assert [m.name for m in put.proto.methods] == ["put"]
    assert isinstance(put.ref, ir.DataTypeSpecialized)
    assert sorted(put.protocols) == [("test_phy_proto", "req"), ("test_pkt_proto", "req")]


def test_kit_pkg_from_api_package():
    # proto_param's payloads are unnamed (logic[W-1:0]): the package comes
    # from the API class.
    r = _scan("proto_param")
    ctx, _ = r.to_ir()
    assert Kit.from_scan(ctx, r.table(), r.api_packages()).pkg == "scenario"
    with pytest.raises(GenError) as e:
        Kit.from_scan(ctx, r.table())
    assert e.value.code == "FW-E-GEN-PKG"
    with pytest.raises(GenError) as e:
        Kit.from_scan(ctx, r.table(), ["scenario", "other_pkg"])
    assert e.value.code == "FW-E-GEN-PKG"


def test_kit_bundle_channels():
    # A bundle of channels: fwd from fw_bundle, rev from fw_mirror, the
    # payload over the bundle's parameters. Bundles with no channel
    # members (proto_param's is an fw_rv_if itself) are left out.
    r = _scan("proto_chan")
    ctx, _ = r.to_ir()
    kit = Kit.from_scan(ctx, r.table(), r.api_packages(), r.bundles.values())
    assert [b.name for b in kit.bundles] == ["test_ch_bundle"]
    b = kit.bundles[0]
    assert b.prefix == "test_ch" and [p.name for p in b.tmpl] == ["W"]
    assert [(c.name, c.fwd, c.family) for c in b.channels] == [
        ("req", True, "fw_rv_if"), ("rsp", False, "fw_rv_if")]
    req, rsp = (c.payload for c in b.channels)
    assert isinstance(req, ir.DataTypeInt) and req.bits == 8 and req.four_state
    assert req.width_expr is not None
    assert isinstance(rsp, ir.DataTypeRef) and rsp.ref_name == "scenario::test_ch_rsp_t"
    # The channel APIs, derived: one task per channel, over W.
    derived = [a for a in kit.apis if a.derived]
    assert [a.name for a in derived] == ["test_ch_req_if", "test_ch_rsp_if"]
    assert [[m.name for m in a.proto.methods] for a in derived] == [["req"], ["rsp"]]
    assert all(m.is_task for a in derived for m in a.proto.methods)
    assert all([p.name for p in a.tmpl] == ["W"] for a in derived)
    r = _scan("proto_param")
    ctx, _ = r.to_ir()
    assert Kit.from_scan(ctx, r.table(), r.api_packages(), r.bundles.values()).bundles == []


def _gen(name):
    diags = []
    sv = gen_kit(_scan(name), diags=diags)
    return sv, {d.id: d for d in diags}


def test_gen_unspecialized_bundle_warns():
    # FW-W-GEN-UNSPEC: a parameterized bundle nothing specializes gets no
    # channel APIs; the warning names it and the fix.
    sv, diags = _gen("gen_unspec")
    d = diags["FW-W-GEN-UNSPEC"]
    assert d.severity == "warning" and d.path == "test_ch_bundle"
    assert "typedef test_ch_bundle #(" in d.msg
    assert "test_ch_req_if" not in sv
    # proto_chan specializes it: no warning.
    _, diags = _gen("proto_chan")
    assert "FW-W-GEN-UNSPEC" not in diags


def test_gen_4state_struct_payload_warns():
    # FW-W-GEN-4STATE: the channel API keeps the payload's type (not
    # ``int``), but there are no helpers.
    sv, diags = _gen("gen_4state")
    d = diags["FW-W-GEN-4STATE"]
    assert d.severity == "warning" and d.path == "fs_bundle.req"
    assert "scenario::fs_req_t" in d.msg and "bit" in d.msg
    assert "task req(input scenario::fs_req_t t);" in sv
    assert "fs_payloads" not in sv
    # F8a: the IR models the struct, 4-state member included, so the scan
    # has no FW-W-SCAN-IR warning; class emission still does not use it.
    assert "FW-W-SCAN-IR" not in diags
    ctx, _ = _scan("gen_4state").to_ir()
    st = ctx.type_m["scenario::fs_req_t"]
    assert [(f.name, f.datatype.four_state) for f in st.fields] == [
        ("code", True), ("tag", False)]


def test_gen_class_local_payload_rejected():
    with pytest.raises(GenError) as e:
        gen_kit(_scan("gen_scope"), diags=[])
    assert e.value.code == "FW-E-GEN-SCOPE"
    assert "ls_bundle::req_t" in str(e.value) and "package level" in str(e.value)


def test_gen_pkg_std_apis_only():
    # A kit whose only API is std's (with a builtin payload) is the
    # protocol's package, not std's; it has nothing to generate.
    sv, diags = _gen("gen_stdonly")
    assert "package scenario_gen_pkg;" in sv and "class" not in sv
    assert not diags


def test_gen_pkg_protocol_outside_package():
    with pytest.raises(GenError) as e:
        gen_kit(_scan("gen_unit"), diags=[])
    assert e.value.code == "FW-E-GEN-PKG"
    assert "'so_proto' is declared outside a package" in str(e.value)


def test_gen_version():
    assert len(GEN_VERSION) == 16


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
@pytest.mark.parametrize("name", ["proto_abs", "proto_param"])
def test_empty_kit_lints(name, tmp_path):
    sv = gen_kit(_scan(name))
    assert "package scenario_gen_pkg;" in sv
    gen = tmp_path / "scenario_gen_pkg.sv"
    gen.write_text(sv)
    d = CONF / name
    # fw_hdl_pkg and the scenario have -Wall warnings of their own: only
    # the generated file must have none.
    cmd = ["verilator", "--lint-only", "-Wall", "-Wno-DECLFILENAME", "-Wno-UNUSEDSIGNAL",
           "-Wno-fatal",
           f"-I{RT}", f"-I{PKGS / 'fw-hdl-spec' / 'bindings' / 'sv'}", f"-I{STD}",
           f"-I{d}", f"-I{CONF / '_lib'}",
           str(RT / "fw_hdl_pkg.sv"), str(STD / "fw_hdl_std_pkg.sv"), str(d / "top.sv"), str(gen)]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "scenario_gen_pkg" not in r.stderr, r.stderr
