"""std's neutral APIs (``fw-hdl-spec/std``) get kit collateral though no
protocol uses them, and it elaborates in Verilator."""
import shutil
import subprocess
from pathlib import Path

import pytest

from fw.hdl.fe.sv.scan import scan
from fw.hdl.gen import gen_kit

PKGS = Path(__file__).resolve().parents[4]
STD = PKGS / "fw-hdl-spec" / "std"
SPEC_SV = PKGS / "fw-hdl-spec" / "bindings" / "sv"
RT = PKGS / "fw-hdl-rt-sv" / "sv"
#: API -> a specialization of it.
APIS = {"fw_put_if": "#(int)", "fw_get_if": "#(int)",
        "fw_reqrsp_if": "#(int, bit [7:0])",
        "fw_mem_if": "#(logic [31:0], logic [31:0], logic [3:0])"}


def test_std_kit(tmp_path):
    r = scan([str(STD / "fw_hdl_std_pkg.sv")], incdirs=[str(STD), str(SPEC_SV)])
    assert r.ok, r.diags
    assert set(APIS) <= set(r.apis)
    assert r.table() == {}
    assert "fw_inbox" not in r.apis
    sv = gen_kit(r)
    assert sv.startswith("package fw_hdl_std_pkg_gen_pkg;")
    for api in APIS:
        for sfx in ("_base", "_listener_fanout", "_proxy", "_imp", "_txn_listener"):
            assert f"class {api}{sfx} #(" in sv, api + sfx
    # fw_rv_if is a pin bundle, not a bundle of channels: the xtor generator
    # leaves std's hand-written fw_rv_initiator/target/monitor alone, and the
    # package stays macro-free (unchanged by K9.7).
    assert "_initiator" not in sv and "_target #(" not in sv and "`fw_" not in sv
    # F8a: fw_mem_if's 4-state defaults are kept, as the API writes them.
    assert ("class fw_mem_if_proxy #(type ADDR = logic [31:0], type DATA = logic [31:0], "
            "type STRB = logic [3:0])") in sv

    if shutil.which("verilator") is None:
        pytest.skip("verilator not on PATH")
    gen = tmp_path / "fw_hdl_std_pkg_gen_pkg.sv"
    gen.write_text(sv)
    lines = ["module std_chk;", "  import fw_hdl_std_pkg::*;",
             "  import fw_hdl_std_pkg_gen_pkg::*;"]
    for i, (api, spec) in enumerate(APIS.items()):
        lines += [f"  class chk_{i} extends {api}_base {spec}; endclass",
                  f"  {api}_proxy {spec} p_{i} = new;",
                  f"  {api}_listener_fanout {spec} f_{i} = new;",
                  f"  initial begin chk_{i} c; c = new; end"]
    # The defaults: a default proxy is a default fw_mem_if.
    lines += ["  fw_mem_if_proxy p_dflt = new;", "  fw_mem_if m_dflt = p_dflt;"]
    # The runtime pieces: an inbox is both a put and a get endpoint.
    lines += ["  fw_inbox #(int) ib = new;", "  fw_put_if #(int) ib_put = ib;",
              "  fw_get_if #(int) ib_get = ib;",
              "  fw_field_t fld = '{name: \"awaddr\", width: 32, dir: fw_dir_flip(FW_FWD)};"]
    chk = tmp_path / "std_chk.sv"
    chk.write_text("\n".join(lines + ["endmodule", ""]))
    cmd = ["verilator", "--lint-only", "-Wno-fatal", "-Wno-MULTITOP",
           f"-I{RT}", f"-I{SPEC_SV}", f"-I{STD}",
           str(RT / "fw_hdl_pkg.sv"), str(STD / "fw_hdl_std_pkg.sv"), str(gen), str(chk)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
