// Wrapping existing RTL and assembling it with fw-hdl (core layer 1).
//
// Two existing modules, with different pin conventions, are wrapped as
// components whose ports are ready/valid bundles (fw_rv_if):
//
//   rv_fifo     wraps fw_rv_fifo  (in_vld/in_rdy/in_data, active-high rst)
//   axis_slice  wraps axis_reg    (s_axis_tvalid/..., aclk, active-low aresetn,
//                                  and a tlast side band the bundle lacks)
//
// chain connects them: in -> fifo -> slice -> out. fe-sv elaborates chain
// and emits Verilog in which each wrapper is replaced by its module.
`include "fw_hdl_macros.svh"

package wrap_pkg;
    import fw_hdl_pkg::*;

    // fw_rv_fifo as a component. Its pins are named <in|out>_<data|vld|rdy>;
    // the map renames the bundle members valid/ready to vld/rdy, and clk,
    // rst, in_data and out_data follow the default rule.
    class rv_fifo #(type T = bit[7:0]) extends fw_component implements fw_extern_if;
        typedef struct { int depth; } params_t;
        static function params_t mk_params(int depth = 2);
            params_t p;
            p.depth = depth;
            return p;
        endfunction
        `fw_component_params
        `fw_extern_utils(rv_fifo #(T), "fw_rv_fifo")

        fw_input  #(bit)            clk, rst;
        fw_mirror #(fw_rv_if #(T))  in;
        fw_bundle #(fw_rv_if #(T))  out;

        virtual function void build();
            clk = fw_input  #(bit)::create("clk", this);
            rst = fw_input  #(bit)::create("rst", this);
            in  = fw_mirror #(fw_rv_if #(T))::create("in",  this);
            out = fw_bundle #(fw_rv_if #(T))::create("out", this);
        endfunction

        virtual function void extern_map(fw_extern_map m);
            m.param("WIDTH", fw_value #(int)::of($bits(T)));
            m.param("DEPTH", fw_value #(int)::of(params.depth));
            void'(m.prefix(in,  "in_").rename("valid", "vld").rename("ready", "rdy"));
            void'(m.prefix(out, "out_").rename("valid", "vld").rename("ready", "rdy"));
        endfunction
    endclass

    // axis_reg as a component. AXI-Stream pin names come from a prefix rule
    // (s_axis_t + valid/ready/data); the clock and the active-low reset are
    // mapped by name; tlast, which fw_rv_if does not carry, is tied on the
    // input side and left open on the output side.
    class axis_slice #(type T = bit[7:0]) extends fw_component implements fw_extern_if;
        `fw_extern_utils(axis_slice #(T), "axis_reg")

        fw_input  #(bit)            clk, rst_n;
        fw_mirror #(fw_rv_if #(T))  s;
        fw_bundle #(fw_rv_if #(T))  m;

        virtual function void build();
            clk   = fw_input  #(bit)::create("clk",   this);
            rst_n = fw_input  #(bit)::create("rst_n", this);
            s     = fw_mirror #(fw_rv_if #(T))::create("s", this);
            m     = fw_bundle #(fw_rv_if #(T))::create("m", this);
        endfunction

        virtual function void extern_map(fw_extern_map x);
            x.param("DATA_W", fw_value #(int)::of($bits(T)));
            x.pin("aclk",    clk);
            x.pin("aresetn", rst_n);
            void'(x.prefix(s, "s_axis_t"));
            void'(x.prefix(m, "m_axis_t"));
            x.tie("s_axis_tlast", 1);
            x.open("m_axis_tlast");
        endfunction
    endclass

    // The assembly: a FIFO feeding a register slice. Layer 1 has no clock or
    // reset domains, so both resets come in as ports (core-layer1.md §3.6).
    class chain #(type T = bit[7:0]) extends fw_component;
        typedef struct { int depth; } params_t;
        static function params_t mk_params(int depth = 4);
            params_t p;
            p.depth = depth;
            return p;
        endfunction
        `fw_component_params
        `fw_component_utils(chain #(T))

        fw_input  #(bit)            clk, rst, rst_n;
        fw_mirror #(fw_rv_if #(T))  in;
        fw_bundle #(fw_rv_if #(T))  out;
        rv_fifo   #(T)              fifo;
        axis_slice #(T)             slice;

        virtual function void build();
            clk   = fw_input  #(bit)::create("clk",   this);
            rst   = fw_input  #(bit)::create("rst",   this);
            rst_n = fw_input  #(bit)::create("rst_n", this);
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
            fifo.clk.connect(clk);
            fifo.rst.connect(rst);
            slice.clk.connect(clk);
            slice.rst_n.connect(rst_n);
        endfunction
    endclass
endpackage
