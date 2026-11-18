"""``gen_tb``: the testbench of an elaborated design (K10.2).

    r = elaborate(["my_pkg.sv"], top="my_pkg::chain#(bit[7:0])", rtl=[...])
    s = scan([std_pkg_sv], incdirs=[...])        # the kits whose xtors to use
    sv = gen_tb(r, s, top_module="chain")

returns the text of one file with

- package ``<top>_tb_pkg`` holding class ``<top>_tb_c`` (an fw component):
  per protocol port of the DUT, a pin bundle of the opposite mixin named
  as the port (``in``) and the opposite-role transactor ``dut_<port>``
  (DUT target port -> initiator; DUT initiator port -> target), its pins
  connected up to the bundle; a clock domain ``clk`` and a reset domain
  ``rst`` driven by the input ``rst_i``. A target transactor whose method
  port calls ``fw_put_if #(T)`` is bound to an ``fw_inbox #(T)``
  ``dut_<port>_rx`` (the test ``get``\\ s from it); any other method port is
  left to a subclass's ``connect()``. With ``bind=True`` a monitor per port
  (K10.3, fed by ``gen_bind``'s bound module); ``build``/``connect`` are
  split per concern (domains, xtors, monitors).
- module ``<top>_tb #(type TB = <top>_tb_c)``: the DUT, one ``fw_pin_bridge``
  per class signal, the clock and reset, the class elaborated as ``top``.
  The class clock is the module's clock through a clock bridge, which also
  starts the run() tasks (the kernel's own edge loop is not used). The run
  lasts ``+fw_edges=N`` clock edges (default 100); diagnostics print as
  ``D <id> <path>``.

Each protocol port is walked once (``protocols.walk_port``, shared with
``gen_bind``): a bundle may hold bundles (a bundle of channels). The
transactor and monitor are those of the outermost level carrying a
protocol, of the opposite role; with none in the scan, ``FW-E-TB-XTOR``
(``FW-E-TB-MON``) names the protocol, the role and the port, and the inner
channels' transactors are never used instead. Every leaf signal gets one
bridge, in its direction for the DUT (reversed at each ``fw_mirror`` on its
path). The class package imports the packages of the level's bundle class,
the transactors and the payload types; the module qualifies payload types
(``sbus_pkg::sbus_req_t``). A transactor's pins are its pin member
(``protocols.xtor_pins``: ``pins``, std's monitor ``p``).

The DUT's clock and reset ports are the drivers of its clock and reset
domains (IR ``domain_drivers``); one clock and at most one reset are
supported. Other plain DUT inputs are tied to 0. Port names follow the
emitter's: the member path joined with ``_``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from fw.hdl.fe.sv.objects import FwBundle, FwSignal
from fw.hdl.fe.sv.protocols import ROLES, PortWalk, ProtoLevel, walk_port, xtor_pins, xtor_role
from fw.hdl.fe.sv import types as ty

from .kit import GenError

#: the mixin of the testbench's pin bundle for each DUT bundle mixin
_TB_MIXIN = {"mirror": "fw_bundle", "bundle": "fw_mirror"}


def _targ(type_name: str) -> str:
    """``fw_output#(bit[7:0])`` -> ``bit[7:0]``."""
    return type_name[type_name.index("#(") + 2:-1]


def _generics(scan):
    """Each generic class of the scanned packages: name -> (pkg, symbol)."""
    out = {}
    for pkg in scan.rt.comp.getPackages():
        for m in pkg:
            if type(m).__name__ == "GenericClassDefSymbol":
                out.setdefault(m.name, (pkg.name, m, pkg))
    return out


def _find_xtor(scan, lvl: ProtoLevel, kind: Optional[str] = None) -> Optional[Tuple[str, Any]]:
    """The transactor for protocol level ``lvl`` of a DUT port, and its
    class: the scan's lookup when it saw a specialization; else a generic
    class of the scanned packages whose default specialization is a
    transactor of the level's protocol family and the opposite role,
    specialized with the protocol's parameters (a guess: a transactor takes
    its protocol's parameters, in order). ``kind`` ``"monitor"`` looks up a
    monitor of the protocol instead."""
    kind = kind or lvl.kind
    b = lvl.bundle
    x = scan.xtor_for(b.btype_name, kind)
    if x is not None:
        return x, scan.xtor_class(x)
    want = {"mirror": "initiator", "bundle": "target", "monitor": "monitor"}[kind]
    proto = scan.rt.proto
    for name, (_, g, scope) in _generics(scan).items():
        try:
            # a property whose getter takes the scope (pyslang binding)
            spec = ty.canon(type(g).defaultSpecialization.fget(g, scope))
        except Exception:       # pragma: no cover
            continue
        if spec is None or not ty.find_member(spec, "__fw_xtor_protocol")[0]:
            continue
        try:
            rec = proto.rec_of(proto._static(spec, "__fw_xtor_protocol"))
        except Exception:       # pragma: no cover - a class the interpreter rejects
            continue
        if rec is not None and rec.family == lvl.proto.family and \
                ROLES[xtor_role(spec)] == want:
            return f"{name}#({','.join(lvl.proto.params.values())})", spec
    return None


def _xtor_of(scan, w: PortWalk, kind: Optional[str] = None) -> Tuple[str, str, Any]:
    """The transactor (``kind`` ``"monitor"``: the monitor) of DUT port
    ``w``: at its outermost protocol level, of the opposite role. Returns
    ``(type, pin member, class)``; a GenError names the protocol, the role
    and the port when the scan has none. Never the inner channels'."""
    lvl = w.levels[0]
    k = kind or lvl.kind
    role = {"mirror": "initiator", "bundle": "target", "monitor": "monitor"}[k]
    found = _find_xtor(scan, lvl, k)
    if found is None:
        code = "FW-E-TB-MON" if k == "monitor" else "FW-E-TB-XTOR"
        raise GenError(code, f"{w.port.path}: protocol {lvl.proto.name} has no {role} "
                       f"{'monitor' if k == 'monitor' else 'transactor'} in the scan "
                       f"(port {w.name}, a {w.port.kind} of {lvl.bundle.btype_name})")
    x, cls = found
    pins = xtor_pins(cls)[1] if cls is not None else None
    return x, pins or "pins", cls


def _ports(r) -> Tuple[List[FwSignal], List[PortWalk]]:
    """The DUT's plain ports, and the walk of each protocol port."""
    sigs, ports = [], []
    for ch in sorted(r.top.children.values(), key=lambda o: o.seq):
        if isinstance(ch, FwBundle) and ch.kind in _TB_MIXIN:
            w = walk_port(ch, r.top)
            if ch.proto is not None:
                ports.append(w)
            elif w.levels:
                # a port is one protocol: a bundle without one that groups
                # protocol bundles is not a port gen_tb drives
                inner = ", ".join(f"{'.'.join(l.rel)} ({l.proto.name})" for l in w.levels)
                raise GenError("FW-E-TB-NOPROTO",
                               f"{ch.path}: port {w.name} ({ch.btype_name}) has no protocol, "
                               f"but its members {inner} do; give the port's bundle a "
                               f"protocol (`fw_bundle_protocol`) or make each a port")
        elif isinstance(ch, FwSignal) and ch.kind == "port":
            sigs.append(ch)
    return sigs, ports


def _imports(scan, w: PortWalk, *classes) -> List[str]:
    """The packages a port's testbench code names: the protocol level's
    bundle class, the transactor classes, and its payload types'."""
    known = {p.name for p in scan.rt.comp.getPackages()}
    out = [ty.package_of(w.levels[0].bundle.btype)]
    out += [ty.package_of(c) for c in classes if c is not None]
    out += [q.split("::", 1)[0] for q in (ty.qual_type_name(p.sig.dtype) for p in w.pins)
            if "::" in q]
    return [p for p in out if p in known]


def _vtype(width: int) -> str:
    return "logic" if width == 1 else f"logic [{width - 1}:0]"


def gen_tb(r, scan, top_module: Optional[str] = None, *, clk_half: int = 5,
           rst_cycles: int = 3, bind: bool = False) -> str:
    """The testbench SV of elaborated design ``r`` (an ``ElabResult``) whose
    emitted module is ``top_module`` (default: the IR top's name), with
    transactors looked up in ``scan`` (a ``ScanResult`` of the kits).
    ``clk_half`` is the half period in time units; reset is held for
    ``rst_cycles`` edges. Call before ``emit_sv`` (which flattens the IR)
    or on a fresh ``to_ir()``.

    ``bind`` adds, per protocol port, a monitor pin bundle ``mon_<port>_pins``
    (an ``fw_monitor``: all inputs) and the protocol's monitor ``mon_<port>``
    tapping it (``build_monitors``/``connect_monitors``); their pins are fed
    from the DUT's nets by the bind module of ``gen_bind`` (K10.3), not by
    this module."""
    ctxt, top = r.to_ir()
    t = ctxt.type_m[top]
    dut = top_module or top
    pfx = dut

    clocks = [d for d in t.domain_drivers if _dkind(t, d) == "fw_clock_kind"]
    resets = [d for d in t.domain_drivers if _dkind(t, d) == "fw_reset_kind"]
    if len(clocks) != 1 or len(resets) > 1:
        raise GenError("FW-E-TB-DOMAINS", f"{top}: gen_tb supports one clock and at most one reset "
                       f"(has {len(clocks)} clock and {len(resets)} reset drivers)")
    clk_pin = "_".join(clocks[0].signal)
    rst_pin = "_".join(resets[0].signal) if resets else None
    rst_low = bool(resets and resets[0].attr.get("active_low") is not None
                   and resets[0].attr["active_low"].value)

    sigs, bundles = _ports(r)
    gen = _generics(scan)
    pkgs: List[str] = []

    def need(type_str: str) -> None:
        g = type_str.split("#(", 1)[0]
        if g in gen and gen[g][0] not in pkgs and gen[g][0] != "fw_hdl_pkg":
            pkgs.append(gen[g][0])

    # -- class ---------------------------------------------------------------
    decl, build, conn_pins, conn_x = [], [], [], []
    bridges, nets = [], []
    dut_conns: List[str] = []
    mdecl, mbuild, mconn = [], [], []
    for w in bundles:
        lvl = w.levels[0]
        port = w.name
        if bind:
            m, mpins, mcls = _xtor_of(scan, w, "monitor")
            need(m)
            pkgs.extend(_imports(scan, w, mcls))
            mt = f"fw_monitor #({lvl.bundle.btype_name})"
            mdecl += [f"        {mt} mon_{port}_pins;", f"        {m} mon_{port};"]
            mbuild += [f"            mon_{port}_pins = {mt}::create(\"mon_{port}_pins\", this);",
                       f"            mon_{port} = {m}::create(\"mon_{port}\", this);"]
            mconn.append(f"            mon_{port}.{mpins}.connect(mon_{port}_pins);")
    for w in bundles:
        lvl = w.levels[0]
        x, xpins, xcls = _xtor_of(scan, w)
        port = w.name
        btype = f"{_TB_MIXIN[lvl.kind]} #({lvl.bundle.btype_name})"
        need(lvl.bundle.btype_name)
        need(x)
        pkgs.extend(_imports(scan, w, xcls))
        xn = f"dut_{port}"
        decl += [f"        {btype} {port};", f"        {x} {xn};"]
        build += [f"            {port} = {btype}::create(\"{port}\", this);",
                  f"            {xn} = {x}::create(\"{xn}\", this);"]
        conn_pins.append(f"            {xn}.{xpins}.connect_up({port});")
        api = next(iter(lvl.proto.apis.values()), None) if lvl.kind == "bundle" else None
        if api is not None and api.name.startswith("fw_put_if#("):
            pt = _targ(api.name)
            decl.append(f"        fw_inbox #({pt}) {xn}_rx;")
            decl.append(f"        fw_export #({api.name}) {xn}_e;")
            build += [f"            {xn}_rx = new();",
                      f"            {xn}_e = fw_export #({api.name})::create(\"{xn}_e\", this);",
                      f"            {xn}_e.set_imp({xn}_rx);"]
            conn_x.append(f"            {xn}.p.connect({xn}_e);")
        elif lvl.kind == "bundle":
            conn_x.append(f"            // {xn}: bind its method port in a subclass's connect()")
        # pins: one bridge per leaf; the tb drives what the DUT takes in
        for p in w.pins:
            leaf = p.name
            path = ".".join(["top"] + p.path)
            st = ty.qual_type_name(p.sig.dtype)
            nets.append(f"    {_vtype(p.sig.width)} {leaf};")
            if p.dir != "out":
                bridges.append(f"    fw_pin_bridge #({st}, \"{path}\") b_{leaf}(.i('0), .o({leaf}));")
            else:
                bridges.append(f"    fw_pin_bridge #({st}, \"{path}\") b_{leaf}(.i({leaf}), .o());")
            dut_conns.append(f".{leaf}({leaf})")

    plain = []
    for s in sigs:
        n = "_".join(s.rel_path(r.top))
        if n == clk_pin:
            dut_conns.append(f".{n}(clk)")
        elif n == rst_pin:
            dut_conns.append(f".{n}({'~rst' if rst_low else 'rst'})")
        elif s.dir == "in":
            dut_conns.append(f".{n}('0)")
            plain.append(n)
        else:
            nets.append(f"    {_vtype(s.width)} {n}_unused;")
            dut_conns.append(f".{n}({n}_unused)")

    imports = ["fw_hdl_pkg"] + [p for p in ["fw_hdl_std_pkg"] + pkgs
                                if p != "fw_hdl_pkg"]
    imports = list(dict.fromkeys(imports))
    L = ['`include "fw_hdl_macros.svh"', "",
         f"// Generated by fw.hdl.gen.tb for {dut}. Do not edit.",
         f"package {pfx}_tb_pkg;"]
    L += [f"    import {p}::*;" for p in imports]
    L += ["",
          f"    // The testbench of {dut}: one transactor per protocol port, its",
          "    // pins the class's top-level signals (bridged to the DUT).",
          f"    class {pfx}_tb_c extends fw_component;",
          f"        `fw_component_utils({pfx}_tb_c)",
          "        fw_clock_domain clk;",
          "        fw_input #(bit) rst_i;",
          "        fw_reset_domain rst;"]
    L += decl + mdecl
    L += ["",
          "        virtual function void build();",
          "            build_domains();",
          "            build_xtors();"]
    if bind:
        L.append("            build_monitors();")
    L += ["        endfunction",
          "",
          "        // The clock is the module's (through a clock bridge); the reset",
          "        // follows the input rst_i, active high.",
          "        virtual function void build_domains();",
          f"            clk = fw_clock_domain::create(\"clk\", this, fw_clock_kind::root({2 * clk_half}ns));",
          "            rst_i = fw_input #(bit)::create(\"rst_i\", this);",
          "            rst = fw_reset_domain::create(\"rst\", this, fw_reset_kind::root(.clock(clk)));",
          "            fw_clock_domain::set(this, clk);",
          "            fw_reset_domain::set(this, rst);",
          "        endfunction",
          "",
          "        virtual function void build_xtors();"]
    L += build
    L += ["        endfunction",
          "",
          "        virtual function void connect();",
          "            rst.drive(rst_i);",
          "            connect_xtors();"]
    if bind:
        L.append("            connect_monitors();")
    L += ["        endfunction",
          "",
          "        virtual function void connect_xtors();"]
    L += conn_pins + conn_x
    L += ["        endfunction"]
    if bind:
        L += ["",
              "        // The monitors: their pins are the DUT's nets, through the",
              f"        // bind module {pfx}_fw_mon.",
              "        virtual function void build_monitors();"] + mbuild + \
             ["        endfunction",
              "",
              "        virtual function void connect_monitors();"] + mconn + \
             ["        endfunction"]
    L += ["    endclass",
          "endpackage",
          "",
          f"// The DUT {dut} against {pfx}_tb_c (or a subclass, TB): the user",
          "// writes no bridges. Runs +fw_edges=N clock edges (default 100).",
          f"module {pfx}_tb #(type TB = {pfx}_tb_pkg::{pfx}_tb_c);",
          "    import fw_hdl_pkg::*;",
          "",
          "    bit clk, rst = 1;",
          "    int unsigned cyc;",
          "    TB tb;"]
    L += nets
    L += ["",
          f"    always #{clk_half} clk = ~clk;",
          "",
          f"    {dut} dut ("]
    L += [f"        {c}{',' if i < len(dut_conns) - 1 else ''}" for i, c in enumerate(dut_conns)]
    L += ["    );", "",
          "    fw_pin_bridge #(bit, \"top.clk\") b_clk(.i(clk), .o());",
          "    fw_pin_bridge #(bit, \"top.rst_i\") b_rst(.i(rst), .o());"]
    L += bridges
    L += ["",
          "    initial begin",
          "        int edges = 100;",
          "        void'($value$plusargs(\"fw_edges=%d\", edges));",
          "        tb = TB::create(\"top\", null);",
          "        void'(__fw_rt::elaborate(tb));",
          "        repeat (edges) begin",
          "            @(posedge clk);",
          "            cyc++;",
          f"            if (cyc == {rst_cycles}) rst <= 0;",
          "        end",
          "        foreach (__fw_rt::diags[i])",
          "            $display(\"D %s %s\", __fw_rt::diags[i].id, __fw_rt::diags[i].path);",
          "        $finish;",
          "    end",
          "endmodule", ""]
    if plain:
        L.insert(3, f"// Tied to 0: {', '.join(plain)}")
    return "\n".join(L)


def _dkind(t, d) -> str:
    decl = next((x for x in t.domains if x.name == d.domain.decl), None)
    return decl.kind if decl is not None else ""
