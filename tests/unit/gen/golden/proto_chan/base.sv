package scenario_gen_pkg;

  import fw_hdl_pkg::*;
  import scenario::*;

  virtual class test_put_if_base #(type T = int) implements test_put_if #(T);
  
    virtual task put(input T t);
      $error("test_put_if_base::put: not implemented");
    endtask
  endclass

  virtual class test_get_if_base #(type T = int) implements test_get_if #(T);
  
    virtual task get(output T t);
      $error("test_get_if_base::get: not implemented");
    endtask
  endclass

  virtual class test_ch_req_if_base #(int W = 8) implements test_ch_req_if #(W);
  
    virtual task req(input logic [W-1:0] t);
      $error("test_ch_req_if_base::req: not implemented");
    endtask
  endclass

  virtual class test_ch_rsp_if_base #(int W = 8) implements test_ch_rsp_if #(W);
  
    virtual task rsp(input scenario::test_ch_rsp_t t);
      $error("test_ch_rsp_if_base::rsp: not implemented");
    endtask
  endclass

endpackage : scenario_gen_pkg
