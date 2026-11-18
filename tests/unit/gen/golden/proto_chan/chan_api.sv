package scenario_gen_pkg;

  import fw_hdl_pkg::*;
  import scenario::*;

  interface class test_ch_req_if #(int W = 8);
    pure virtual task req(input logic [W-1:0] t);
  endclass

  interface class test_ch_rsp_if #(int W = 8);
    pure virtual task rsp(input scenario::test_ch_rsp_t t);
  endclass

endpackage : scenario_gen_pkg
