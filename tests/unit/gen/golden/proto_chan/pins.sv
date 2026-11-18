package scenario_gen_pkg;

  import fw_hdl_pkg::*;
  import scenario::*;

  class test_ch_pins;
  
    static function fw_pin_rule flat(input fw_extern_map m, input fw_object b, input string pre);
      return m.prefix(b, pre).rename("req.valid", "reqvalid").rename("req.ready", "reqready").rename("req.data", "reqdata").rename("rsp.valid", "rspvalid").rename("rsp.ready", "rspready").rename("rsp.data.code", "rspcode").rename("rsp.data.tag", "rsptag");
    endfunction
  endclass

endpackage : scenario_gen_pkg
