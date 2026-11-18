"""The fw-hdl-spec conformance scenarios on fe-sv (core-layer1.md §7).

fe-sv runs every scenario, including the ``static-only`` ones (header checks
and static analysis), and must produce each expected netlist or set of
diagnostics exactly (semantics/netlist.md, "Conformance comparison").
Scenarios that need a feature fe-sv does not implement yet (``features`` in
``meta.json``, not in ``FEATURES``) are skipped."""
import difflib
import json
import os
from pathlib import Path

import pytest

from fw.hdl.fe.sv import elaborate
from fw.hdl.fe.sv.api import spec_dir
from fw.hdl.fe.sv.netlist import canonical

# The optional features of the spec that fe-sv implements (meta.json
# "features").
FEATURES = {"domains", "protocols"}

# Scenarios that need a part of a feature still to come
# (core-domains-plan.md §9). None now; kept for the steps ahead.
PENDING: dict = {}


def _scenarios():
    try:
        conf = spec_dir() / "conformance"
    except Exception:
        return []
    return sorted(d for d in conf.iterdir() if (d / "top.sv").is_file())


@pytest.mark.parametrize("d", _scenarios(), ids=lambda d: d.name)
def test_scenario(d: Path):
    meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").is_file() else {}
    missing = sorted(set(meta.get("features", [])) - FEATURES)
    if missing:
        pytest.skip("needs " + ", ".join(missing))
    if d.name in PENDING:
        pytest.skip("needs " + PENDING[d.name])
    expect = json.loads((d / "expect.json").read_text())
    r = elaborate([str(d / "top.sv")], top="scenario::top",
                  incdirs=[str(d), str(d.parent / "_lib")],
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
