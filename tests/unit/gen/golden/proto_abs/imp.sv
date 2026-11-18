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

endpackage : scenario_gen_pkg
