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

endpackage : scenario_gen_pkg
