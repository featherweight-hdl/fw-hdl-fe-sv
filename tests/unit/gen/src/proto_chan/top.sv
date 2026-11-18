// A protocol whose bundle is a bundle of channels: a request channel of a
// W-bit word (fwd) and a response channel of a status struct (rev). The
// kit derives test_ch_req_if and test_ch_rsp_if from the bundle.
`include "fw_hdl_macros.svh"

package scenario;
    import fw_hdl_pkg::*;
    import fw_hdl_std_pkg::*;
    `include "test_apis.svh"

    // One response.
    typedef struct packed {
        bit [1:0] code;
        bit [7:0] tag;
    } test_ch_rsp_t;

    class test_ch_proto #(int W = 8) extends fw_protocol;
        typedef test_ch_proto #(W) this_t;
        `fw_protocol_param_utils(test_ch_proto)
        static function void declare(fw_protocol_decl d);
            d.api(fw_api_type_of #(test_put_if #(logic [W-1:0]))::get(), FW_PROTO_REQ);
        endfunction
    endclass

    virtual class test_ch_bundle #(int W = 8) extends fw_bundle_base;
        `fw_bundle_protocol(test_ch_proto #(W))
        fw_bundle #(fw_rv_if #(logic [W-1:0])) req;
        fw_mirror #(fw_rv_if #(test_ch_rsp_t)) rsp;
        virtual function void build();
            req = fw_bundle #(fw_rv_if #(logic [W-1:0]))::create("req", this);
            rsp = fw_mirror #(fw_rv_if #(test_ch_rsp_t))::create("rsp", this);
        endfunction
    endclass

    class top extends fw_component;
        `fw_component_utils(top)
        fw_bundle #(test_ch_bundle #(8))  a;
        fw_mirror #(test_ch_bundle #(16)) b;
        virtual function void build();
            a = fw_bundle #(test_ch_bundle #(8))::create("a", this);
            b = fw_mirror #(test_ch_bundle #(16))::create("b", this);
        endfunction
    endclass

endpackage
