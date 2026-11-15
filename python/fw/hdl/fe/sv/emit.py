"""Verilog from fe-sv's IR: the Zuspec structural flattening pass, then
zuspec-be-sv (core-layer1.md §6.1, §6.5)."""
from __future__ import annotations

import dataclasses as dc
from pathlib import Path
from typing import List, Optional, Sequence

import zuspec.ir.core as ir
from zuspec.be.sv import SVGenerator
from zuspec.be.sv.structural import sv_module_name, write_filelist
from zuspec.synth.passes.struct_flatten import flatten_structure


@dc.dataclass
class EmitResult:
    files: List[Path]           # the generated Verilog
    filelist: Path              # extern RTL first, then the types package, then the
                                # generated modules, children first
    top_module: str


def emit_sv(ctxt: ir.Context, top: str, out_dir, *, extern_files: Sequence[str] = (),
            top_module: Optional[str] = None, filelist: str = "files.f") -> EmitResult:
    """Flatten ``ctxt`` in place and write one ``.sv`` per generated module
    into ``out_dir``, plus ``<top>_types_pkg.sv`` when ports use packed
    structs or enums the RTL does not declare, plus a filelist.
    ``top_module`` renames the top."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if top_module and top_module != top:
        t = ctxt.type_m.pop(top)
        t.name = top_module
        ctxt.type_m[top_module] = t
        for other in ctxt.type_m.values():
            for f in getattr(other, "fields", []):
                if isinstance(f.datatype, ir.DataTypeRef) and f.datatype.ref_name == top:
                    f.datatype.ref_name = top_module
        top = top_module
    flatten_structure(ctxt)
    # The packed structs and enums the ports use: one package, named after
    # the top (§6.5).
    pkg = f"{sv_module_name(top)}_types_pkg"
    files = [Path(f) for f in SVGenerator(out, types_package=pkg).generate(ctxt)]
    fl = write_filelist(out / filelist, files, [str(Path(f).resolve()) for f in extern_files], ctxt)
    return EmitResult(files, fl, top)
