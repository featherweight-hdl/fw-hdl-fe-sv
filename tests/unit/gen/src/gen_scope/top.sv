// FW-E-GEN-SCOPE: a channel payload struct declared inside the bundle class.
`include "fw_hdl_macros.svh"
package scenario;
    import fw_hdl_pkg::*;
    import fw_hdl_std_pkg::*;
    class ls_proto extends fw_protocol;
        `fw_protocol_utils(ls_proto)
        static function void declare(fw_protocol_decl d);
        endfunction
    endclass
    virtual class ls_bundle extends fw_bundle_base;
        typedef struct packed { bit [1:0] code; bit [7:0] tag; } req_t;
        `fw_bundle_protocol(ls_proto)
        fw_bundle #(fw_rv_if #(req_t)) req;
        virtual function void build();
            req = fw_bundle #(fw_rv_if #(req_t))::create("req", this);
        endfunction
    endclass
endpackage
