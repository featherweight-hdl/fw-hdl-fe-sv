"""The fw-hdl-spec conformance scenarios on fe-sv (core-layer1.md §7).

fe-sv runs every scenario, including the ``static-only`` ones (header checks
and static analysis), and must produce each expected netlist or set of
diagnostics exactly (semantics/netlist.md, "Conformance comparison")."""
import difflib
import json
import os
from pathlib import Path

import pytest

from fw.hdl.fe.sv import elaborate
from fw.hdl.fe.sv.api import spec_dir
from fw.hdl.fe.sv.netlist import canonical


def _scenarios():
    try:
        conf = spec_dir() / "conformance"
    except Exception:
        return []
    return sorted(d for d in conf.iterdir() if (d / "top.sv").is_file())


@pytest.mark.parametrize("d", _scenarios(), ids=lambda d: d.name)
def test_scenario(d: Path):
    meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").is_file() else {}
    expect = json.loads((d / "expect.json").read_text())
    r = elaborate([str(d / "top.sv")], top="scenario::top", incdirs=[str(d)],
                  rtl=[str(d / f) for f in meta.get("rtl", [])])
    assert r.rt is not None, "\n".join(map(str, r.diags))
    got = r.netlist()
    if "objects" in expect:
        a, b = canonical(expect), canonical(got)
    else:
        a, b = canonical(expect["diagnostics"]), canonical(got["diagnostics"])
    diff = "\n".join(difflib.unified_diff(a.splitlines(), b.splitlines(), "expect", "fe-sv",
                                          lineterm="", n=1))
    msgs = "\n".join(map(str, r.diags))
    assert a == b, f"{diff}\n--- diagnostics:\n{msgs}"
    fails = any(e["id"].startswith("FW-E-") for e in expect.get("diagnostics", []))
    assert r.ok != fails
