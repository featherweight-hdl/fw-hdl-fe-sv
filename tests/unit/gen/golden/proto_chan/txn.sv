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
      if ((t != that.t)) begin
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
      if ((t != that.t)) begin
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

  virtual class test_ch_req_if_txn #(int W = 8);
  
    pure virtual function string sprint();
  
    pure virtual function bit compare(input test_ch_req_if_txn #(W) o, output string diff);
  endclass

  class test_ch_req_if_req_txn #(int W = 8) extends test_ch_req_if_txn #(W);
    rand logic [W-1:0] t;
  
    virtual function string sprint();
      return $sformatf("req(t=0x%0h)", t);
    endfunction
  
    virtual function bit compare(input test_ch_req_if_txn #(W) o, output string diff);
      test_ch_req_if_req_txn #(W) that;
      diff = "";
      if (!($cast(that, o))) begin
        diff = " not a req transaction";
        return 0;
      end
      if ((that == null)) begin
        diff = " null";
        return 0;
      end
      if ((t != that.t)) begin
        diff = {diff, $sformatf(" t: 0x%0h != 0x%0h", t, that.t)};
      end
      return (diff == "");
    endfunction
  endclass

  interface class test_ch_req_if_txn_sink #(int W = 8);
    pure virtual function void put(input test_ch_req_if_txn #(W) t);
  endclass

  class test_ch_req_if_txn_listener #(int W = 8) extends test_ch_req_if_listener_base #(W);
    test_ch_req_if_txn_sink #(W) m_sink;
  
    function new(input test_ch_req_if_txn_sink #(W) sink);
      m_sink = sink;
    endfunction
  
    virtual function void req_post(input logic [W-1:0] t);
      test_ch_req_if_req_txn #(W) __txn = new;
      __txn.t = t;
      m_sink.put(__txn);
    endfunction
  endclass

  class test_ch_req_if_txn_driver #(int W = 8);
    test_ch_req_if #(W) m_api;
  
    function new(input test_ch_req_if #(W) api);
      m_api = api;
    endfunction
  
    virtual task drive(input test_ch_req_if_txn #(W) t);
      test_ch_req_if_req_txn #(W) __req;
      if ($cast(__req, t)) begin
        m_api.req(__req.t);
        return;
      end
    endtask
  endclass

  virtual class test_ch_rsp_if_txn #(int W = 8);
  
    pure virtual function string sprint();
  
    pure virtual function bit compare(input test_ch_rsp_if_txn #(W) o, output string diff);
  endclass

  class test_ch_rsp_if_rsp_txn #(int W = 8) extends test_ch_rsp_if_txn #(W);
    rand scenario::test_ch_rsp_t t;
  
    virtual function string sprint();
      return $sformatf("rsp(t=%p)", t);
    endfunction
  
    virtual function bit compare(input test_ch_rsp_if_txn #(W) o, output string diff);
      test_ch_rsp_if_rsp_txn #(W) that;
      diff = "";
      if (!($cast(that, o))) begin
        diff = " not a rsp transaction";
        return 0;
      end
      if ((that == null)) begin
        diff = " null";
        return 0;
      end
      if ((t != that.t)) begin
        diff = {diff, $sformatf(" t: %p != %p", t, that.t)};
      end
      return (diff == "");
    endfunction
  endclass

  interface class test_ch_rsp_if_txn_sink #(int W = 8);
    pure virtual function void put(input test_ch_rsp_if_txn #(W) t);
  endclass

  class test_ch_rsp_if_txn_listener #(int W = 8) extends test_ch_rsp_if_listener_base #(W);
    test_ch_rsp_if_txn_sink #(W) m_sink;
  
    function new(input test_ch_rsp_if_txn_sink #(W) sink);
      m_sink = sink;
    endfunction
  
    virtual function void rsp_post(input scenario::test_ch_rsp_t t);
      test_ch_rsp_if_rsp_txn #(W) __txn = new;
      __txn.t = t;
      m_sink.put(__txn);
    endfunction
  endclass

  class test_ch_rsp_if_txn_driver #(int W = 8);
    test_ch_rsp_if #(W) m_api;
  
    function new(input test_ch_rsp_if #(W) api);
      m_api = api;
    endfunction
  
    virtual task drive(input test_ch_rsp_if_txn #(W) t);
      test_ch_rsp_if_rsp_txn #(W) __rsp;
      if ($cast(__rsp, t)) begin
        m_api.rsp(__rsp.t);
        return;
      end
    endtask
  endclass

endpackage : scenario_gen_pkg
