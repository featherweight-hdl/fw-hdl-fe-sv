// fe-sv's expansions of the fw-hdl API macros (core-layer1.md §4.6).
//
// fe-sv never runs these bodies as SV: its interpreter replaces every
// `__fw_` method and every method of the API with an intrinsic. The
// expansions declare only the API's members plus `__fw_` names (§4.1).
`ifndef FW_HDL_MACROS_SVH
`define FW_HDL_MACROS_SVH

// create() hands off to __fw_create, which fe-sv's intrinsic implements:
// allocate, name, resolve params, construct.
`define fw_component_utils(T) \
    static function T create(string name, fw_component parent, \
                             params_t p = mk_params()); \
        return __fw_create(name, parent, p); \
    endfunction \
    static function T __fw_create(string name, fw_component parent, params_t p); \
        T obj = new(); \
        return obj; \
    endfunction

`define fw_extern_utils(T, MODULE) \
    `fw_component_utils(T) \
    virtual function string extern_module(); \
        return MODULE; \
    endfunction

`define fw_component_params \
    params_t params;

// __fw_init_params marks the class that declares params as having an
// init function; resolution calls it last.
`define fw_component_params_init(FN) \
    params_t params; \
    static function void __fw_init_params(ref params_t p); \
        FN(p); \
    endfunction

`endif // FW_HDL_MACROS_SVH
