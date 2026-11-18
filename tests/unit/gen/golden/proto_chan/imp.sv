package scenario_gen_pkg;

  import fw_hdl_pkg::*;
  import scenario::*;

  class test_put_if_imp #(type T = int, type IMP = int) implements test_put_if #(T);
    IMP m_imp;
  
    function new(input IMP imp);
      m_imp = imp;
    endfunction
  
    virtual task put(input T t);
      m_imp.put(t);
    endtask
  endclass

  class test_get_if_imp #(type T = int, type IMP = int) implements test_get_if #(T);
    IMP m_imp;
  
    function new(input IMP imp);
      m_imp = imp;
    endfunction
  
    virtual task get(output T t);
      m_imp.get(t);
    endtask
  endclass

  class test_ch_req_if_imp #(int W = 8, type IMP = int) implements test_ch_req_if #(W);
    IMP m_imp;
  
    function new(input IMP imp);
      m_imp = imp;
    endfunction
  
    virtual task req(input logic [W-1:0] t);
      m_imp.req(t);
    endtask
  endclass

  class test_ch_rsp_if_imp #(int W = 8, type IMP = int) implements test_ch_rsp_if #(W);
    IMP m_imp;
  
    function new(input IMP imp);
      m_imp = imp;
    endfunction
  
    virtual task rsp(input scenario::test_ch_rsp_t t);
      m_imp.rsp(t);
    endtask
  endclass

endpackage : scenario_gen_pkg
