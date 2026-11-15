"""The elaborated object graph: components, signals, ports and bundles.

fe-sv's counterpart of a tool's state behind ``fw_impl_if`` (core-layer1.md
§4.1). Each object wraps the interpreter object that the user's SV code
holds a handle to, and records what elaboration learns about it.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Dict, List, Optional

# The kinds of §3.11, as the netlist spells them.
COMPONENT = "component"
PORT = "port"
SIGNAL = "signal"
BUNDLE = "bundle"
MIRROR = "mirror"
MONITOR = "monitor"
BUNDLE_KINDS = (BUNDLE, MIRROR, MONITOR)


def flip(d: str) -> str:
    return {"in": "out", "out": "in"}.get(d, d)


@dc.dataclass(eq=False)
class FwObject:
    name: str
    parent: Optional["FwObject"]
    sv: Any                     # the interpreter object
    cls: Any                    # the ClassType it was created as
    type_name: str              # canonical spelling (see types.type_name)
    kind: str = ""
    children: Dict[str, "FwObject"] = dc.field(default_factory=dict)
    seq: int = 0                # creation order
    # Tracing (parameterized emission, §6.6): the traced control frames active
    # in the creator's code when this object was created, as
    # ``(frame, iteration, loop value, taken)``; and the name as a traced
    # value (a Sym) when it was computed from one.
    ctx: tuple = ()
    name_sym: Any = None
    # The class it was created as, as written at the creation site
    # (``zuspec.fe.sv.interp.trace.TType``: its parameters traced over the
    # creator's), or None where the creation site does not name it (a
    # constructor factory). For a bundle: the bundle type B.
    targs: Any = None
    # The path of a top elaborated on its own to explore another arm of a
    # branch (FeRt.explore): the path of the component it stands for.
    path_alias: Optional[str] = None

    @property
    def path(self) -> str:
        if self.parent is None:
            return self.path_alias or self.name
        return f"{self.parent.path}.{self.name}"

    @property
    def scope(self) -> "FwComponent":
        """The component that owns this object: itself for a component,
        else the component above the outermost bundle (§3.1)."""
        p = self if isinstance(self, FwComponent) else self.parent
        while not isinstance(p, FwComponent):
            p = p.parent
        return p

    def rel_path(self, scope: "FwComponent") -> List[str]:
        """Field names from ``scope`` down to this object."""
        out: List[str] = []
        o = self
        while o is not scope:
            out.append(o.name)
            o = o.parent
            if o is None:
                raise ValueError(f"{self.path} is not below {scope.path}")
        return list(reversed(out))


@dc.dataclass(eq=False)
class Traced:
    """What a component's or bundle's code is traced over (§6.6): its class
    parameters and, for a component, its params fields.

    - ``cparams``: the value class parameters, a struct-valued one (a shape)
      flattened: ``(flat name, path, slang type, value)``, ``("S_data_w",
      ("S", "data_w"), int, 8)``;
    - ``cvals``: each value class parameter as the code reads it, by name: a
      ``Sym`` over its leaf (a shape's leaf is a struct leaf);
    - ``leaves``: ``id(leaf) -> parameter name`` of every scalar leaf;
    - ``sleaves``: ``id(struct leaf) -> (name, {field path: leaf})``;
    - ``type_leaves``: type parameter name -> leaf;
    - ``base_args``: for each base class, by its canonical name, its
      parameters as the ``extends`` clause writes them (traced)."""
    cparams: List[tuple] = dc.field(default_factory=list)
    cvals: Dict[str, Any] = dc.field(default_factory=dict)
    leaves: Dict[int, str] = dc.field(default_factory=dict)
    sleaves: Dict[int, tuple] = dc.field(default_factory=dict)
    type_leaves: Dict[str, Any] = dc.field(default_factory=dict)
    base_args: Dict[str, Any] = dc.field(default_factory=dict)


@dc.dataclass(eq=False)
class FwComponent(FwObject):
    kind: str = COMPONENT
    params: Any = None                       # the resolved params value (interpreter struct)
    params_type: Any = None                  # its slang type, or None for no params
    param_src: Dict[str, str] = dc.field(default_factory=dict)
    extern: Optional["ExternRec"] = None     # set for a component that implements fw_extern_if
    is_extern: bool = False
    # Tracing. The params value as the component's own code sees it: each
    # module-parameter field (§6.6) a Sym over an ``ExprRefParam`` leaf.
    params_sym: Any = None
    params_dflt: Any = None                  # mk_params() with no arguments
    mparams: List[tuple] = dc.field(default_factory=list)   # (flat name, field path, slang type)
    tr: Traced = dc.field(default_factory=Traced)            # leaves, class parameters
    # Each module parameter's value as the creator computed it: an Expr over
    # the creator's parameters and genvars, or None when it cannot be
    # written so (set by an ancestor's override, or not traced).
    param_exprs: Dict[str, Any] = dc.field(default_factory=dict)
    frames: List[Any] = dc.field(default_factory=list)      # CtlFrames run in its code
    conns: List["Conn"] = dc.field(default_factory=list)    # connections made in its code
    ties: List["Tie"] = dc.field(default_factory=list)

    @property
    def leaves(self) -> Dict[int, str]:
        return self.tr.leaves

    @property
    def type_leaves(self) -> Dict[str, Any]:
        return self.tr.type_leaves


@dc.dataclass(eq=False)
class FwSignal(FwObject):
    """A port (``dir`` in/out/inout, declared) or a local signal (``dir`` None)."""
    dir: Optional[str] = None
    dtype: Any = None                        # the slang type T
    width: int = 1
    signed: bool = False
    ttype: Any = None                        # T as written (a TType), or None

    @property
    def is_port(self) -> bool:
        return self.dir is not None

    def effective_dir(self) -> Optional[str]:
        """The direction as seen from the scope: each mirror on the path from
        the scope flips it, a monitor makes it an input (§3.4)."""
        d = self.dir
        if d is None or d == "inout":
            return d
        p = self.parent
        while isinstance(p, FwBundle):
            if p.kind == MONITOR:
                return "in"
            if p.kind == MIRROR:
                d = flip(d)
            p = p.parent
        return d


@dc.dataclass(eq=False)
class FwBundle(FwObject):
    """A bundle in a role: kind is bundle (initiator), mirror or monitor."""
    btype: Any = None                        # the bundle type B (a ClassType)
    btype_name: str = ""
    tr: Traced = dc.field(default_factory=Traced)            # B's class parameters

    def leaves(self) -> List[FwSignal]:
        out: List[FwSignal] = []
        for c in self.children.values():
            if isinstance(c, FwBundle):
                out.extend(c.leaves())
            else:
                out.append(c)
        return out


@dc.dataclass
class PinRule:
    """``m.prefix(b, pre)`` with its renames."""
    bundle: FwBundle
    prefix: str
    renames: Dict[str, str] = dc.field(default_factory=dict)


@dc.dataclass
class ExternRec:
    """What ``extern_map()`` recorded, before it is checked against the
    module header (§3.7)."""
    module: str
    params: Dict[str, int] = dc.field(default_factory=dict)
    pins: List[tuple] = dc.field(default_factory=list)      # (pin, FwSignal)
    rules: List[PinRule] = dc.field(default_factory=list)
    ties: Dict[str, int] = dc.field(default_factory=dict)
    opens: List[str] = dc.field(default_factory=list)
    unused: List["FwObject"] = dc.field(default_factory=list)  # unused(): ports or bundles
    # Tracing: param() and tie() values as given (Syms where traced), and
    # the traced frames active when extern_map() made them.
    tie_syms: Dict[str, Any] = dc.field(default_factory=dict)
    ctl: bool = False
    # Resolved by the check: pin -> ("port", FwSignal) | ("tie", int) | ("open", None),
    # in module order.
    resolved: List[tuple] = dc.field(default_factory=list)
    unused_ports: List["FwSignal"] = dc.field(default_factory=list)   # leaf ports, by path
    header: Any = None


@dc.dataclass
class Conn:
    """One recorded connection in ``scope``: two signals, or two bundles."""
    scope: FwComponent
    a: FwObject
    b: FwObject
    how: str = "connect"        # connect | connect_up | connect_all
    ctx: tuple = ()             # traced frames (see FwObject.ctx)
    a_sym: Any = None           # the endpoint handles as traced values (Syms), if they were
    b_sym: Any = None


@dc.dataclass
class Tie:
    scope: FwComponent
    sig: FwSignal
    value: int
    ctx: tuple = ()
    sig_sym: Any = None
    value_sym: Any = None


@dc.dataclass
class Diag:
    id: str
    path: str
    msg: str
    severity: str = "error"

    def __str__(self) -> str:
        return f"{self.severity}: {self.id}: {self.path}: {self.msg}"
