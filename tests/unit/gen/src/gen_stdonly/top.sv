// A kit that registers only a std API with a builtin payload: its package
// is the protocol's (scenario), not std's.
`include "fw_hdl_macros.svh"
package scenario;
    import fw_hdl_pkg::*;
    import fw_hdl_std_pkg::*;
    class so_proto extends fw_protocol;
        `fw_protocol_utils(so_proto)
        static function void declare(fw_protocol_decl d);
            d.api(fw_api_type_of #(fw_put_if #(byte))::get(), FW_PROTO_REQ);
        endfunction
    endclass
endpackage
