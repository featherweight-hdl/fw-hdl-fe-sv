// Wrapping existing RTL and assembling it with fw-hdl.
//
// Two existing modules, with different pin conventions, are wrapped as
// components whose ports are ready/valid bundles (fw_rv_if):
//
//   rv_fifo     wraps fw_rv_fifo  (in_vld/in_rdy/in_data, active-high rst)
//   axis_slice  wraps axis_reg    (s_axis_tvalid/..., aclk, active-low aresetn,
//                                  and a tlast side band the bundle lacks)
//
// chain connects them: in -> fifo -> slice -> out. Clock and reset are
// domains: each wrapper maps its clock and reset pins to its domains, and
// chain creates the domains and drives them from its inputs. Nothing wires
// a clock or a reset by hand. fe-sv elaborates chain and emits Verilog in
// which each wrapper is replaced by its module, and the active-low aresetn
// gets an inverted reset.
`include "fw_hdl_macros.svh"

package wrap_pkg;
    import fw_hdl_pkg::*;

    // fw_rv_fifo as a component. Its pins are named <in|out>_<data|vld|rdy>;
    // the map renames the bundle members valid/ready to vld/rdy, and in_data
    // and out_data follow the default rule. clk and rst are domain pins: the
    // component's clock and its (active-high) reset.
    class rv_fifo #(type T = bit[7:0]) extends fw_component implements fw_extern_if;
        typedef struct { int depth; } params_t;
        static function params_t mk_params(int depth = 2);
            params_t p;
            p.depth = depth;
            return p;
        endfunction
        `fw_component_params
        `fw_extern_utils(rv_fifo #(T), "fw_rv_fifo")

        fw_mirror #(fw_rv_if #(T))  in;
        fw_bundle #(fw_rv_if #(T))  out;

        virtual function void build();
            in  = fw_mirror #(fw_rv_if #(T))::create("in",  this);
            out = fw_bundle #(fw_rv_if #(T))::create("out", this);
        endfunction

        virtual function void extern_map(fw_extern_map m);
            m.param("WIDTH", fw_value #(int)::of($bits(T)));
            m.param("DEPTH", fw_value #(int)::of(params.depth));
            fw_clock_domain::pin(m, "clk");
            fw_reset_domain::pin(m, "rst");
            void'(m.prefix(in,  "in_").rename("valid", "vld").rename("ready", "rdy"));
            void'(m.prefix(out, "out_").rename("valid", "vld").rename("ready", "rdy"));
        endfunction
    endclass

    // axis_reg as a component. AXI-Stream pin names come from a prefix rule
    // (s_axis_t + valid/ready/data); aclk and aresetn are domain pins, the
    // reset one expecting active low; tlast, which fw_rv_if does not carry,
    // is tied on the input side and left open on the output side.
    class axis_slice #(type T = bit[7:0]) extends fw_component implements fw_extern_if;
        `fw_extern_utils(axis_slice #(T), "axis_reg")

        fw_mirror #(fw_rv_if #(T))  s;
        fw_bundle #(fw_rv_if #(T))  m;

        virtual function void build();
            s = fw_mirror #(fw_rv_if #(T))::create("s", this);
            m = fw_bundle #(fw_rv_if #(T))::create("m", this);
        endfunction

        virtual function void extern_map(fw_extern_map x);
            x.param("DATA_W", fw_value #(int)::of($bits(T)));
            fw_clock_domain::pin(x, "aclk");
            fw_reset_domain::pin(x, "aresetn", null, fw_reset_kind::active_low());
            void'(x.prefix(s, "s_axis_t"));
            void'(x.prefix(m, "m_axis_t"));
            x.tie("s_axis_tlast", 1);
            x.open("m_axis_tlast");
        endfunction
    endclass

    // The assembly: a FIFO feeding a register slice. chain creates a clock
    // and a reset domain, makes them its defaults (its children inherit
    // them), and drives them from its two inputs. The reset net is active
    // high; the slice's aresetn gets it inverted.
    class chain #(type T = bit[7:0]) extends fw_component;
        typedef struct { int depth; } params_t;
        static function params_t mk_params(int depth = 4);
            params_t p;
            p.depth = depth;
            return p;
        endfunction
        `fw_component_params
        `fw_component_utils(chain #(T))

        fw_input  #(bit)            clk_i, rst_i;
        fw_mirror #(fw_rv_if #(T))  in;
        fw_bundle #(fw_rv_if #(T))  out;
        fw_clock_domain             clk;
        fw_reset_domain             rst;
        rv_fifo   #(T)              fifo;
        axis_slice #(T)             slice;

        virtual function void build();
            clk_i = fw_input  #(bit)::create("clk_i", this);
            rst_i = fw_input  #(bit)::create("rst_i", this);
            clk   = fw_clock_domain::create("clk", this);
            rst   = fw_reset_domain::create("rst", this, fw_reset_kind::root(.clock(clk)));
            fw_clock_domain::set(this, clk);
            fw_reset_domain::set(this, rst);
            in    = fw_mirror #(fw_rv_if #(T))::create("in",  this);
            out   = fw_bundle #(fw_rv_if #(T))::create("out", this);
            fifo  = rv_fifo #(T)::create("fifo", this,
                                         rv_fifo #(T)::mk_params(.depth(params.depth)));
            slice = axis_slice #(T)::create("slice", this);
        endfunction

        virtual function void connect();
            fifo.in.connect_up(in);         // pass-through: child mirror to own mirror
            fifo.out.connect(slice.s);      // siblings: initiator to target
            slice.m.connect_up(out);        // pass-through: child bundle to own bundle
            clk.drive(clk_i);
            rst.drive(rst_i);               // an active-high net
        endfunction
    endclass
endpackage
