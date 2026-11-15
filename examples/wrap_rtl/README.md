# Wrapping existing RTL with fw-hdl

This example takes two existing RTL modules with different pin conventions,
wraps each as an fw-hdl component whose protocol ports are ready/valid
bundles, connects them in fw-hdl, and generates plain Verilog. A testbench
then runs the generated module next to the same two modules wired by hand,
and checks that they match on every cycle.

    dfm run sim      # elaborate, emit, build with Verilator, simulate
    dfm run emit     # only the Verilog: rundir/wrap_rtl.emit/chain.sv

| File | What it is |
|---|---|
| `rtl/fw_rv_fifo.sv` | Existing RTL: a FIFO with pins `in_vld/in_rdy/in_data`, active-high `rst`. |
| `rtl/axis_reg.sv` | Existing RTL: an AXI-Stream register slice with pins `s_axis_tvalid/...`, `aclk`, active-low `aresetn`, and a `tlast` side band. |
| `sv/wrap_pkg.sv` | The fw-hdl description: wrappers `rv_fifo` and `axis_slice`, and the assembly `chain`. |
| `tb/chain_ref.sv` | The same assembly wired by hand: the reference. |
| `tb/chain_tb.sv` | Random stimulus and back-pressure; compares `chain` with `chain_ref` and checks stream order. |
| `flow.yaml` | `fw.hdl.fe.sv.Elaborate` → `fw.hdl.fe.sv.EmitSV` → Verilator. |

## The wrappers

A wrapper is a component that implements `fw_extern_if`. It declares its
ports like any component, and its `extern_map()` says how they meet the
module's pins:

```systemverilog
class rv_fifo #(type T = bit[7:0]) extends fw_component implements fw_extern_if;
    `fw_extern_utils(rv_fifo #(T), "fw_rv_fifo")
    fw_input  #(bit)            clk, rst;
    fw_mirror #(fw_rv_if #(T))  in;      // target side of a ready/valid link
    fw_bundle #(fw_rv_if #(T))  out;     // initiator side
    ...
    virtual function void extern_map(fw_extern_map m);
        m.param("WIDTH", fw_value #(int)::of($bits(T)));
        m.param("DEPTH", fw_value #(int)::of(params.depth));
        void'(m.prefix(in,  "in_").rename("valid", "vld").rename("ready", "rdy"));
        void'(m.prefix(out, "out_").rename("valid", "vld").rename("ready", "rdy"));
    endfunction
endclass
```

- `prefix(b, pre)` maps every member of bundle `b` to `pre` + its name;
  `rename` changes the member part. `axis_slice` uses `prefix(s, "s_axis_t")`
  to reach `s_axis_tvalid`, `s_axis_tready` and `s_axis_tdata`.
- `pin(name, port)` maps one pin by hand (`aclk`, `aresetn`).
- `tie(name, value)` drives an input pin the component has no port for
  (`s_axis_tlast`); `open(name)` leaves an output pin unconnected
  (`m_axis_tlast`).
- Anything not mapped follows the default rule: the pin named like the
  port, flattened (`clk`, `in_data`).

fe-sv checks the map against each module's header: every pin is accounted
for once, every port reaches a pin, and directions and widths agree.

## The assembly

`chain` creates the two wrappers and connects bundles, not wires:

```systemverilog
fifo.in.connect_up(in);      // pass a child's target side up to our own
fifo.out.connect(slice.s);   // initiator to target, between siblings
slice.m.connect_up(out);
```

In the generated `chain.sv` each wrapper is replaced by its module, with
the parameters and pin connections its map gave.

Layer 1 has no clock or reset domains, so `chain` takes both `rst` and
`rst_n` as ports; the domain layer will derive the active-low reset.
