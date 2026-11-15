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


async def Elaborate(ctxt: TaskRunCtxt, input: TaskDataInput) -> TaskDataResult:
    from zuspec.ir.core.serializer import IRSerializer
    from .elab import elaborate
    p = input.params
    files, incdirs, defines = _inputs(input, ("fwHdlSource",))
    rtl, rtl_inc, _ = _inputs(input, _RTL)
    if not files:
        return TaskDataResult(status=1, changed=False, output=[],
                              markers=[_error("no fwHdlSource input")])
    r = elaborate(files, p.top, incdirs=incdirs, defines=defines, rtl=rtl,
                  rtl_incdirs=rtl_inc, spec=p.spec or None)
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
    with open(os.path.join(rundir, "design.zir"), "w") as f:
        f.write(IRSerializer().serialize(ctx, None))
    with open(os.path.join(rundir, "design.json"), "w") as f:
        json.dump({"ir": "design.zir", "top": top}, f, indent=2)
    with open(os.path.join(rundir, "netlist.json"), "w") as f:
        json.dump(r.netlist(), f, indent=2, sort_keys=True)
        f.write("\n")
    return TaskDataResult(status=0, changed=True, markers=markers, output=[
        FileSet(src=input.name, filetype="zuspecIR", basedir=rundir, files=["design.json", "design.zir"]),
        FileSet(src=input.name, filetype="fwHdlNetlist", basedir=rundir, files=["netlist.json"])]
        + _rtl_filesets(input))


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
                      top_module=input.params.top_module or None)
    except Exception as e:   # a pass or the back end rejected the IR
        return TaskDataResult(status=1, changed=True, output=[],
                              markers=[_error(f"{type(e).__name__}: {e}")])
    gen = [str(os.path.relpath(f, rundir)) for f in res.files]
    order = [os.path.relpath(l, rundir) for l in res.filelist.read_text().split()
             if os.path.relpath(l, rundir) in gen]
    return TaskDataResult(status=0, changed=True, output=_rtl_filesets(input) + [
        FileSet(src=input.name, filetype="systemVerilogSource", basedir=rundir, files=order),
        FileSet(src=input.name, filetype="fwHdlFilelist", basedir=rundir,
                files=[os.path.basename(str(res.filelist))])])
