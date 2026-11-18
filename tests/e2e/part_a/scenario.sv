// Part A exit: std's rv protocol. src calls sink's put through a port
// bound to sink's export (sink implements the protocol's request API);
// src's rv bundle drives sink's rv mirror, which carry the protocol.
`include "fw_hdl_macros.svh"

package scenario;
    import fw_hdl_pkg::*;
    import fw_hdl_std_pkg::*;

    class src extends fw_component;
        `fw_component_utils(src)
        fw_port #(fw_put_if #(byte))     p;
        fw_bundle #(fw_rv_if #(byte))    b;
        virtual function void build();
            p = fw_port #(fw_put_if #(byte))::create("p", this);
            b = fw_bundle #(fw_rv_if #(byte))::create("b", this);
        endfunction
    endclass

    // Logs each put, so the test can check where the target runs.
    class sink extends fw_component implements fw_put_if #(byte);
        `fw_component_utils(sink)
        fw_export #(fw_put_if #(byte))   e;
        fw_mirror #(fw_rv_if #(byte))    m;
        string log [$];
        virtual function void build();
            e = fw_export #(fw_put_if #(byte))::create("e", this);
            m = fw_mirror #(fw_rv_if #(byte))::create("m", this);
        endfunction
        virtual task put(input byte t);
            log.push_back($sformatf("put %0d", t));
        endtask
    endclass

    class top extends fw_component;
        `fw_component_utils(top)
        src  a;
        sink k;
        virtual function void build();
            a = src::create("a", this);
            k = sink::create("k", this);
        endfunction
        virtual function void connect();
            a.p.connect(k.e);
            a.b.connect(k.m);
        endfunction
    endclass
endpackage
