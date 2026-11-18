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
    typedef T __fw_self_t; \
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

// A domain kind's handle: fe-sv's intrinsic for fw_domain_kind_type_of::get
// returns one object per kind.
`define fw_domain_kind_utils(T) \
    static function fw_domain_kind_type get_type(); \
        return fw_domain_kind_type_of #(T)::get(); \
    endfunction

`define fw_component_param_utils(C) \
    `fw_component_utils(this_t)

// A protocol's handle: fe-sv's intrinsic for fw_protocol_type_of::get
// returns one object per class and runs its declare() the first time.
`define fw_protocol_utils(C) \
    static function fw_protocol_type get_type(); \
        return fw_protocol_type_of #(C)::get(`"C`"); \
    endfunction

`define fw_protocol_param_utils(C) \
    static function fw_protocol_type get_type(); \
        return fw_protocol_type_of #(this_t)::get(`"C`"); \
    endfunction

// fe-sv reads the __fw_ members below to register transactors,
// abstractors and bundle protocols. __fw_ctor names the class's
// fw_ctor_of, so the abstractors() and abstractor_for queries can return
// one for a class the design names but never creates.
`define fw_xtor_utils(C, PROTO) \
    `fw_component_utils(C) \
    static function fw_protocol_type __fw_xtor_protocol(); \
        return PROTO::get_type(); \
    endfunction \
    static function fw_ctor #(fw_component) __fw_ctor(); \
        return fw_ctor_of #(fw_component, C)::get(); \
    endfunction

`define fw_xtor_param_utils(C, PROTO) \
    `fw_xtor_utils(this_t, PROTO)

`define fw_abstractor_utils(C, FROM, TO) \
    `fw_component_utils(C) \
    static function fw_protocol_type __fw_abs_from(); \
        return FROM::get_type(); \
    endfunction \
    static function fw_protocol_type __fw_abs_to(); \
        return TO::get_type(); \
    endfunction \
    static function fw_ctor #(fw_component) __fw_ctor(); \
        return fw_ctor_of #(fw_component, C)::get(); \
    endfunction

`define fw_abstractor_param_utils(C, FROM, TO) \
    `fw_abstractor_utils(this_t, FROM, TO)

`define fw_bundle_protocol(PROTO) \
    static function fw_protocol_type __fw_bundle_protocol(); \
        return PROTO::get_type(); \
    endfunction

// A named comb or step block: fe-sv's ProcMapper lowers the nested
// __fw_comb_/__fw_step_ class's method (the call of name()) to one
// always_comb / always_ff.
`define fw_comb(name) \
    class __fw_comb_``name implements fw_comb_if; \
        __fw_self_t __fw_p; \
        function new(__fw_self_t p); __fw_p = p; endfunction \
        virtual function void comb(); __fw_p.name(); endfunction \
    endclass \
    __fw_comb_``name __fw_comb_``name``_i = new(this);

`define fw_step(name) \
    class __fw_step_``name implements fw_step_if; \
        __fw_self_t __fw_p; \
        function new(__fw_self_t p); __fw_p = p; endfunction \
        virtual function void step(); __fw_p.name(); endfunction \
    endclass \
    __fw_step_``name __fw_step_``name``_i = new(this);

`endif // FW_HDL_MACROS_SVH
