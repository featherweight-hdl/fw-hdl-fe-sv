"""K6.2: the dfm task ``fw.hdl.fe.sv.Scan`` writes the protocols table
(scan.json) and the scanned IR (scan.zir) of a protocols scenario."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

CONF = Path(__file__).resolve().parents[3] / "fw-hdl-spec" / "conformance"


@pytest.mark.skipif(shutil.which("dfm") is None, reason="dfm not on PATH")
def test_scan_through_dfm(tmp_path):
    d = CONF / "proto_abs"
    (tmp_path / "flow.yaml").write_text(f"""
package:
  name: sc
  tasks:
  - name: design
    uses: std.FileSet
    with: {{type: fwHdlSource, base: "{d}", include: [top.sv],
            incdirs: ["{d}", "{CONF / '_lib'}"]}}
  - root: scan
    uses: fw.hdl.fe.sv.Scan
    needs: [design]
""")
    r = subprocess.run(["dfm", "run", "scan"], capture_output=True, text=True, cwd=tmp_path)
    (tmp_path / "dfm.log").write_text(r.stdout + r.stderr)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    sj = next((tmp_path / "rundir").rglob("scan.json"))
    meta = json.loads(sj.read_text())
    assert meta["protocols"] == json.loads((d / "expect.json").read_text())["protocols"]
    zir = sj.parent / meta["ir"]
    apis = {a["type"] for p in meta["protocols"].values() for a in p["apis"]}
    assert all(a in zir.read_text() for a in apis)


def _flow(tmp_path, d, *, scan):
    src = "[scan]" if scan else "[design]"
    (tmp_path / "flow.yaml").write_text(f"""
package:
  name: se
  tasks:
  - name: design
    uses: std.FileSet
    with: {{type: fwHdlSource, base: "{d}", include: [top.sv],
            incdirs: ["{d}", "{CONF / '_lib'}"]}}
  - name: scan
    uses: fw.hdl.fe.sv.Scan
    needs: [design]
  - root: elab
    uses: fw.hdl.fe.sv.Elaborate
    needs: {src}
    with: {{top: "scenario::top"}}
""")
    r = subprocess.run(["dfm", "run", "elab"], capture_output=True, text=True, cwd=tmp_path)
    (tmp_path / "dfm.log").write_text(r.stdout + r.stderr)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    nl = next((tmp_path / "rundir").rglob("netlist.json"))
    return nl.read_text(), (nl.parent / "design.zir").read_text()


@pytest.mark.skipif(shutil.which("dfm") is None, reason="dfm not on PATH")
@pytest.mark.parametrize("name", ["proto_abs", "proto_param"])
def test_scan_then_elaborate(tmp_path, name):
    """K6.3: Scan then Elaborate gives the same netlist and IR as Elaborate alone."""
    d = CONF / name
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert _flow(tmp_path / "a", d, scan=True) == _flow(tmp_path / "b", d, scan=False)


def test_scan_check():
    """A scanned protocol that disagrees with the elaborated one is FW-E-SCAN-STALE."""
    from fw.hdl.fe.sv.dfm import _scan_check
    t = json.loads((CONF / "proto_abs" / "expect.json").read_text())["protocols"]
    assert _scan_check(t, t) == []
    assert _scan_check(t, {}) == []
    bad = json.loads(json.dumps(t))
    bad["test_pkt_proto"]["prefix"] = "x"
    bad["test_phy_proto"]["bundles"] = []
    msgs = [m.msg for m in _scan_check(t, bad)]
    assert len(msgs) == 2 and "prefix" in msgs[1] and "bundles" in msgs[0]
