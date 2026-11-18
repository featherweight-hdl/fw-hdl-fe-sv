// Back-to-back of the generated transactor pair (K9.7b): a host calls req()
// through its socket into test_ch_initiator; the initiator drives the
// bundle's pins into test_ch_target, whose socket reaches a device; the
// device answers each req with rsp(), which comes back over the rsp channel
// to the host. test_ch_monitor taps the same pins. No RTL: the root clock's
// period drives the run (+fw_edges). The test supplies the module.
`include "fw_hdl_macros.svh"

package xtor_b2b_pkg;
    import fw_hdl_pkg::*;
    import fw_hdl_std_pkg::*;
    import scenario::*;
    import scenario_gen_pkg::*;

    function automatic int cyc();
        return int'($time / 10ns);
    endfunction

    class host_c extends fw_component implements test_ch_rsp_if #(8), fw_run_if;
        `fw_component_utils(host_c)
        fw_socket #(test_ch_req_if #(8), test_ch_rsp_if #(8)) s;
        string got[$];
        virtual function void build();
            s = fw_socket #(test_ch_req_if #(8), test_ch_rsp_if #(8))::create("s", this);
        endfunction
        virtual task rsp(input test_ch_rsp_t t);
            got.push_back($sformatf("%0d:%0d@%0d", t.code, t.tag, cyc()));
        endtask
        virtual task run();
            for (int k = 1; k <= 4; k++) s.get_if().req(8'(k));
        endtask
    endclass

    class dev_c extends fw_component implements test_ch_req_if #(8);
        `fw_component_utils(dev_c)
        fw_socket #(test_ch_rsp_if #(8), test_ch_req_if #(8)) s;
        string got[$];
        virtual function void build();
            s = fw_socket #(test_ch_rsp_if #(8), test_ch_req_if #(8))::create("s", this);
        endfunction
        virtual task req(input logic [7:0] t);
            test_ch_rsp_t r;
            got.push_back($sformatf("%0d@%0d", t, cyc()));
            r.code = 2'd1;
            r.tag = t + 8'd100;
            s.get_if().rsp(r);
        endtask
    endclass

    class top_c extends fw_component;
        `fw_component_utils(top_c)
        fw_clock_domain        clk;
        host_c                 h;
        test_ch_initiator #(8) xi;
        test_ch_target #(8)    xt;
        test_ch_monitor #(8)   m;
        dev_c                  d;
        virtual function void build();
            clk = fw_clock_domain::create("clk", this, fw_clock_kind::root(10ns));
            fw_clock_domain::set(this, clk);
            h  = host_c::create("h", this);
            xi = test_ch_initiator #(8)::create("xi", this);
            xt = test_ch_target #(8)::create("xt", this);
            m  = test_ch_monitor #(8)::create("m", this);
            d  = dev_c::create("d", this);
        endfunction
        virtual function void connect();
            h.s.connect(xi.s);
            xi.pins.connect(xt.pins);
            m.pins.connect(xi.pins);
            xt.s.connect(d.s);
        endfunction
    endclass
endpackage
