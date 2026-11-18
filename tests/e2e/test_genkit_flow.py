"""K7.6: the dfm task ``fw.hdl.fe.sv.GenKit`` writes a kit's generated
package, and re-runs when the generators change."""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

CONF = Path(__file__).resolve().parents[3] / "fw-hdl-spec" / "conformance"


def _run(cwd):
    r = subprocess.run(["dfm", "run", "gen"], capture_output=True, text=True, cwd=cwd)
    (cwd / "dfm.log").write_text(r.stdout + r.stderr)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return next((cwd / "rundir").rglob("*_gen_pkg.sv"))


@pytest.mark.skipif(shutil.which("dfm") is None, reason="dfm not on PATH")
def test_genkit_through_dfm(tmp_path):
    from fw.hdl.fe.sv.scan import scan
    from fw.hdl.gen import GEN_VERSION, gen_kit
    d = CONF / "proto_abs"
    (tmp_path / "flow.yaml").write_text(f"""
package:
  name: gk
  tasks:
  - name: design
    uses: std.FileSet
    with: {{type: fwHdlSource, base: "{d}", include: [top.sv],
            incdirs: ["{d}", "{CONF / '_lib'}"]}}
  - root: gen
    uses: fw.hdl.fe.sv.GenKit
    needs: [design]
""")
    out = _run(tmp_path)
    want = gen_kit(scan([str(d / "top.sv")], incdirs=[str(d), str(CONF / "_lib")]))
    assert out.read_text() == want

    # Nothing changed: up to date, the package is not rewritten.
    mtime = out.stat().st_mtime_ns
    assert _run(tmp_path).stat().st_mtime_ns == mtime

    # A run by another generator version is stale.
    ex = next(p for p in out.parent.glob("*.json")
              if '"gen_version"' in p.read_text())
    data = json.loads(ex.read_text())
    assert data["result"]["memento"]["gen_version"] == GEN_VERSION
    data["result"]["memento"]["gen_version"] = "old"
    ex.write_text(json.dumps(data))
    assert _run(tmp_path).stat().st_mtime_ns != mtime


def test_genkit_uptodate():
    from fw.hdl.fe.sv.dfm import GenKitUpToDate
    from fw.hdl.gen import GEN_VERSION
    chk = lambda m: asyncio.run(GenKitUpToDate(SimpleNamespace(memento=m)))
    assert chk({"gen_version": GEN_VERSION})
    assert not chk({"gen_version": "0"})
    assert not chk(None)
