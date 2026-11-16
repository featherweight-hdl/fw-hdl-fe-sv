# Clock and reset domains

This example moves a stream from one clock to another through a divided
clock, with no clock or reset wired by hand. The fw-hdl description says
which domain each part is in. fe-sv checks every connection between
domains, and the generated Verilog gets the clock and reset ports, nets and
inverters each module needs. A testbench runs the generated design next to
the same modules wired by hand, and checks that they match.

    dfm run sim      # elaborate, emit, build with Verilator, simulate
    dfm run emit     # only the Verilog: rundir/domains.emit/soc.sv

| File | What it is |
|---|---|
| `rtl/clk_div2.sv` | Existing RTL: a divide-by-2 clock, active-low `rst_ni`. |
| `rtl/fw_rv_fifo.sv` | Existing RTL: a single-clock FIFO, active-high `rst`. |
| `rtl/async_fifo.sv` | Existing RTL: a dual-clock FIFO (`wr_clk`, `rd_clk`), active-low resets. |
| `sv/domains_pkg.sv` | The fw-hdl description: wrappers `div2`, `rv_fifo` and `cdc_fifo`, the block `xfer`, the design `soc`, and `soc_bad`. |
| `tb/soc_ref.sv` | The same design wired by hand: the reference. |
| `tb/soc_tb.sv` | Random stimulus on two unrelated clocks; compares `soc` with `soc_ref` and checks stream order. |
| `flow.yaml` | `fw.hdl.fe.sv.Elaborate` → `fw.hdl.fe.sv.EmitSV` → Verilator. |

## The design

```
in (clk_a) -> x1 -> (clk_a) -> x2 -> (slow) -> x3 -> (clk_b) out
```

`soc` creates three clock domains and a reset domain, and drives the
root ones from its inputs:

```systemverilog
clk_a = fw_clock_domain::create("clk_a", this);
clk_b = fw_clock_domain::create("clk_b", this);
slow  = fw_clock_domain::create("slow", this, fw_clock_kind::derived(clk_a, .div(2)));
rst   = fw_reset_domain::create("rst", this);
fw_clock_domain::set(this, clk_a);          // soc and its children default to clk_a
fw_reset_domain::set(this, rst);
fw_clock_domain::set(out, clk_b);           // the output interface is in clk_b
...
clk_a.drive(clk_a_i);
clk_b.drive(clk_b_i);
rst.drive(rst_ni, fw_reset_kind::active_low());
```

- `clk_a` and `clk_b` are unrelated: neither is derived from the other.
- `slow` is `clk_a` divided by 2. The divider wrapper `div2` produces it:
  its map drives its association `out` from the module's output pin, and
  `soc` sets `div`'s `out` to `slow`. `div2` also states the derivation it
  provides, which fe-sv checks against `slow`'s.
- The reset net is active low. The divider and the dual-clock FIFO expect
  active low; the single-clock FIFO expects active high.

## Choosing a FIFO by how the clocks relate

`xfer` moves a stream from its `wr` clock to its `rd` clock, two named
associations its parent sets. It asks how they relate and builds the FIFO
that fits:

```systemverilog
fw_clock_domain wr = fw_clock_domain::get(this, "wr");
fw_clock_domain rd = fw_clock_domain::get(this, "rd");
if (fw_clock_domain::relate(wr, rd) == FW_DOMAIN_SAME) begin
    sf = rv_fifo #(T)::create("sf", this);       // one clock
    fw_clock_domain::set(sf, wr);
end else begin
    af = cdc_fifo #(T)::create("af", this);      // two clocks
    fw_clock_domain::set(af, wr, "wr");
    fw_clock_domain::set(af, rd, "rd");
end
```

`x1` joins `clk_a` to itself and gets the single-clock FIFO; `x2` and `x3`
cross and get the dual-clock one. The two structures are two classes in
the Verilog, `xfer` and `xfer__1` (fe-sv says so with `FW-I-EMIT-SPEC`).
`x2` and `x3` share `xfer__1`: their ports are named by association
(`wr_clk`, `rd_clk`), not by the clocks an instance happens to get.

`cdc_fifo` puts its interfaces in its two clocks, so a connection to its
`in` is checked against `wr` and one to its `out` against `rd`:

```systemverilog
fw_clock_domain::set(in,  fw_clock_domain::get(this, "wr"));
fw_clock_domain::set(out, fw_clock_domain::get(this, "rd"));
```

## What the Verilog gets

- Each class's module has a port for each clock and reset its subtree
  uses, named by the kind: `clk`/`wr_clk`/`rd_clk`, `rst`/`rst_n`. A
  reset port's polarity is what its subtree's pins expect (`xfer` has
  `rst`, `xfer__1` has `rst_n`).
- `soc` declares a net per domain (`clk_a`, `clk_b`, `slow`, `rst`),
  assigns the root ones from its inputs, and connects the divider's
  `clk_o` to `slow`.
- The reset net is active low and `x1`'s `rst` is active high, so `soc`
  declares `rst_inv`, `~rst`, and connects `x1` to it.

## The crossing check

`soc_bad` connects `x1`'s output, in `clk_a`, straight to its own output,
in `clk_b`, with nothing between. Elaboration fails:

    FW-E-DOM-CROSS: top.out: fw_clock_kind crossing from top.clk_a to top.clk_b (top.x1.out)

A crossing between related clocks (`clk_a` and `slow`) without a crossing
element would be information (`FW-I-DOM-CROSS`), not an error.
