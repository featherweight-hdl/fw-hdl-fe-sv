// FW-W-GEN-4STATE: a channel payload struct with a logic member.
`include "fw_hdl_macros.svh"
package scenario;
    import fw_hdl_pkg::*;
    import fw_hdl_std_pkg::*;
    typedef struct packed { logic [1:0] code; bit [7:0] tag; } fs_req_t;
    class fs_proto extends fw_protocol;
        `fw_protocol_utils(fs_proto)
        static function void declare(fw_protocol_decl d);
            d.api(fw_api_type_of #(fw_put_if #(fs_req_t))::get(), FW_PROTO_REQ);
        endfunction
    endclass
    virtual class fs_bundle extends fw_bundle_base;
        `fw_bundle_protocol(fs_proto)
        fw_bundle #(fw_rv_if #(fs_req_t)) req;
        virtual function void build();
            req = fw_bundle #(fw_rv_if #(fs_req_t))::create("req", this);
        endfunction
    endclass
endpackage
