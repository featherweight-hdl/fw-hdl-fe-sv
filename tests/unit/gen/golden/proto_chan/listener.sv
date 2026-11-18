package scenario_gen_pkg;

  import fw_hdl_pkg::*;
  import scenario::*;

  interface class test_put_if_listener #(type T = int);
    pure virtual function void put_pre(input T t);
    pure virtual function void put_post(input T t);
  endclass

  virtual class test_put_if_listener_base #(type T = int) implements test_put_if_listener #(T);
  
    virtual function void put_pre(input T t);
    endfunction
  
    virtual function void put_post(input T t);
    endfunction
  endclass

  class test_put_if_listener_fanout #(type T = int) implements test_put_if_listener #(T);
    test_put_if_listener #(T) m_listeners [$];
  
    virtual function void add_listener(input test_put_if_listener #(T) l);
      m_listeners.push_back(l);
    endfunction
  
    virtual function void remove_listener(input test_put_if_listener #(T) l);
      foreach (m_listeners[i]) begin
        if ((m_listeners[i] == l)) begin
          m_listeners.delete(i);
          return;
        end
      end
    endfunction
  
    virtual function void put_pre(input T t);
      foreach (m_listeners[i]) begin
        m_listeners[i].put_pre(t);
      end
    endfunction
  
    virtual function void put_post(input T t);
      foreach (m_listeners[i]) begin
        m_listeners[i].put_post(t);
      end
    endfunction
  endclass

  interface class test_get_if_listener #(type T = int);
    pure virtual function void get_pre();
    pure virtual function void get_post(input T t);
  endclass

  virtual class test_get_if_listener_base #(type T = int) implements test_get_if_listener #(T);
  
    virtual function void get_pre();
    endfunction
  
    virtual function void get_post(input T t);
    endfunction
  endclass

  class test_get_if_listener_fanout #(type T = int) implements test_get_if_listener #(T);
    test_get_if_listener #(T) m_listeners [$];
  
    virtual function void add_listener(input test_get_if_listener #(T) l);
      m_listeners.push_back(l);
    endfunction
  
    virtual function void remove_listener(input test_get_if_listener #(T) l);
      foreach (m_listeners[i]) begin
        if ((m_listeners[i] == l)) begin
          m_listeners.delete(i);
          return;
        end
      end
    endfunction
  
    virtual function void get_pre();
      foreach (m_listeners[i]) begin
        m_listeners[i].get_pre();
      end
    endfunction
  
    virtual function void get_post(input T t);
      foreach (m_listeners[i]) begin
        m_listeners[i].get_post(t);
      end
    endfunction
  endclass

  interface class test_ch_req_if_listener #(int W = 8);
    pure virtual function void req_pre(input logic [W-1:0] t);
    pure virtual function void req_post(input logic [W-1:0] t);
  endclass

  virtual class test_ch_req_if_listener_base #(int W = 8) implements test_ch_req_if_listener #(W);
  
    virtual function void req_pre(input logic [W-1:0] t);
    endfunction
  
    virtual function void req_post(input logic [W-1:0] t);
    endfunction
  endclass

  class test_ch_req_if_listener_fanout #(int W = 8) implements test_ch_req_if_listener #(W);
    test_ch_req_if_listener #(W) m_listeners [$];
  
    virtual function void add_listener(input test_ch_req_if_listener #(W) l);
      m_listeners.push_back(l);
    endfunction
  
    virtual function void remove_listener(input test_ch_req_if_listener #(W) l);
      foreach (m_listeners[i]) begin
        if ((m_listeners[i] == l)) begin
          m_listeners.delete(i);
          return;
        end
      end
    endfunction
  
    virtual function void req_pre(input logic [W-1:0] t);
      foreach (m_listeners[i]) begin
        m_listeners[i].req_pre(t);
      end
    endfunction
  
    virtual function void req_post(input logic [W-1:0] t);
      foreach (m_listeners[i]) begin
        m_listeners[i].req_post(t);
      end
    endfunction
  endclass

  interface class test_ch_rsp_if_listener #(int W = 8);
    pure virtual function void rsp_pre(input scenario::test_ch_rsp_t t);
    pure virtual function void rsp_post(input scenario::test_ch_rsp_t t);
  endclass

  virtual class test_ch_rsp_if_listener_base #(int W = 8) implements test_ch_rsp_if_listener #(W);
  
    virtual function void rsp_pre(input scenario::test_ch_rsp_t t);
    endfunction
  
    virtual function void rsp_post(input scenario::test_ch_rsp_t t);
    endfunction
  endclass

  class test_ch_rsp_if_listener_fanout #(int W = 8) implements test_ch_rsp_if_listener #(W);
    test_ch_rsp_if_listener #(W) m_listeners [$];
  
    virtual function void add_listener(input test_ch_rsp_if_listener #(W) l);
      m_listeners.push_back(l);
    endfunction
  
    virtual function void remove_listener(input test_ch_rsp_if_listener #(W) l);
      foreach (m_listeners[i]) begin
        if ((m_listeners[i] == l)) begin
          m_listeners.delete(i);
          return;
        end
      end
    endfunction
  
    virtual function void rsp_pre(input scenario::test_ch_rsp_t t);
      foreach (m_listeners[i]) begin
        m_listeners[i].rsp_pre(t);
      end
    endfunction
  
    virtual function void rsp_post(input scenario::test_ch_rsp_t t);
      foreach (m_listeners[i]) begin
        m_listeners[i].rsp_post(t);
      end
    endfunction
  endclass

endpackage : scenario_gen_pkg
