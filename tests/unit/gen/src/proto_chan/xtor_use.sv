// Names the 8-bit specializations of the generated transactors, so a scan
// of the generated package registers them (a scan sees a generic class only
// through its specializations).
package xtor_use_pkg;
    import scenario_gen_pkg::*;
    typedef test_ch_initiator #(8) xi8_t;
    typedef test_ch_target #(8)    xt8_t;
    typedef test_ch_monitor #(8)   xm8_t;
endpackage
