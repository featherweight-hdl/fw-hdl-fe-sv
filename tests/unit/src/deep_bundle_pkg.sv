// A port of nested bundles for test_walk_port.py: deep_bundle (protocol
// deep_proto) holds two pair_bundles (no protocol) of rv channels, one of
// them mirrored; dut mirrors the whole port. dut_q's port q has no
// protocol at its own level (gen_tb rejects it).
`include "fw_hdl_macros.svh"

package deep_pkg;
    import fw_hdl_pkg::*;
    import fw_hdl_std_pkg::*;

    interface class deep_if;
        pure virtual task go(input bit [7:0] x);
    endclass

    class deep_proto extends fw_protocol;
        `fw_protocol_utils(deep_proto)
        static function void declare(fw_protocol_decl d);
            d.api(fw_api_type_of #(deep_if)::get(), FW_PROTO_REQ);
        endfunction
    endclass

    // No protocol of its own: two rv channels, one each way.
    virtual class pair_bundle extends fw_bundle_base;
        fw_bundle #(fw_rv_if #(bit [7:0])) x;
        fw_mirror #(fw_rv_if #(bit [3:0])) y;
        virtual function void build();
            x = fw_bundle #(fw_rv_if #(bit [7:0]))::create("x", this);
            y = fw_mirror #(fw_rv_if #(bit [3:0]))::create("y", this);
        endfunction
    endclass

    // A protocol over a bundle of bundles of channels; b is mirrored.
    virtual class deep_bundle extends fw_bundle_base;
        `fw_bundle_protocol(deep_proto)
        fw_output #(bit) en;
        fw_bundle #(pair_bundle) a;
        fw_mirror #(pair_bundle) b;
        virtual function void build();
            en = fw_output #(bit)::create("en", this);
            a = fw_bundle #(pair_bundle)::create("a", this);
            b = fw_mirror #(pair_bundle)::create("b", this);
        endfunction
    endclass

    class dut extends fw_component;
        `fw_component_utils(dut)
        fw_input  #(bit)          clk_i, rst_i;
        fw_mirror #(deep_bundle)  p;
        fw_clock_domain           clk;
        fw_reset_domain           rst;

        virtual function void build();
            clk_i = fw_input #(bit)::create("clk_i", this);
            rst_i = fw_input #(bit)::create("rst_i", this);
            clk   = fw_clock_domain::create("clk", this);
            rst   = fw_reset_domain::create("rst", this, fw_reset_kind::root(.clock(clk)));
            fw_clock_domain::set(this, clk);
            fw_reset_domain::set(this, rst);
            p     = fw_mirror #(deep_bundle)::create("p", this);
        endfunction

        virtual function void connect();
            clk.drive(clk_i);
            rst.drive(rst_i);
        endfunction
    endclass

    class dut_q extends fw_component;
        `fw_component_utils(dut_q)
        fw_input  #(bit)          clk_i, rst_i;
        fw_bundle #(pair_bundle)  q;
        fw_clock_domain           clk;
        fw_reset_domain           rst;

        virtual function void build();
            clk_i = fw_input #(bit)::create("clk_i", this);
            rst_i = fw_input #(bit)::create("rst_i", this);
            clk   = fw_clock_domain::create("clk", this);
            rst   = fw_reset_domain::create("rst", this, fw_reset_kind::root(.clock(clk)));
            fw_clock_domain::set(this, clk);
            fw_reset_domain::set(this, rst);
            q     = fw_bundle #(pair_bundle)::create("q", this);
        endfunction

        virtual function void connect();
            clk.drive(clk_i);
            rst.drive(rst_i);
        endfunction
    endclass
endpackage
