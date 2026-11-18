package scenario_gen_pkg;

  import fw_hdl_pkg::*;
  import scenario::*;

  class test_put_if_proxy #(type T = int) implements test_put_if #(T);
    test_put_if #(T) m_target;
    test_put_if_listener #(T) m_listeners [$];
  
    function new(input test_put_if #(T) target = null);
      m_target = target;
    endfunction
  
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
  
    virtual task put(input T t);
      foreach (m_listeners[i]) begin
        m_listeners[i].put_pre(t);
      end
      m_target.put(t);
      foreach (m_listeners[i]) begin
        m_listeners[i].put_post(t);
      end
    endtask
  endclass

  class test_get_if_proxy #(type T = int) implements test_get_if #(T);
    test_get_if #(T) m_target;
    test_get_if_listener #(T) m_listeners [$];
  
    function new(input test_get_if #(T) target = null);
      m_target = target;
    endfunction
  
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
  
    virtual task get(output T t);
      foreach (m_listeners[i]) begin
        m_listeners[i].get_pre();
      end
      m_target.get(t);
      foreach (m_listeners[i]) begin
        m_listeners[i].get_post(t);
      end
    endtask
  endclass

endpackage : scenario_gen_pkg
