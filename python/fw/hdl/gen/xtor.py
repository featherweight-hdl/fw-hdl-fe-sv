"""``<prefix>_initiator``/``<prefix>_target``/``<prefix>_monitor``: the
transactors a kit derives from a bundle of channels (``Kit.bundles``). The
pin member fe-sv's ``protocols.xtor_role`` reads off decides the role
(``fw_bundle`` -> initiator, ``fw_mirror`` -> target, ``fw_monitor`` ->
monitor).

The initiator implements ``<prefix>_req_if`` (each fwd channel's task
forwards to a per-channel ``fw_rv_initiator``'s ``put``) and calls out
``<prefix>_rsp_if`` for each received rev beat, through a per-channel
``fw_rv_target`` and a small generated sink adapter (an ``fw_put_if``
implementation whose ``put`` forwards to the owner's delivery task, since
an ``fw_rv_target``'s ``p`` port needs an export to bind to). The target is
the mirror image: it sends rev channels, receives fwd ones, implements
``<prefix>_rsp_if`` and calls out ``<prefix>_req_if``. Both are registered
as converters against the bundle's protocol (``fw_xtor_param_utils`` /
``fw_xtor_utils``, as :sv:func:`fw_rv_initiator`).

The monitor taps every channel with a ``fw_rv_monitor``, named after the
channel, and is registered as the converter for a monitor tap. Its method
side is a listener fan-out per derived API: ``add_req_listener`` takes a
``<prefix>_req_if_listener`` (``add_rsp_listener`` the ``_rsp_if`` one),
and each channel's ``<ch>_pre``/``<ch>_post`` is called at the channel's
first cycle/handshake, through a small generated adapter
(``<prefix>_<ch>_mon_listener``, an :sv:class:`fw_rv_mon_listener` attached
to the channel's ``fw_rv_monitor``) into the API's generated
``<api>_listener_fanout``.

Construction, pin wiring and the registration macros are raw SV
(``emit.to_sv``'s ``metadata["sv_items"]`` mechanism): be-sv's IR has no
``::create`` or macro-expansion construct. The forwarding/delivery tasks
are built as IR.
"""
from __future__ import annotations

from typing import Dict, List, Sequence

import zuspec.ir.core as ir

from fw.hdl.fe.sv.channels import BundleChannels, Channel

from . import irb
from .kit import Kit, KitApi
from .listener import fanout_name, listener_name

#: External (std/rt-sv) runtime classes a bundle's protocol macro, when it
#: is not std's own kit, needs ``fw_hdl_std_pkg`` imported for.
STD_IMPORT = "fw_hdl_std_pkg"


def initiator_name(b: BundleChannels) -> str:
    return b.prefix + "_initiator"


def target_name(b: BundleChannels) -> str:
    return b.prefix + "_target"


def monitor_name(b: BundleChannels) -> str:
    return b.prefix + "_monitor"


def sink_name(b: BundleChannels, c: Channel) -> str:
    return f"{b.prefix}_{c.name}_sink"


def mon_listener_name(b: BundleChannels, c: Channel) -> str:
    return f"{b.prefix}_{c.name}_mon_listener"


def _ext(name: str, args: Sequence[ir.DataType] = ()) -> ir.DataType:
    """A reference to an external generic class (std/rt-sv), specialized
    over ``args``, or bare when there are none: ``name #(args)``."""
    if not args:
        return ir.DataTypeRef(ref_name=name, name=name)
    targs = [ir.TemplateArgType(param_name=f"a{i}", type_value=a)
             for i, a in enumerate(args)]
    # be-sv's ``_type_deps`` reads a specialization's base's ``name`` (not
    # ``ref_name``); set both so a package-local dependency scan does not
    # trip over an external base with no ``name``.
    return ir.DataTypeSpecialized(base_template=ir.DataTypeRef(ref_name=name, name=name),
                                  template_args=targs, name=name)


def _self(name: str, tmpl: Sequence[ir.TemplateParam]) -> ir.DataType:
    """``name #(tmpl)`` (or bare ``name``): a reference to another class
    this generator declares, over the bundle's own parameters, as
    :func:`irb.self_ref` makes for a template of its own parameters."""
    if not tmpl:
        return ir.DataTypeRef(ref_name=name)
    return irb.self_ref(ir.DataTypeParameterized(name=name, template_params=list(tmpl)))


def _tn(dt: ir.DataType) -> str:
    return getattr(dt, "ref_name", None) or getattr(dt, "name", None) or "int"


def _type_str(dt: ir.DataType) -> str:
    from zuspec.be.sv.ir.core_to_sv import sv_type_str
    return sv_type_str(dt, _tn)


def _args_str(tmpl: Sequence[ir.TemplateParam]) -> str:
    return f" #({', '.join(p.name for p in tmpl)})" if tmpl else ""


def _raw_component(name: str, tmpl: Sequence[ir.TemplateParam]) -> List[str]:
    return [f"typedef {name}{_args_str(tmpl)} this_t;",
            f"`fw_component_param_utils({name})"]


def _raw_xtor(name: str, tmpl: Sequence[ir.TemplateParam], proto: str) -> List[str]:
    """The ``this_t`` typedef (when parameterized) and the converter
    registration macro, over the bundle's protocol ``proto``."""
    if tmpl:
        proto_ref = f"{proto}{_args_str(tmpl)}"
        return [f"typedef {name}{_args_str(tmpl)} this_t;",
                f"`fw_xtor_param_utils({name}, {proto_ref})"]
    return [f"`fw_xtor_utils({name}, {proto})"]


def _deliver_name(c: Channel) -> str:
    return f"__{c.name}_deliver"


def _sink_decl(b: BundleChannels, c: Channel, owner_name: str) -> ir.DataType:
    """``<prefix>_<ch>_sink``: implements ``fw_put_if #(payload)`` by
    forwarding each ``put`` to the owning xtor's delivery task. rt-sv's
    ``fw_export``: "if the parent implements the API, it is the
    implementation, with no further call" -- the sink is its own export's
    implementation, as :sv:class:`sink_c` in the K9.3 b2b test."""
    name = sink_name(b, c)
    put_if = _ext("fw_put_if", [c.payload])
    put = irb.method("put", [irb.arg("d", c.payload)],
                     [irb.call(irb.attr(irb.ref("owner"), _deliver_name(c)), [irb.ref("d")])],
                     task=True)
    cls = ir.DataTypeClass(
        super=_ext("fw_component"), implements=[put_if],
        fields=[irb.field("e", _ext("fw_export", [put_if])),
               irb.field("owner", _self(owner_name, b.tmpl))],
        functions=[put])
    raw = _raw_component(name, b.tmpl) + [
        "", "virtual function void build();",
        f"    e = {_type_str(_ext('fw_export', [put_if]))}::create(\"e\", this);",
        "endfunction"]
    cls.metadata = {"sv_items": [raw]}
    return irb.template(name, b.tmpl, cls)


def _xtor_decls(b: BundleChannels, kit: Kit, initiator: bool) -> List[ir.DataType]:
    """``<prefix>_initiator`` (``initiator``) or ``<prefix>_target``, and
    the sink adapters its received channels need."""
    api_by_name: Dict[str, KitApi] = {a.name: a for a in kit.apis}
    fwd = [c for c in b.channels if c.fwd]
    rev = [c for c in b.channels if not c.fwd]
    send, recv = (fwd, rev) if initiator else (rev, fwd)
    impl_suffix, call_suffix = ("_req_if", "_rsp_if") if initiator else ("_rsp_if", "_req_if")
    impl_api = api_by_name.get(b.prefix + impl_suffix)
    call_api = api_by_name.get(b.prefix + call_suffix)
    name = initiator_name(b) if initiator else target_name(b)
    # A mirror's members keep the bundle's declared kinds (rt-sv does not
    # flip them), so in the target each channel's xtor pins are the
    # opposite kind of ``pins.<ch>``: bind them as peers, which the runtime
    # joins the same way (``__fw_rt::connect`` unifies the nets either way).
    up = "connect_up" if initiator else "connect"
    bundle_ref = _self(b.name, b.tmpl)
    pins_t = _ext("fw_bundle" if initiator else "fw_mirror", [bundle_ref])

    fields = [irb.field("pins", pins_t)]
    build = [f"pins = {_type_str(pins_t)}::create(\"pins\", this);"]
    connect: List[str] = []
    methods: List[ir.Function] = []
    implements: List[ir.DataType] = []
    out: List[ir.DataType] = []

    # The method side: a socket (both directions), or an export/port (one).
    sock_field = None
    if impl_api is not None and call_api is not None:
        sock_t = _ext("fw_socket", [call_api.ref, impl_api.ref])
        fields.insert(0, irb.field("s", sock_t))
        build.insert(0, f"s = {_type_str(sock_t)}::create(\"s\", this);")
        implements = [impl_api.ref]
        sock_field = "s"
    elif impl_api is not None:
        exp_t = _ext("fw_export", [impl_api.ref])
        fields.insert(0, irb.field("t", exp_t))
        build.insert(0, f"t = {_type_str(exp_t)}::create(\"t\", this);")
        implements = [impl_api.ref]
    elif call_api is not None:
        port_t = _ext("fw_port", [call_api.ref])
        fields.insert(0, irb.field("p", port_t))
        build.insert(0, f"p = {_type_str(port_t)}::create(\"p\", this);")
        sock_field = "p"
    if sock_field is None and call_api is not None:
        sock_field = "p"  # pragma: no cover (call_api always sets p or s above)

    # The channels this role sends: one fw_rv_initiator each, forwarding
    # the implemented API's task to its put().
    for c in send:
        fx = f"{c.name}_x"
        fx_t = _ext("fw_rv_initiator", [c.payload])
        fields.append(irb.field(fx, fx_t))
        build.append(f"{fx} = {_type_str(fx_t)}::create(\"{fx}\", this);")
        connect.append(f"{fx}.pins.{up}(pins.{c.name});")
        if impl_api is not None:
            m = next(mm for mm in impl_api.proto.methods if mm.name == c.name)
            arg = m.args.args[0].arg
            methods.append(irb.clone_method(
                m, [irb.call(irb.attr(irb.ref(fx), "put"), [irb.ref(arg)])]))

    # The channels this role receives: a fw_rv_target each, plus the sink
    # adapter its ``p`` port binds to; the sink's put calls the delivery
    # task, which calls out the called API's matching method.
    for c in recv:
        fx = f"{c.name}_x"
        fx_t = _ext("fw_rv_target", [c.payload])
        fields.append(irb.field(fx, fx_t))
        build.append(f"{fx} = {_type_str(fx_t)}::create(\"{fx}\", this);")
        connect.append(f"{fx}.pins.{up}(pins.{c.name});")
        sink_f = f"{c.name}_sink"
        sink_t = _self(sink_name(b, c), b.tmpl)
        fields.append(irb.field(sink_f, sink_t))
        build.append(f"{sink_f} = {_type_str(sink_t)}::create(\"{sink_f}\", this);")
        build.append(f"{sink_f}.owner = this;")
        connect.append(f"{fx}.p.connect({sink_f}.e);")
        out.append(_sink_decl(b, c, name))

        deliver = irb.method(_deliver_name(c), [irb.arg("d", c.payload)], task=True)
        body: List[ir.Stmt] = []
        if call_api is not None and sock_field is not None:
            out_if = ir.ExprCall(func=irb.attr(irb.ref(sock_field), "get_if"), args=[])
            body = [irb.call(irb.attr(out_if, c.name), [irb.ref("d")])]
        methods.append(irb.clone_method(deliver, body))

    cls = ir.DataTypeClass(super=_ext("fw_component"), implements=implements, fields=fields, functions=methods)
    raw = _raw_xtor(name, b.tmpl, b.proto)
    if build:
        raw += ["", "virtual function void build();"] + [f"    {ln}" for ln in build] + ["endfunction"]
    if connect:
        raw += ["", "virtual function void connect();"] + [f"    {ln}" for ln in connect] + ["endfunction"]
    cls.metadata = {"sv_items": [raw]}
    out.insert(0, irb.template(name, b.tmpl, cls))
    return out


def _fanout_field(fwd: bool) -> str:
    return "m_req_listeners" if fwd else "m_rsp_listeners"


def _mon_listener_decl(b: BundleChannels, c: Channel, api: KitApi,
                       owner_name: str) -> ir.DataType:
    """``<prefix>_<ch>_mon_listener``: an ``fw_rv_mon_listener #(payload)``
    attached to the monitor's channel ``fw_rv_monitor``, forwarding each
    ``put_pre``/``put_post`` to the owner's fan-out of ``api`` as
    ``<ch>_pre``/``<ch>_post``."""
    fan = irb.attr(irb.ref("owner"), _fanout_field(c.fwd))
    fns = [irb.method(f"put_{w}", [irb.arg("t", c.payload)],
                      [irb.call(irb.attr(fan, f"{c.name}_{w}"), [irb.ref("t")])])
           for w in ("pre", "post")]
    cls = ir.DataTypeClass(super=_ext("fw_rv_mon_listener", [c.payload]),
                           fields=[irb.field("owner", _self(owner_name, b.tmpl))],
                           functions=fns)
    return irb.template(mon_listener_name(b, c), b.tmpl, cls)


def _monitor_decls(b: BundleChannels, kit: Kit) -> List[ir.DataType]:
    """``<prefix>_monitor``: one ``fw_rv_monitor`` per channel, named after
    the channel, tapping ``pins.<channel>``, and a listener fan-out per
    derived API (``add_req_listener``/``add_rsp_listener``), called from
    each channel's adapter; then the adapters."""
    api_by_name: Dict[str, KitApi] = {a.name: a for a in kit.apis}
    name = monitor_name(b)
    bundle_ref = _self(b.name, b.tmpl)
    pins_t = _ext("fw_monitor", [bundle_ref])
    fields = [irb.field("pins", pins_t)]
    build = [f"pins = {_type_str(pins_t)}::create(\"pins\", this);"]
    connect = []
    methods: List[ir.Function] = []
    out: List[ir.DataType] = []
    for fwd, suffix in ((True, "req"), (False, "rsp")):
        api = api_by_name.get(b.prefix + f"_{suffix}_if")
        if api is None or not any(c.fwd == fwd for c in b.channels):
            continue
        fld = _fanout_field(fwd)
        fields.append(irb.field(fld, _self(fanout_name(api), b.tmpl)))
        build.append(f"{fld} = new();")
        lst_t = _self(listener_name(api), b.tmpl)
        for op in ("add", "remove"):
            methods.append(irb.method(
                f"{op}_{suffix}_listener", [irb.arg("l", lst_t)],
                [irb.call(irb.attr(irb.ref(fld), f"{op}_listener"), [irb.ref("l")])]))
    for c in b.channels:
        mon_t = _ext("fw_rv_monitor", [c.payload])
        fields.append(irb.field(c.name, mon_t))
        build.append(f"{c.name} = {_type_str(mon_t)}::create(\"{c.name}\", this);")
        connect.append(f"{c.name}.p.connect(pins.{c.name});")
        api = api_by_name.get(b.prefix + ("_req_if" if c.fwd else "_rsp_if"))
        if api is None:
            continue    # pragma: no cover (each channel's API is derived)
        ml = f"{c.name}_ml"
        fields.append(irb.field(ml, _self(mon_listener_name(b, c), b.tmpl)))
        build += [f"{ml} = new();", f"{ml}.owner = this;", f"{c.name}.add_listener({ml});"]
        out.append(_mon_listener_decl(b, c, api, name))
    cls = ir.DataTypeClass(super=_ext("fw_component"), fields=fields, functions=methods)
    raw = _raw_xtor(name, b.tmpl, b.proto)
    raw += ["", "virtual function void build();"] + [f"    {ln}" for ln in build] + ["endfunction"]
    raw += ["", "virtual function void connect();"] + [f"    {ln}" for ln in connect] + ["endfunction"]
    cls.metadata = {"sv_items": [raw]}
    out.insert(0, irb.template(name, b.tmpl, cls))
    return out


def kit_post_decls(kit: Kit) -> List[ir.DataType]:
    """The initiator, target and monitor of each bundle of channels, after
    all per-API decls (they reference the derived ``_req_if``/``_rsp_if``
    and need them already declared)."""
    out: List[ir.DataType] = []
    for b in kit.bundles:
        if not b.channels:
            continue
        out.extend(_xtor_decls(b, kit, initiator=True))
        out.extend(_xtor_decls(b, kit, initiator=False))
        out.extend(_monitor_decls(b, kit))
    return out
