package scenario_gen_pkg;

  import fw_hdl_pkg::*;
  import scenario::*;

  virtual class test_put_if_txn #(type T = int);
  
    pure virtual function string sprint();
  
    pure virtual function bit compare(input test_put_if_txn #(T) o, output string diff);
  endclass

  class test_put_if_put_txn #(type T = int) extends test_put_if_txn #(T);
    rand T t;
  
    virtual function string sprint();
      return $sformatf("put(t=%p)", t);
    endfunction
  
    virtual function bit compare(input test_put_if_txn #(T) o, output string diff);
      test_put_if_put_txn #(T) that;
      diff = "";
      if (!($cast(that, o))) begin
        diff = " not a put transaction";
        return 0;
      end
      if ((that == null)) begin
        diff = " null";
        return 0;
      end
      if ((t !== that.t)) begin
        diff = {diff, $sformatf(" t: %p != %p", t, that.t)};
      end
      return (diff == "");
    endfunction
  endclass

  interface class test_put_if_txn_sink #(type T = int);
    pure virtual function void put(input test_put_if_txn #(T) t);
  endclass

  class test_put_if_txn_listener #(type T = int) extends test_put_if_listener_base #(T);
    test_put_if_txn_sink #(T) m_sink;
  
    function new(input test_put_if_txn_sink #(T) sink);
      m_sink = sink;
    endfunction
  
    virtual function void put_post(input T t);
      test_put_if_put_txn #(T) __txn = new;
      __txn.t = t;
      m_sink.put(__txn);
    endfunction
  endclass

  class test_put_if_txn_driver #(type T = int);
    test_put_if #(T) m_api;
  
    function new(input test_put_if #(T) api);
      m_api = api;
    endfunction
  
    virtual task drive(input test_put_if_txn #(T) t);
      test_put_if_put_txn #(T) __put;
      if ($cast(__put, t)) begin
        m_api.put(__put.t);
        return;
      end
    endtask
  endclass

  virtual class test_get_if_txn #(type T = int);
  
    pure virtual function string sprint();
  
    pure virtual function bit compare(input test_get_if_txn #(T) o, output string diff);
  endclass

  class test_get_if_get_txn #(type T = int) extends test_get_if_txn #(T);
    rand T t;
  
    virtual function string sprint();
      return $sformatf("get(t=%p)", t);
    endfunction
  
    virtual function bit compare(input test_get_if_txn #(T) o, output string diff);
      test_get_if_get_txn #(T) that;
      diff = "";
      if (!($cast(that, o))) begin
        diff = " not a get transaction";
        return 0;
      end
      if ((that == null)) begin
        diff = " null";
        return 0;
      end
      if ((t !== that.t)) begin
        diff = {diff, $sformatf(" t: %p != %p", t, that.t)};
      end
      return (diff == "");
    endfunction
  endclass

  interface class test_get_if_txn_sink #(type T = int);
    pure virtual function void put(input test_get_if_txn #(T) t);
  endclass

  class test_get_if_txn_listener #(type T = int) extends test_get_if_listener_base #(T);
    test_get_if_txn_sink #(T) m_sink;
  
    function new(input test_get_if_txn_sink #(T) sink);
      m_sink = sink;
    endfunction
  
    virtual function void get_post(input T t);
      test_get_if_get_txn #(T) __txn = new;
      __txn.t = t;
      m_sink.put(__txn);
    endfunction
  endclass

  class test_get_if_txn_driver #(type T = int);
    test_get_if #(T) m_api;
  
    function new(input test_get_if #(T) api);
      m_api = api;
    endfunction
  
    virtual task drive(input test_get_if_txn #(T) t);
      test_get_if_get_txn #(T) __get;
      if ($cast(__get, t)) begin
        m_api.get(__get.t);
        return;
      end
    endtask
  endclass

endpackage : scenario_gen_pkg
