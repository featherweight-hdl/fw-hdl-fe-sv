"""What the kit generators read: the APIs of a scanned protocol kit.

    from fw.hdl.fe.sv.scan import scan
    from fw.hdl.gen.kit import Kit
    r = scan(["my_proto_pkg.sv"])
    ctx, _ = r.to_ir()
    kit = Kit.from_scan(ctx, r.table(), r.api_packages(), r.bundles.values())

Each API is listed once, by its generic name (``test_put_if``), with the
protocols that use it. Each protocol bundle of channels is listed with its
channels (``fw.hdl.fe.sv.channels``). The kit's core package is the package its APIs and
payload types are declared in; the generated package imports it.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Dict, Iterable, List, Optional, Tuple

import zuspec.ir.core as ir

from fw.hdl.fe.sv.channels import BundleChannels
from fw.hdl.fe.sv.objects import Diag
from fw.hdl.fe.sv.types import STD_PACKAGE

from . import irb

#: Packages that are never a kit's core package.
_NOT_CORE = ("fw_hdl_pkg", "std")


class GenError(Exception):
    """A kit the generators cannot handle; ``code`` is the diagnostic id."""

    def __init__(self, code: str, msg: str):
        super().__init__(f"{code}: {msg}")
        self.code = code


@dc.dataclass
class KitApi:
    name: str                               # the generic name: test_put_if
    tmpl: List[ir.TemplateParam]            # its parameters, or []
    proto: ir.DataTypeProtocol              # the API, written over tmpl
    ref: ir.DataType                        # the API type over its own params
    protocols: List[Tuple[str, str]] = dc.field(default_factory=list)  # (protocol, dir)
    derived: bool = False                   # derived by the kit (chan_api), not scanned


@dc.dataclass
class Kit:
    pkg: str
    apis: List[KitApi]
    bundles: List[BundleChannels] = dc.field(default_factory=list)  # with channels
    #: Each struct channel payload of a bundle, by bundle prefix: (the
    #: payload type as the channel names it, its struct), each once.
    structs: Dict[str, List[Tuple[ir.DataTypeRef, ir.DataTypeStruct]]] = \
        dc.field(default_factory=dict)
    #: warnings: what the kit leaves out, and why
    diags: List[Diag] = dc.field(default_factory=list)

    @classmethod
    def from_scan(cls, ctx: ir.Context, table: Dict,
                  pkgs: Iterable[str] = (),
                  bundles: Iterable[BundleChannels] = (),
                  free_apis: Iterable[str] = (),
                  homes: Optional[Dict[str, str]] = None,
                  protos: Optional[Dict[str, Optional[str]]] = None) -> "Kit":
        """``ctx`` and ``table`` as ``ScanResult.to_ir`` and ``table`` give
        them; ``pkgs`` the packages the APIs are declared in
        (``ScanResult.api_packages``); ``bundles`` the protocol bundles
        (``ScanResult.bundles``, filled by ``to_ir``); ``free_apis`` the
        API classes the package declares (``ScanResult.apis``), which get
        collateral even when no protocol uses them; ``homes`` each API's
        package (``ScanResult.api_homes``): an API of another package
        (``fw_rv_proto``'s ``fw_put_if``, from std) has its collateral
        in that package's kit; ``protos`` each protocol and bundle class
        -> its package, None if in none (``ScanResult.proto_packages``).

        A channel payload typedef declared in a class is
        ``FW-E-GEN-SCOPE``; a struct payload with a 4-state member is
        warning ``FW-W-GEN-4STATE`` (in ``diags``)."""
        pkg = _core_pkg(ctx, pkgs, protos or {})
        apis: Dict[str, KitApi] = {}
        for pname, rec in table.items():
            for a in rec.get("apis", []):
                gname = a["type"].split("#", 1)[0]
                if homes and homes.get(gname, pkg) != pkg:
                    continue
                if gname not in apis:
                    apis[gname] = _api(ctx, gname)
                apis[gname].protocols.append((pname, a["dir"]))
        for gname in free_apis:
            if gname not in apis:
                apis[gname] = _api(ctx, gname)
        bundles = [b for b in bundles if b.channels]
        diags = []
        for b in bundles:
            diags.extend(_check_payloads(b))
        out = list(apis.values())
        for b in bundles:
            out.extend(_chan_apis(b))
        return cls(pkg, out, bundles,
                   {b.prefix: _structs(ctx, b) for b in bundles}, diags)


def _check_payloads(b: BundleChannels) -> List[Diag]:
    out = []
    for c in b.channels:
        tname = getattr(c.payload, "ref_name", "?")
        if c.local_to:
            raise GenError("FW-E-GEN-SCOPE",
                           f"bundle '{b.name}' channel '{c.name}': payload type '{tname}' is "
                           f"declared inside class '{c.local_to}'; declare a channel's "
                           f"payload type at package level (a typedef in the kit's package)")
        if c.four_state_struct:
            out.append(Diag("FW-W-GEN-4STATE", f"{b.name}.{c.name}",
                            f"payload struct '{tname}' has a 4-state (logic) member: GenKit "
                            f"generates no {b.prefix}_payloads helpers for it and drives it as "
                            f"one pin, not a pin per field. Use bit members", "warning"))
    return out


def _structs(ctx: ir.Context, b: BundleChannels):
    out, seen = [], set()
    for c in b.channels:
        t = c.payload
        if c.four_state_struct:
            # The IR has the struct (with its 4-state members), but class
            # emission does not use 4-state: no helpers (FW-W-GEN-4STATE).
            continue
        if isinstance(t, ir.DataTypeRef) and t.ref_name not in seen:
            st = ctx.type_m.get(t.ref_name)
            if isinstance(st, ir.DataTypeStruct):
                seen.add(t.ref_name)
                out.append((t, st))
    return out


def _chan_apis(b: BundleChannels) -> List[KitApi]:
    """``<prefix>_req_if`` (the fwd channels) and ``<prefix>_rsp_if`` (the
    rev ones) of a bundle of channels: a task ``<channel>(input <payload>
    t)`` per channel, over the bundle's parameters."""
    out = []
    for suffix, fwd in (("_req_if", True), ("_rsp_if", False)):
        chans = [c for c in b.channels if c.fwd == fwd]
        if not chans:
            continue
        proto = ir.DataTypeProtocol(methods=[
            irb.method(c.name, [irb.arg("t", c.payload)], task=True) for c in chans])
        t = irb.template(b.prefix + suffix, b.tmpl, proto)
        out.append(KitApi(b.prefix + suffix, list(b.tmpl), proto, irb.self_ref(t),
                          derived=True))
    return out


def _api(ctx: ir.Context, name: str) -> KitApi:
    t = ctx.type_m.get(name)
    if isinstance(t, ir.DataTypeParameterized) and isinstance(t.body, ir.DataTypeProtocol):
        ref = irb.self_ref(t)
        return KitApi(name, list(t.template_params), t.body, ref)
    if isinstance(t, ir.DataTypeProtocol):
        return KitApi(name, [], t, ir.DataTypeRef(ref_name=name))
    raise GenError("FW-E-GEN-API", f"API '{name}' is not in the scanned IR")


def _core_pkg(ctx: ir.Context, pkgs: Iterable[str],
              protos: Dict[str, Optional[str]]) -> str:
    # A key's package is before "::" in its name, not in its parameters
    # (``fw_put_if#(scenario::rsp_t)``).
    heads = (k.split("#(", 1)[0] for k in ctx.type_m)
    found = {h.split("::", 1)[0] for h in heads if "::" in h}
    found.update(p for p in pkgs if p)
    found.update(p for p in protos.values() if p)
    found.difference_update(_NOT_CORE)
    unit = sorted(n for n, p in protos.items() if p is None)
    if unit:
        # A protocol outside any package: its kit has no package to import,
        # and must not be taken for std's.
        raise GenError("FW-E-GEN-PKG",
                       f"protocol or bundle {', '.join(repr(n) for n in unit)} is declared "
                       f"outside a package; declare a kit's protocols, bundles, APIs and "
                       f"payload types in one package")
    if len(found) > 1:                  # std is the core only of its own kit
        found.discard(STD_PACKAGE)
    if not found:
        raise GenError("FW-E-GEN-PKG",
                       "the scan found no protocol, bundle, API or payload type declared "
                       "in a package other than fw_hdl_pkg; declare the kit's protocol in "
                       "a package")
    if len(found) > 1:
        raise GenError("FW-E-GEN-PKG",
                       "a kit's protocols, bundles, APIs and payload types must be in one "
                       "package; found several: " + ", ".join(sorted(found)))
    return found.pop()
