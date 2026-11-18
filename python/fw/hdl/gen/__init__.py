"""Kit-stage generators: the classes a protocol kit derives from its APIs
(payload helpers, pin-map presets, channel APIs, bases, listeners,
proxies, imps, transactions, transactors), emitted as one SV package.

    from fw.hdl.fe.sv.scan import scan
    from fw.hdl.gen import gen_kit
    sv = gen_kit(scan(["my_proto_pkg.sv"]))   # package my_proto_pkg_gen_pkg

Each generator is a module with ``decls(api: KitApi, kit: Kit) ->
List[DataType]`` (per API), ``kit_decls(kit: Kit) -> List[DataType]``
(once per kit; emitted before the per-API ones) or ``kit_post_decls(kit)``
(once per kit; emitted after the per-API ones -- ``xtor``'s transactors
reference the derived channel APIs and need them already declared);
``GENERATORS`` lists them in emission order.
"""
from __future__ import annotations

import hashlib
import pathlib
import warnings
from typing import List, Optional

from fw.hdl.fe.sv.objects import Diag
from fw.hdl.fe.sv.types import STD_PACKAGE

from . import base, chan_api, imp, listener, payloads, pins, proxy, txn, xtor
from .emit import to_sv
from .kit import GenError, Kit, KitApi
from .tb import gen_tb
from .bind import gen_bind

#: The generators, in emission order.
GENERATORS: List = [payloads, pins, chan_api, base, listener, proxy, imp, txn, xtor]


def _version() -> str:
    h = hashlib.sha256()
    for p in sorted(pathlib.Path(__file__).parent.glob("*.py")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


#: A hash of the generators' sources: a generated kit older than it is stale.
GEN_VERSION = _version()


def gen_kit(result, pkg_name: Optional[str] = None, generators=None,
            diags: Optional[list] = None) -> str:
    """The generated package for a scanned kit (a ``ScanResult``), as SV
    text. The package is ``<core pkg>_gen_pkg`` unless ``pkg_name`` is
    given; it imports ``fw_hdl_pkg`` and the core package. ``generators``
    (default ``GENERATORS``) selects the generators to run.

    Warnings (``Diag``) about what the package leaves out -- types that do
    not map to IR (``FW-W-SCAN-IR``), a parameterized bundle the source
    never specializes (``FW-W-GEN-UNSPEC``), a 4-state struct payload
    (``FW-W-GEN-4STATE``) -- are appended to ``diags``; with no list, they
    are issued as Python warnings."""
    ctx, ir_diags = result.to_ir()
    kit = Kit.from_scan(ctx, result.table(), result.api_packages(),
                        result.bundles.values(), getattr(result, "apis", {}).keys(),
                        result.api_homes() if hasattr(result, "api_homes") else None,
                        result.proto_packages() if hasattr(result, "proto_packages") else None)
    found = list(ir_diags) + kit.diags
    for b in (result.unspecialized_bundles() if hasattr(result, "unspecialized_bundles") else []):
        found.append(Diag("FW-W-GEN-UNSPEC", b,
                          f"bundle '{b}' is parameterized and the scanned source names no "
                          f"specialization of it, so it gets no channel APIs and no "
                          f"transactors. Name one in the kit's package, for example "
                          f"'typedef {b} #(...) {b}_t;'", "warning"))
    if diags is not None:
        diags.extend(found)
    else:
        for d in found:
            warnings.warn(str(d), stacklevel=2)
    gens = GENERATORS if generators is None else generators
    decls = []
    for g in gens:
        if hasattr(g, "kit_decls"):
            decls.extend(g.kit_decls(kit))
    for api in kit.apis:
        for g in gens:
            if hasattr(g, "decls"):
                decls.extend(g.decls(api, kit))
    post_start = len(decls)
    for g in gens:
        if hasattr(g, "kit_post_decls"):
            decls.extend(g.kit_post_decls(kit))
    # xtor's transactors use rt-sv/std runtime classes (fw_rv_initiator,
    # fw_put_if, ...), so the package needs fw_hdl_std_pkg too, unless the
    # kit's own package already is std.
    imports = ["fw_hdl_pkg", kit.pkg]
    if len(decls) > post_start and kit.pkg != STD_PACKAGE:
        imports.append(STD_PACKAGE)
    sv = to_sv(pkg_name or f"{kit.pkg}_gen_pkg", decls, imports)
    # The transactors register with fw-hdl macros: a file of its own (a
    # compilation unit under slang) must include them.
    if "`fw_" in sv:
        sv = '`include "fw_hdl_macros.svh"\n\n' + sv
    return sv


__all__ = ["GEN_VERSION", "GENERATORS", "GenError", "Kit", "KitApi", "gen_bind", "gen_kit", "gen_tb", "to_sv"]
