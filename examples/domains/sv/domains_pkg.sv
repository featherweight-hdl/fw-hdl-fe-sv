// Clock and reset domains with fw-hdl.
//
// soc moves a stream from one clock to another, through a divided clock:
//
//   in (clk_a) -> x1 -> (clk_a) -> x2 -> (slow) -> x3 -> (clk_b) out
//
// - clk_a and clk_b are unrelated clocks, driven from soc's inputs.
// - slow is clk_a divided by 2. A wrapped divider (div2) produces it on an
//   output pin, and states the derivation it provides.
// - xfer moves a stream from its "wr" clock to its "rd" clock. It asks how
//   the two relate and builds a single-clock FIFO when they are the same
//   clock (x1), a dual-clock FIFO when they are not (x2, x3).
// - One reset, driven active low from rst_ni. The divider and the
//   dual-clock FIFO expect it active low, the single-clock FIFO active
//   high: emission inverts where they differ.
//
// Nothing wires a clock or a reset by hand. soc_bad connects the clk_a
// side straight to the clk_b output, with no xfer between: elaboration
// fails with FW-E-DOM-CROSS.
`include "fw_hdl_macros.svh"

package domains_pkg;
    import fw_hdl_pkg::*;

    // clk_div2: clk_o is clk_i divided by 2. The divider's own clock is its
    // default; the clock it produces is its association "out".
    class div2 extends fw_component implements fw_extern_if;
        `fw_extern_utils(div2, "clk_div2")
        virtual function void build();
            fw_clock_domain::provides(this, "out",
                fw_clock_kind::derived(fw_clock_domain::get(this), .div(2)));
        endfunction
        virtual function void extern_map(fw_extern_map m);
            fw_clock_domain::pin(m, "clk_i");
            fw_reset_domain::pin(m, "rst_ni", null, fw_reset_kind::active_low());
            fw_clock_domain::pin_out(m, "clk_o", fw_clock_domain::get(this, "out"));
        endfunction
    endclass

    // fw_rv_fifo: a single-clock ready/valid FIFO, active-high reset.
    class rv_fifo #(type T = bit[7:0]) extends fw_component implements fw_extern_if;
        `fw_extern_utils(rv_fifo #(T), "fw_rv_fifo")
        fw_mirror #(fw_rv_if #(T))  in;
        fw_bundle #(fw_rv_if #(T))  out;

        virtual function void build();
            in  = fw_mirror #(fw_rv_if #(T))::create("in",  this);
            out = fw_bundle #(fw_rv_if #(T))::create("out", this);
        endfunction

        virtual function void extern_map(fw_extern_map m);
            m.param("WIDTH", fw_value #(int)::of($bits(T)));
            m.param("DEPTH", fw_value #(int)::of(2));
            fw_clock_domain::pin(m, "clk");
            fw_reset_domain::pin(m, "rst");
            void'(m.prefix(in,  "in_").rename("valid", "vld").rename("ready", "rdy"));
            void'(m.prefix(out, "out_").rename("valid", "vld").rename("ready", "rdy"));
        endfunction
    endclass

    // async_fifo: a dual-clock FIFO. Its clocks are its named associations
    // "wr" and "rd"; in is in the "wr" clock, out in the "rd" clock. Both
    // reset pins expect active low.
    class cdc_fifo #(type T = bit[7:0]) extends fw_component implements fw_extern_if;
        `fw_extern_utils(cdc_fifo #(T), "async_fifo")
        fw_mirror #(fw_rv_if #(T))  in;
        fw_bundle #(fw_rv_if #(T))  out;

        virtual function void build();
            in  = fw_mirror #(fw_rv_if #(T))::create("in",  this);
            out = fw_bundle #(fw_rv_if #(T))::create("out", this);
            fw_clock_domain::set(in,  fw_clock_domain::get(this, "wr"));
            fw_clock_domain::set(out, fw_clock_domain::get(this, "rd"));
        endfunction

        virtual function void extern_map(fw_extern_map m);
            m.param("WIDTH", fw_value #(int)::of($bits(T)));
            fw_clock_domain::pin(m, "wr_clk", fw_clock_domain::get(this, "wr"));
            fw_clock_domain::pin(m, "rd_clk", fw_clock_domain::get(this, "rd"));
            fw_reset_domain::pin(m, "wr_rst_n", null, fw_reset_kind::active_low());
            fw_reset_domain::pin(m, "rd_rst_n", null, fw_reset_kind::active_low());
            void'(m.prefix(in,  "in_"));
            void'(m.prefix(out, "out_"));
        endfunction
    endclass

    // A stream from the "wr" clock to the "rd" clock. The FIFO is chosen by
    // how the two clocks relate, which the parent decides.
    class xfer #(type T = bit[7:0]) extends fw_component;
        `fw_component_utils(xfer #(T))
        fw_mirror #(fw_rv_if #(T))  in;
        fw_bundle #(fw_rv_if #(T))  out;
        rv_fifo  #(T)               sf;
        cdc_fifo #(T)               af;

        virtual function void build();
            fw_clock_domain wr = fw_clock_domain::get(this, "wr");
            fw_clock_domain rd = fw_clock_domain::get(this, "rd");
            in  = fw_mirror #(fw_rv_if #(T))::create("in",  this);
            out = fw_bundle #(fw_rv_if #(T))::create("out", this);
            fw_clock_domain::set(in,  wr);
            fw_clock_domain::set(out, rd);
            if (fw_clock_domain::relate(wr, rd) == FW_DOMAIN_SAME) begin
                sf = rv_fifo #(T)::create("sf", this);
                fw_clock_domain::set(sf, wr);
            end else begin
                af = cdc_fifo #(T)::create("af", this);
                fw_clock_domain::set(af, wr, "wr");
                fw_clock_domain::set(af, rd, "rd");
            end
        endfunction

        virtual function void connect();
            if (sf != null) begin
                sf.in.connect_up(in);
                sf.out.connect_up(out);
            end else begin
                af.in.connect_up(in);
                af.out.connect_up(out);
            end
        endfunction
    endclass

    // The design. It creates the domains, makes clk_a and the reset its
    // defaults, puts its output in clk_b, and drives the root domains from
    // its inputs.
    class soc #(type T = bit[7:0]) extends fw_component;
        `fw_component_utils(soc #(T))
        fw_input  #(bit)            clk_a_i, clk_b_i, rst_ni;
        fw_mirror #(fw_rv_if #(T))  in;
        fw_bundle #(fw_rv_if #(T))  out;
        fw_clock_domain             clk_a, clk_b, slow;
        fw_reset_domain             rst;
        div2                        div;
        xfer #(T)                   x1, x2, x3;

        virtual function void build();
            clk_a_i = fw_input #(bit)::create("clk_a_i", this);
            clk_b_i = fw_input #(bit)::create("clk_b_i", this);
            rst_ni  = fw_input #(bit)::create("rst_ni",  this);
            in      = fw_mirror #(fw_rv_if #(T))::create("in",  this);
            out     = fw_bundle #(fw_rv_if #(T))::create("out", this);

            clk_a = fw_clock_domain::create("clk_a", this);
            clk_b = fw_clock_domain::create("clk_b", this);
            slow  = fw_clock_domain::create("slow", this,
                        fw_clock_kind::derived(clk_a, .div(2)));
            rst   = fw_reset_domain::create("rst", this);
            fw_clock_domain::set(this, clk_a);
            fw_reset_domain::set(this, rst);
            fw_clock_domain::set(out, clk_b);

            div = div2::create("div", this);
            fw_clock_domain::set(div, slow, "out");
            x1 = xfer #(T)::create("x1", this);
            fw_clock_domain::set(x1, clk_a, "wr");
            fw_clock_domain::set(x1, clk_a, "rd");
            x2 = xfer #(T)::create("x2", this);
            fw_clock_domain::set(x2, clk_a, "wr");
            fw_clock_domain::set(x2, slow,  "rd");
            x3 = xfer #(T)::create("x3", this);
            fw_clock_domain::set(x3, slow,  "wr");
            fw_clock_domain::set(x3, clk_b, "rd");
        endfunction

        virtual function void connect();
            clk_a.drive(clk_a_i);
            clk_b.drive(clk_b_i);
            rst.drive(rst_ni, fw_reset_kind::active_low());
            x1.in.connect_up(in);
            x1.out.connect(x2.in);
            x2.out.connect(x3.in);
            x3.out.connect_up(out);
        endfunction
    endclass

    // The crossing-error variant: the clk_a side goes straight to the clk_b
    // output, with no crossing element.
    class soc_bad #(type T = bit[7:0]) extends fw_component;
        `fw_component_utils(soc_bad #(T))
        fw_input  #(bit)            clk_a_i, clk_b_i, rst_ni;
        fw_mirror #(fw_rv_if #(T))  in;
        fw_bundle #(fw_rv_if #(T))  out;
        fw_clock_domain             clk_a, clk_b;
        fw_reset_domain             rst;
        xfer #(T)                   x1;

        virtual function void build();
            clk_a_i = fw_input #(bit)::create("clk_a_i", this);
            clk_b_i = fw_input #(bit)::create("clk_b_i", this);
            rst_ni  = fw_input #(bit)::create("rst_ni",  this);
            in      = fw_mirror #(fw_rv_if #(T))::create("in",  this);
            out     = fw_bundle #(fw_rv_if #(T))::create("out", this);
            clk_a = fw_clock_domain::create("clk_a", this);
            clk_b = fw_clock_domain::create("clk_b", this);
            rst   = fw_reset_domain::create("rst", this);
            fw_clock_domain::set(this, clk_a);
            fw_reset_domain::set(this, rst);
            fw_clock_domain::set(out, clk_b);
            x1 = xfer #(T)::create("x1", this);
            fw_clock_domain::set(x1, clk_a, "wr");
            fw_clock_domain::set(x1, clk_a, "rd");
        endfunction

        virtual function void connect();
            clk_a.drive(clk_a_i);
            clk_b.drive(clk_b_i);
            rst.drive(rst_ni, fw_reset_kind::active_low());
            x1.in.connect_up(in);
            x1.out.connect_up(out);       // clk_a into clk_b: FW-E-DOM-CROSS
        endfunction
    endclass
endpackage
