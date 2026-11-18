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
| `tb/chain_accept_tb.sv` | All-RTL reference for the generated testbench: drives `chain_ref` with a given sequence and prints the cycle of each handshake. |
| `flow.yaml` | `fw.hdl.fe.sv.Elaborate` → `fw.hdl.fe.sv.EmitSV` → Verilator. |

## The wrappers

A wrapper is a component that implements `fw_extern_if`. It declares its
ports like any component, and its `extern_map()` says how they meet the
module's pins:

```systemverilog
class rv_fifo #(type T = bit[7:0]) extends fw_component implements fw_extern_if;
    `fw_extern_utils(rv_fifo #(T), "fw_rv_fifo")
    fw_mirror #(fw_rv_if #(T))  in;      // target side of a ready/valid link
    fw_bundle #(fw_rv_if #(T))  out;     // initiator side
    ...
    virtual function void extern_map(fw_extern_map m);
        m.param("WIDTH", fw_value #(int)::of($bits(T)));
        m.param("DEPTH", fw_value #(int)::of(params.depth));
        fw_clock_domain::pin(m, "clk");          // the component's clock
        fw_reset_domain::pin(m, "rst");          // and reset (active high)
        void'(m.prefix(in,  "in_").rename("valid", "vld").rename("ready", "rdy"));
        void'(m.prefix(out, "out_").rename("valid", "vld").rename("ready", "rdy"));
    endfunction
endclass
```

- `prefix(b, pre)` maps every member of bundle `b` to `pre` + its name;
  `rename` changes the member part. `axis_slice` uses `prefix(s, "s_axis_t")`
  to reach `s_axis_tvalid`, `s_axis_tready` and `s_axis_tdata`.
- `fw_clock_domain::pin(m, name)` and `fw_reset_domain::pin(m, name)` map
  a clock or reset pin to the component's clock or reset domain. The
  wrapper declares no port for it, and its parent wires nothing.
  `axis_slice` maps `aresetn` with `fw_reset_kind::active_low()`, the
  polarity the pin expects.
- `tie(name, value)` drives an input pin the component has no port for
  (`s_axis_tlast`); `open(name)` leaves an output pin unconnected
  (`m_axis_tlast`).
- Anything not mapped follows the default rule: the pin named like the
  port, flattened (`in_data`).

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

`chain` owns the clocking. It creates a clock and a reset domain, makes
them its defaults (so its children inherit them), and drives them from
its two inputs:

```systemverilog
clk = fw_clock_domain::create("clk", this);
rst = fw_reset_domain::create("rst", this, fw_reset_kind::root(.clock(clk)));
fw_clock_domain::set(this, clk);
fw_reset_domain::set(this, rst);
...
clk.drive(clk_i);
rst.drive(rst_i);                 // an active-high net
```

In `chain.sv` the domains are nets, `clk` and `rst`, driven from `clk_i`
and `rst_i`. The FIFO's `rst` reads `rst`; the slice's active-low
`aresetn` reads `rst_inv`, which emission declares as `~rst`. The
reference, `tb/chain_ref.sv`, wires the same inverter by hand.

## A generated testbench

`chain`'s ports are ready/valid bundles, so fw-hdl knows the transactor
for each, and the testbench can be generated rather than written:

```python
r = elaborate(["sv/wrap_pkg.sv"], top="wrap_pkg::chain#(bit[7:0])", rtl=[...])
s = scan([".../fw_hdl_std_pkg.sv"], incdirs=[...])
tb_sv   = gen_tb(r, s, "chain", bind=True)   # chain_tb_pkg::chain_tb_c + module chain_tb
bind_sv = gen_bind(r, s, "chain")            # chain_fw_mon + bind chain ...
```

`chain_tb_c` holds an initiator `dut_in` for the DUT's `in`, a target
`dut_out` that puts what it receives into the inbox `dut_out_rx`, the
clock and reset, and (with `bind=True`) the monitors `mon_in` and
`mon_out`, fed by the bound module `chain_fw_mon`, which only reads the
DUT's pins. Module `chain_tb #(type TB)` holds `chain` and the bridges
between its pins and the class. A test extends `chain_tb_c`, calls
`dut_in.put(...)` and `dut_out_rx.get(...)` in `run()`, adds listeners
to the monitors in `connect()`, and instances `chain_tb #(.TB(test_c))`.

fe-sv's `tests/e2e/test_wrap_rtl_acceptance.py` checks that this adds no
latency: it drives 48 random items with input gaps and output
back-pressure through the generated testbench, scores in = out in order
on the monitors' listeners, and compares the cycle of every handshake
with `tb/chain_accept_tb.sv` driving `chain_ref` with the same sequence.
All 96 handshakes match. `tests/e2e/test_gen_tb.py` and
`test_gen_bind.py` are smaller examples of the same bench.
