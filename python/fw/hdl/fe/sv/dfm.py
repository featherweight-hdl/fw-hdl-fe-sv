"""dv-flow tasks of package ``fw.hdl.fe.sv`` (flow.dv)."""
from __future__ import annotations

import json
import os
from typing import List, Tuple

from dv_flow.mgr import (FileSet, SeverityE, TaskDataInput, TaskDataResult, TaskMarker,
                         TaskRunCtxt)

_RTL = ("systemVerilogSource", "verilogSource")


def dvfm_packages():
    """Entry point ``dv_flow.mgr``: the packages this distribution provides."""
    return {"fw.hdl.fe.sv": os.path.join(os.path.dirname(os.path.abspath(__file__)), "flow.dv")}


def _inputs(input: TaskDataInput, filetypes) -> Tuple[List[str], List[str], dict]:
    files, incdirs, defines = [], [], {}
    for fs in getattr(input, "inputs", []) or []:
        if getattr(fs, "filetype", None) not in filetypes:
            continue
        files += [os.path.join(fs.basedir, f) for f in fs.files]
        incdirs += [os.path.join(fs.basedir, d) for d in getattr(fs, "incdirs", []) or []]
        for d in getattr(fs, "defines", []) or []:
            k, _, v = str(d).partition("=")
            defines[k] = v or "1"
    return list(dict.fromkeys(files)), list(dict.fromkeys(incdirs)), defines


def _rtl_filesets(input: TaskDataInput) -> list:
    """The extern RTL inputs, forwarded so a simulator downstream gets them."""
    return [fs for fs in getattr(input, "inputs", []) or []
            if getattr(fs, "filetype", None) in _RTL]


def _diag_markers(diags) -> list:
    sev = {"error": SeverityE.Error, "warning": SeverityE.Warning}
    return [TaskMarker(msg=f"{d.id}: {d.path}: {d.msg}", severity=sev.get(d.severity, SeverityE.Info))
            for d in diags]


def _error(msg: str) -> TaskMarker:
    return TaskMarker(msg=msg, severity=SeverityE.Error)


def _scan_tables(input: TaskDataInput) -> list:
    """The ``protocols`` tables of the ``fwHdlScan`` inputs (a ``Scan`` upstream)."""
    out = []
    for fs in getattr(input, "inputs", []) or []:
        if getattr(fs, "filetype", None) != "fwHdlScan":
            continue
        for f in fs.files:
            if os.path.basename(f) == "scan.json":
                with open(os.path.join(fs.basedir, f)) as fp:
                    out.append(json.load(fp).get("protocols", {}))
    return out


def _scan_check(protos: dict, scanned: dict) -> list:
    """Where the elaborated ``protocols`` disagree with a scan of the same sources.

    The scan sees every registered bundle and converter but only named
    specializations; elaboration sees what the top uses. So a protocol the
    scan lacks is fine; for one it has, the declaration must match and the
    elaborated bundles and converters must be among the scanned ones."""
    out = []
    for name, p in sorted(protos.items()):
        s = scanned.get(name)
        if s is None:
            continue
        diff = [k for k in ("family", "params", "prefix", "apis") if p.get(k) != s.get(k)]
        if not set(p.get("bundles", [])) <= set(s.get("bundles", [])):
            diff.append("bundles")
        if any(c not in s.get("converters", []) for c in p.get("converters", [])):
            diff.append("converters")
        if diff:
            out.append(_error(f"FW-E-SCAN-STALE: {name}: elaboration and the scan differ "
                              f"in {', '.join(diff)}; re-run Scan"))
    return out


async def Elaborate(ctxt: TaskRunCtxt, input: TaskDataInput) -> TaskDataResult:
    from zuspec.ir.core.serializer import IRSerializer
    from .elab import elaborate
    p = input.params
    files, incdirs, defines = _inputs(input, ("fwHdlSource",))
    rtl, rtl_inc, _ = _inputs(input, _RTL)
    if not files:
        return TaskDataResult(status=1, changed=False, output=[],
                              markers=[_error("no fwHdlSource input")])
    try:
        r = elaborate(files, p.top, incdirs=incdirs, defines=defines, rtl=rtl,
                      rtl_incdirs=rtl_inc, spec=p.spec or None,
                      domain_severity=p.domain_severity or None)
    except ValueError as e:                     # a bad domain_severity
        return TaskDataResult(status=1, changed=False, output=[], markers=[_error(str(e))])
    markers = _diag_markers(r.diags)
    if not r.ok:
        return TaskDataResult(status=1, changed=True, output=[], markers=markers)
    rundir = input.rundir
    os.makedirs(rundir, exist_ok=True)
    from .rt import ElabError
    try:
        ctx, top = r.to_ir(parameterize=p.parameterize, require_param=p.require_param)
    except ElabError as e:
        return TaskDataResult(status=1, changed=True, output=[],
                              markers=markers + _diag_markers(r.ir_diags) + [_error(str(e))])
    markers += _diag_markers(r.ir_diags)
    nl = r.netlist()
    stale = [m for t in _scan_tables(input) for m in _scan_check(nl.get("protocols", {}), t)]
    if stale:
        return TaskDataResult(status=1, changed=True, output=[], markers=markers + stale)
    with open(os.path.join(rundir, "design.zir"), "w") as f:
        f.write(IRSerializer().serialize(ctx, None))
    with open(os.path.join(rundir, "design.json"), "w") as f:
        json.dump({"ir": "design.zir", "top": top}, f, indent=2)
    with open(os.path.join(rundir, "netlist.json"), "w") as f:
        json.dump(nl, f, indent=2, sort_keys=True)
        f.write("\n")
    return TaskDataResult(status=0, changed=True, markers=markers, output=[
        FileSet(src=input.name, filetype="zuspecIR", basedir=rundir, files=["design.json", "design.zir"]),
        FileSet(src=input.name, filetype="fwHdlNetlist", basedir=rundir, files=["netlist.json"])]
        + _rtl_filesets(input))


async def Scan(ctxt: TaskRunCtxt, input: TaskDataInput) -> TaskDataResult:
    from zuspec.ir.core.serializer import IRSerializer
    from .scan import scan
    p = input.params
    files, incdirs, defines = _inputs(input, ("fwHdlSource",))
    if not files:
        return TaskDataResult(status=1, changed=False, output=[],
                              markers=[_error("no fwHdlSource input")])
    r = scan(files, incdirs=incdirs, defines=defines, spec=p.spec or None)
    markers = _diag_markers(r.diags)
    if not r.ok:
        return TaskDataResult(status=1, changed=True, output=[], markers=markers)
    ctx, ir_diags = r.to_ir()
    markers += _diag_markers(ir_diags)
    rundir = input.rundir
    os.makedirs(rundir, exist_ok=True)
    with open(os.path.join(rundir, "scan.zir"), "w") as f:
        f.write(IRSerializer().serialize(ctx, None))
    with open(os.path.join(rundir, "scan.json"), "w") as f:
        json.dump({"ir": "scan.zir", "protocols": r.table(), "classes": r.classes},
                  f, indent=2, sort_keys=True)
        f.write("\n")
    srcs = [fs for fs in getattr(input, "inputs", []) or []
            if getattr(fs, "filetype", None) == "fwHdlSource"]
    return TaskDataResult(status=0, changed=True, markers=markers, output=srcs + [
        FileSet(src=input.name, filetype="fwHdlScan", basedir=rundir,
                files=["scan.json", "scan.zir"])] + _rtl_filesets(input))


async def GenKit(ctxt: TaskRunCtxt, input: TaskDataInput) -> TaskDataResult:
    import re
    from fw.hdl.gen import GEN_VERSION, GenError, gen_kit
    from .scan import scan
    p = input.params
    files, incdirs, defines = _inputs(input, ("fwHdlSource",))
    if not files:
        return TaskDataResult(status=1, changed=False, output=[],
                              markers=[_error("no fwHdlSource input")])
    r = scan(files, incdirs=incdirs, defines=defines, spec=p.spec or None)
    markers = _diag_markers(r.diags)
    if not r.ok:
        return TaskDataResult(status=1, changed=True, output=[], markers=markers)
    gen_diags = []
    try:
        sv = gen_kit(r, pkg_name=p.pkg_name or None, diags=gen_diags)
        markers += _diag_markers(gen_diags)
    except GenError as e:
        return TaskDataResult(status=1, changed=True, output=[], markers=markers + [_error(str(e))])
    pkg = re.search(r"^package\s+(\w+)\s*;", sv, re.M).group(1)
    rundir = input.rundir
    os.makedirs(rundir, exist_ok=True)
    with open(os.path.join(rundir, f"{pkg}.sv"), "w") as f:
        f.write(sv)
    srcs = [fs for fs in getattr(input, "inputs", []) or []
            if getattr(fs, "filetype", None) == "fwHdlSource"]
    return TaskDataResult(status=0, changed=True, markers=markers,
                          memento={"gen_version": GEN_VERSION}, output=srcs + [
        FileSet(src=input.name, filetype="systemVerilogSource", basedir=rundir,
                files=[f"{pkg}.sv"])] + _rtl_filesets(input))


async def GenKitUpToDate(ctxt) -> bool:
    """``GenKit`` is stale when the generators changed since it ran
    (``GEN_VERSION``), as well as when its inputs or parameters did."""
    from fw.hdl.gen import GEN_VERSION
    return (ctxt.memento or {}).get("gen_version") == GEN_VERSION


async def EmitSV(ctxt: TaskRunCtxt, input: TaskDataInput) -> TaskDataResult:
    import zuspec.ir.core as ir
    from zuspec.ir.core.deserializer import IRDeserializer
    from .emit import emit_sv
    zirs = [fs for fs in getattr(input, "inputs", []) or []
            if getattr(fs, "filetype", None) == "zuspecIR"]
    if len(zirs) != 1:
        return TaskDataResult(status=1, changed=False, output=[],
                              markers=[_error(f"expected one zuspecIR input, got {len(zirs)}")])
    zfs = zirs[0]
    d = IRDeserializer()
    d.auto_register(ir)
    with open(os.path.join(zfs.basedir, "design.json")) as f:
        meta = json.load(f)
    with open(os.path.join(zfs.basedir, meta["ir"])) as f:
        ctx, _ = d.deserialize(f.read())
    top = meta["top"]
    rtl, _, _ = _inputs(input, _RTL)
    rundir = input.rundir
    try:
        res = emit_sv(ctx, top, rundir, extern_files=rtl,
                      top_module=input.params.top_module or None,
                      width_params=bool(getattr(input.params, "width_params", False)))
    except Exception as e:   # a pass or the back end rejected the IR
        return TaskDataResult(status=1, changed=True, output=[],
                              markers=[_error(f"{type(e).__name__}: {e}")])
    gen = [str(os.path.relpath(f, rundir)) for f in res.files]
    order = [os.path.relpath(l, rundir) for l in res.filelist.read_text().split()
             if os.path.relpath(l, rundir) in gen]
    # A type parameter width_params could not lower stays one: say so.
    kept = [TaskMarker(msg=f"type parameter '{k.type_param}' of '{k.component}' kept: "
                       f"{k.reason}", severity=SeverityE.Warning)
            for k in (res.width_params.kept if res.width_params else [])]
    return TaskDataResult(status=0, changed=True, markers=kept,
                          output=_rtl_filesets(input) + [
        FileSet(src=input.name, filetype="systemVerilogSource", basedir=rundir, files=order),
        FileSet(src=input.name, filetype="fwHdlFilelist", basedir=rundir,
                files=[os.path.basename(str(res.filelist))])])
