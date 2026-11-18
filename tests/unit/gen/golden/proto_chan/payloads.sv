package scenario_gen_pkg;

  import fw_hdl_pkg::*;
  import scenario::*;

  class test_ch_payloads;
  
    static function string test_ch_rsp_sprint(input scenario::test_ch_rsp_t v);
      return $sformatf("code=0x%0h tag=0x%0h", v.code, v.tag);
    endfunction
  
    static function bit test_ch_rsp_compare(input scenario::test_ch_rsp_t a, input scenario::test_ch_rsp_t b, output string diff);
      diff = "";
      if ((a.code !== b.code)) begin
        diff = {diff, $sformatf(" code: 0x%0h != 0x%0h", a.code, b.code)};
      end
      if ((a.tag !== b.tag)) begin
        diff = {diff, $sformatf(" tag: 0x%0h != 0x%0h", a.tag, b.tag)};
      end
      return (diff == "");
    endfunction
  
    static function scenario::test_ch_rsp_t test_ch_rsp_mask(input scenario::test_ch_rsp_t v);
      return v;
    endfunction
  endclass

endpackage : scenario_gen_pkg
