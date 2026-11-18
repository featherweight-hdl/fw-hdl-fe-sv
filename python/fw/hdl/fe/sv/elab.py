"""Elaborate a design written against the fw-hdl SV API, statically.

    from fw.hdl.fe.sv import elaborate
    r = elaborate(["my_pkg.sv"], top="my_pkg::chain#(bit[7:0])",
                  rtl=["rtl/fw_rv_fifo.sv"])
    if r.ok:
        ctxt, top = r.to_ir()

``top`` is the top class, as SV type text. ``rtl`` lists the RTL sources of
the extern modules, used to check pin maps against module headers (§3.7).
"""
from __future__ import annotations

import dataclasses as dc
from typing import Dict, List, Optional, Sequence

from zuspec.fe.sv import class_from_typedef
from zuspec.fe.sv import compile as sv_compile
from zuspec.fe.sv.interp import InterpError, Interpreter
from zuspec.fe.sv.module_header import HeaderError, package_types, read_header

from .api import Api
from .objects import Diag, FwComponent
from .rt import ElabError, FeIntrinsics, FeRt


class Headers:
    """Module headers read from the extern RTL, by module and parameter
    values. ``get`` returns None for a module not in the RTL given."""

    def __init__(self, files: Sequence[str], incdirs: Sequence[str] = (),
                 defines: Optional[Dict[str, str]] = None):
        self.files = list(files)
        self.incdirs = list(incdirs)
        self.defines = dict(defines or {})
        self._cache: Dict[tuple, object] = {}
        self._types: Optional[Dict[str, str]] = None
        self.errors: List[str] = []

    def declared_type(self, qual: str) -> Optional[str]:
        """If the RTL declares typedef ``qual`` (``prm_pkg::cfg_t``) in a
        package, its structural signature
        (:func:`zuspec.fe.sv.module_header.type_signature`); else None."""
        if not self.files:
            return None
        if self._types is None:
            self._types = package_types(self.files, None, self.defines, self.incdirs)
        return self._types.get(qual)

    def get(self, module: str, overrides: Dict[str, str]):
        """The header of ``module`` with parameter ``overrides`` (SV text)."""
        if not self.files:
            return None
        key = (module, tuple(sorted(overrides.items())))
        if key not in self._cache:
            try:
                self._cache[key] = read_header(self.files, module, None, self.defines,
                                               self.incdirs, overrides=overrides)
            except HeaderError as e:
                if "not found" in str(e):
                    self._cache[key] = None
                else:
                    raise
        return self._cache[key]


@dc.dataclass
class ElabResult:
    rt: Optional[FeRt]
    diags: List[Diag]
    ir_diags: List[Diag] = dc.field(default_factory=list)   # from to_ir()

    @property
    def ok(self) -> bool:
        return self.rt is not None and not any(d.severity == "error" for d in self.diags)

    @property
    def top(self) -> Optional[FwComponent]:
        return self.rt.top if self.rt else None

    def to_ir(self, parameterize: bool = True, require_param: bool = False):
        """Structural Zuspec IR: ``(Context, top type name)``.

        With ``parameterize`` (the default) each class is one IR type with
        its parameters where its structure allows (core-layer1.md §6.6);
        the components that must be specialized get an informational
        diagnostic in :attr:`ir_diags`, an error with ``require_param``
        (which then raises :class:`ElabError`). Without it, one IR type per
        specialization (M4/M5)."""
        from .ir_map import to_ir
        if not self.ok:
            raise ElabError("elaboration failed: " + "; ".join(map(str, self.diags)))
        ctxt, top, diags = to_ir(self.rt, parameterize, require_param)
        self.ir_diags = diags
        errs = [d for d in diags if d.severity == "error"]
        if errs:
            raise ElabError("parameterized emission: " + "; ".join(map(str, errs)))
        return ctxt, top

    def netlist(self) -> dict:
        from .netlist import netlist
        return netlist(self.rt)


TOP_MODULE = "__fw_fe_sv_top"


def elaborate(files: Sequence[str], top: str, *,
              incdirs: Sequence[str] = (), defines: Optional[Dict[str, str]] = None,
              rtl: Sequence[str] = (), rtl_incdirs: Sequence[str] = (),
              spec: Optional[str] = None, top_name: str = "top",
              domain_severity=None) -> ElabResult:
    """Elaborate ``top``. ``domain_severity`` overrides kinds' crossing
    severities: ``{"fw_reset_kind": {"unrelated": "warning"}}``, or the text
    ``fw_reset_kind.unrelated=warning,...``; ``"none"`` silences."""
    from zuspec.synth.passes.domains import severity_option
    sev = severity_option(domain_severity)        # ValueError on a bad option
    api = Api(spec)
    text = f"module {TOP_MODULE}; typedef {top} __fw_top_t; endmodule\n"
    cres = sv_compile(api.sources(files), incdirs=api.incdirs + list(incdirs),
                      defines=defines or {}, extra_text={"__fw_fe_sv_top.sv": text},
                      timescale="1ns/1ps")
    comp, sv_diags = cres
    if sv_diags.has_errors:
        return ElabResult(None, [Diag("FW-E-SV", "<compile>", sv_diags.report())])
    cls = class_from_typedef(comp, TOP_MODULE, "__fw_top_t")
    if cls is None or not getattr(cls, "isClass", False):
        return ElabResult(None, [Diag("FW-E-SV", "<top>", f"{top} is not a class")])
    rt = FeRt(None, comp)
    rt.extern_methods = api.extern_methods
    rt.compile_result = cres          # keeps the source manager and trees alive
    rt.headers = Headers(rtl, rtl_incdirs, defines)
    rt.interp = Interpreter(comp, FeIntrinsics(rt))
    rt.dom.severity_opt = sev
    try:
        rt.elaborate(cls, top_name)
    except (ElabError, InterpError) as e:
        rt.error("FW-E-ELAB", rt.current.path if rt.current else top_name, str(e))
    return ElabResult(rt, rt.diags)
