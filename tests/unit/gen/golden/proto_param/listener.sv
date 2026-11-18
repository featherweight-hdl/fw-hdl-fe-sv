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

endpackage : scenario_gen_pkg
