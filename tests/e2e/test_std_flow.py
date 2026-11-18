"""K8.4: fw-hdl-spec's ``std-src`` through dfm: the hand-written std source
-> ``fw.hdl.fe.sv.GenKit`` -> a package that lints in Verilator."""
import shutil
import subprocess
from pathlib import Path

import pytest

PKGS = Path(__file__).resolve().parents[3]
SPEC = PKGS / "fw-hdl-spec"
RT = PKGS / "fw-hdl-rt-sv" / "sv"


@pytest.mark.skipif(shutil.which("dfm") is None or shutil.which("verilator") is None,
                    reason="dfm or verilator not on PATH")
def test_std_through_dfm(tmp_path):
    from fw.hdl.fe.sv.scan import scan
    from fw.hdl.gen import gen_kit
    (tmp_path / "flow.yaml").write_text(f"""
package:
  name: std_flow
  imports:
  - name: fw-hdl-spec
    from: {SPEC / 'flow.yaml'}
  tasks:
  - root: gen
    uses: fw.hdl.fe.sv.GenKit
    needs: [fw-hdl-spec.std-src]
""")
    r = subprocess.run(["dfm", "run", "gen"], capture_output=True, text=True, cwd=tmp_path)
    (tmp_path / "dfm.log").write_text(r.stdout + r.stderr)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    out = next((tmp_path / "rundir").rglob("*_gen_pkg.sv"))
    assert out.name == "fw_hdl_std_pkg_gen_pkg.sv"
    std = SPEC / "std"
    want = gen_kit(scan([str(std / "fw_hdl_std_pkg.sv")],
                        incdirs=[str(std), str(SPEC / "bindings" / "sv")]))
    assert out.read_text() == want

    cmd = ["verilator", "--lint-only", "-Wno-fatal",
           f"-I{RT}", f"-I{SPEC / 'bindings' / 'sv'}", f"-I{std}",
           str(RT / "fw_hdl_pkg.sv"), str(std / "fw_hdl_std_pkg.sv"), str(out)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
