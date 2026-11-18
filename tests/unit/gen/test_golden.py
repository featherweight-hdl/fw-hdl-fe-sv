"""Each generator's output against its golden
(``golden/<scenario>/<gen>.sv``; regenerate with ``FW_HDL_GEN_REGEN=1``).
Each golden lints in Verilator with the kit core, its classes specialized
for every API type the scenario's protocols use, and every channel API
the kit derives (at its parameters' defaults)."""
import os
import re
import shutil
import subprocess
import types
from pathlib import Path

import pytest

from fw.hdl.fe.sv.scan import scan
from fw.hdl.gen import Kit, base, chan_api, gen_kit, imp, listener, payloads, pins, proxy, txn

PKGS = Path(__file__).resolve().parents[4]
CONF = PKGS / "fw-hdl-spec" / "conformance"
RT = PKGS / "fw-hdl-rt-sv" / "sv"
STD = PKGS / "fw-hdl-spec" / "std"
SRC = Path(__file__).parent / "src"
GOLDEN = Path(__file__).parent / "golden"
SCENARIOS = ["proto_abs", "proto_param", "proto_chan"]

#: Generator -> the names of the classes the lint extends and constructs,
#: for an API (an interface class is elaborated through the classes that
#: implement it).
GENS = {"chan_api": (chan_api, [base.base_name]),
        "base": (base, [base.base_name]),
        "listener": (listener, [listener.listener_base_name, listener.fanout_name]),
        "proxy": (proxy, [proxy.proxy_name]),
        "imp": (imp, [imp.imp_name]),
        "payloads": (payloads, []),
        "pins": (pins, []),
        "txn": (txn, [txn.txn_listener_name, txn.txn_driver_name])}
#: Generator -> the generators whose goldens its golden needs to lint, in
#: package order ("" is the generator's own). Every golden of a kit with
#: channel APIs needs their declarations (chan_api) first.
DEPS = {"chan_api": ["", "base"], "proxy": ["chan_api", "listener", ""],
        "imp": ["chan_api", "base", ""], "payloads": [""], "pins": [""],
        "txn": ["chan_api", "listener", ""]}
#: Class name -> (the extra parameter after the API's, its name (named
#: when the API's parameters keep their defaults), given the API's
#: specialization; the arguments its ``new`` needs).
EXTRA = {imp.imp_name: (lambda g, spec: base.base_name(_Api(g)) + spec, "IMP", "null")}
#: Class name -> the arguments its ``new`` needs (no extra parameter).
CTOR = {txn.txn_listener_name: "null", txn.txn_driver_name: "null"}


def _dir(name):
    return SRC / name if (SRC / name).is_dir() else CONF / name


def _scan(name):
    d = _dir(name)
    r = scan([str(d / "top.sv")], incdirs=[str(d), str(CONF / "_lib")])
    assert r.ok, r.diags
    return r


def _api_types(r):
    """``(generic name, SV parameter list)`` of each API type used, then
    each channel API the kit derives (``None``: its parameters' defaults)."""
    out = []
    homes = r.api_homes()
    for rec in r.table().values():
        for a in rec.get("apis", []):
            m = re.match(r"(\w+)(?:#\((.*)\))?$", a["type"])
            if homes.get(m.group(1)) == "fw_hdl_std_pkg":     # std's own kit
                continue
            if m.groups() not in out:
                out.append(m.groups())
    return out + [(a.name, None) for a in _kit(r).apis if a.derived]


def _kit(r):
    ctx, _ = r.to_ir()
    return Kit.from_scan(ctx, r.table(), r.api_packages(), r.bundles.values())


def _payload_calls(r):
    """Calls of each payload class's functions (``payloads``)."""
    lines = []
    for i, (prefix, structs) in enumerate(_kit(r).structs.items()):
        for j, (t, _) in enumerate(structs):
            c, n, v = payloads.payloads_name(prefix), payloads.short_name(t), f"p_{i}_{j}"
            lines += [f"  initial begin {t.ref_name} {v}; string d;",
                      f"    {v} = {c}::{n}_mask({v});",
                      f"    if (!{c}::{n}_compare({v}, {v}, d)) $display(\"%s\", d);",
                      f"    $display(\"%s\", {c}::{n}_sprint({v})); end"]
    return lines


def _pin_calls(r):
    """A call of each bundle's pin-map preset (``pins``)."""
    return [f"  initial begin fw_hdl_pkg::fw_extern_map m; fw_hdl_pkg::fw_object b;"
            f" void'({pins.pins_name(b.prefix)}::flat(m, b, \"x_\")); end"
            for b in _kit(r).bundles]


def _txn_calls(r):
    """Construct, randomize, compare and print each method's transaction
    (``txn``)."""
    apis = {a.name: a for a in _kit(r).apis}
    lines = []
    for i, (g, args) in enumerate(_api_types(r)):
        spec = f" #({args})" if args else ""
        for j, fn in enumerate(apis[g].proto.methods):
            v = f"t_{i}_{j}"
            lines += [f"  initial begin {txn.method_txn_name(apis[g], fn.name)}{spec} {v};"
                      f" string d;",
                      f"    {v} = new; void'({v}.randomize());",
                      f"    if (!{v}.compare({v}, d)) $display(\"%s\", d);",
                      f"    $display(\"%s\", {v}.sprint()); end"]
    return lines


def _checker(r, cls_names, gen) -> str:
    """A module that extends and constructs each specialization, so
    Verilator elaborates the generated classes."""
    lines = ["module gen_chk;", "  import scenario::*;", "  import scenario_gen_pkg::*;"]
    i = 0
    for g, args in _api_types(r):
        spec = f" #({args})" if args else ""
        for cls_name in cls_names:
            cspec, ctor = spec, ""
            if cls_name in EXTRA:
                extra, pname, cargs = EXTRA[cls_name]
                cspec = (f" #({args}, {extra(g, spec)})" if args
                         else f" #(.{pname}({extra(g, spec)}))")
                ctor = f" function new(); super.new({cargs}); endfunction"
            elif cls_name in CTOR:
                ctor = f" function new(); super.new({CTOR[cls_name]}); endfunction"
            lines += [f"  class chk_{i} extends {cls_name(_Api(g))}{cspec};{ctor} endclass",
                      f"  initial begin chk_{i} c; c = new; end"]
            i += 1
    if gen == "payloads":
        lines += _payload_calls(r)
    if gen == "pins":
        lines += _pin_calls(r)
    if gen == "txn":
        lines += _txn_calls(r)
    return "\n".join(lines + ["endmodule", ""])


def _Api(name):
    return types.SimpleNamespace(name=name)


@pytest.mark.parametrize("gen", sorted(GENS))
@pytest.mark.parametrize("name", SCENARIOS)
def test_golden(name, gen, tmp_path):
    mod, cls_names = GENS[gen]
    r = _scan(name)
    sv = gen_kit(r, generators=[mod])
    golden = GOLDEN / name / f"{gen}.sv"
    if os.environ.get("FW_HDL_GEN_REGEN"):
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(sv)
    assert sv == golden.read_text()

    if shutil.which("verilator") is None:
        pytest.skip("verilator not on PATH")
    d = _dir(name)
    chk = tmp_path / "gen_chk.sv"
    chk.write_text(_checker(r, cls_names, gen))
    # fw_hdl_pkg and the scenario have -Wall warnings of their own: only
    # the golden and the checker must have none. Verilator reports every
    # ``output T`` argument as UNDRIVEN, even in a pure virtual prototype
    # (the scenario's own test_apis.svh); in SV it starts at T's default.
    # A channel API keeps all its bundle's parameters, used or not
    # (UNUSEDPARAM).
    cmd = ["verilator", "--lint-only", "-Wall", "-Wno-DECLFILENAME", "-Wno-UNUSEDSIGNAL",
           "-Wno-UNDRIVEN", "-Wno-UNUSEDPARAM", "-Wno-MULTITOP", "-Wno-fatal",
           f"-I{RT}", f"-I{PKGS / 'fw-hdl-spec' / 'bindings' / 'sv'}", f"-I{STD}",
           f"-I{d}", f"-I{CONF / '_lib'}",
           str(RT / "fw_hdl_pkg.sv"), str(STD / "fw_hdl_std_pkg.sv"), str(d / "top.sv")]
    # One package per generator in the lint (each golden is a whole
    # ``scenario_gen_pkg``): the dependencies' decls are spliced in.
    deps = DEPS.get(gen, ["chan_api", ""])
    if deps != [""]:
        body = lambda t: t.split("import scenario::*;\n", 1)[1].rsplit("endpackage", 1)[0]
        merged = sv.split("import scenario::*;\n", 1)[0] + "import scenario::*;\n"
        for dep in deps:
            merged += body(sv if not dep else (GOLDEN / name / f"{dep}.sv").read_text())
        merged += "endpackage : scenario_gen_pkg\n"
        golden = tmp_path / f"{gen}.sv"
        golden.write_text(merged)
    cmd += [str(golden), str(chk)]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    where = re.findall(r"^%\w+(?:-\w+)?: ([^:\s]+):", res.stderr, re.M)
    bad = [w for w in where if w in (str(golden), str(chk))]
    assert not bad, res.stderr
