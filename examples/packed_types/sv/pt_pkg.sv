// Packed struct and enum port types (core-layer1.md §6.3, §6.5).
//
// pri_e and tag_t are fw-hdl's own: the emitted code declares them in a
// generated package (<top>_types_pkg). cmd_t and op_e are the RTL's
// (pt_rtl_pkg, which the extern RTL also compiles): the emitted code refers
// to them, so the net on pt_acc's cmd pin has the pin's own type. tag_t
// holds an op_e: the generated package refers to the RTL's enum too.
`include "fw_hdl_macros.svh"

package pt_pkg;
    import fw_hdl_pkg::*;
    import pt_rtl_pkg::*;

    typedef enum bit [1:0] {PRI_LO, PRI_MID, PRI_HI} pri_e;
    typedef struct packed {
        pri_e     pri;
        op_e      op;
        bit [3:0] id;
    } tag_t;

    // A pass-through, generic over its type: its module has `parameter type T`.
    class pass #(type T = bit [7:0]) extends fw_component;
        `fw_component_utils(pass #(T))
        fw_input  #(T) i;
        fw_output #(T) o;
        virtual function void build();
            i = fw_input  #(T)::create("i", this);
            o = fw_output #(T)::create("o", this);
        endfunction
        virtual function void connect();
            o.connect(i);
        endfunction
    endclass

    // pt_acc wrapped: W is a module parameter; the debug pin is left open.
    class acc #(int W = 8) extends fw_component implements fw_extern_if;
        `fw_extern_utils(acc #(W), "pt_acc")
        fw_input  #(bit)         clk, rst;
        fw_input  #(cmd_t)       cmd;
        fw_output #(bit [W-1:0]) sum;
        virtual function void build();
            clk = fw_input  #(bit)::create("clk", this);
            rst = fw_input  #(bit)::create("rst", this);
            cmd = fw_input  #(cmd_t)::create("cmd", this);
            sum = fw_output #(bit [W-1:0])::create("sum", this);
        endfunction
        virtual function void extern_map(fw_extern_map m);
            m.param("W", fw_value #(int)::of(W));
            m.pin("acc", sum);
            m.open("nops");
        endfunction
    endclass

    class pt_top #(int W = 8) extends fw_component;
        `fw_component_utils(pt_top #(W))
        fw_input  #(bit)         clk, rst;
        fw_input  #(cmd_t)       cmd;
        fw_output #(cmd_t)       cmd_out;
        fw_output #(bit [W-1:0]) sum;
        fw_input  #(tag_t)       tag_in;
        fw_output #(tag_t)       tag_out;
        fw_input  #(pri_e)       pri_in;
        fw_output #(pri_e)       pri_out;
        acc  #(W)                a;
        pass #(tag_t)            tp;
        pass #(pri_e)            pp;
        pass #(cmd_t)            cp;
        virtual function void build();
            clk     = fw_input  #(bit)::create("clk", this);
            rst     = fw_input  #(bit)::create("rst", this);
            cmd     = fw_input  #(cmd_t)::create("cmd", this);
            cmd_out = fw_output #(cmd_t)::create("cmd_out", this);
            sum     = fw_output #(bit [W-1:0])::create("sum", this);
            tag_in  = fw_input  #(tag_t)::create("tag_in", this);
            tag_out = fw_output #(tag_t)::create("tag_out", this);
            pri_in  = fw_input  #(pri_e)::create("pri_in", this);
            pri_out = fw_output #(pri_e)::create("pri_out", this);
            a  = acc  #(W)::create("a", this);
            tp = pass #(tag_t)::create("tp", this);
            pp = pass #(pri_e)::create("pp", this);
            cp = pass #(cmd_t)::create("cp", this);
        endfunction
        virtual function void connect();
            a.clk.connect(clk);
            a.rst.connect(rst);
            a.cmd.connect(cmd);
            sum.connect(a.sum);
            cp.i.connect(cmd);
            cmd_out.connect(cp.o);
            tp.i.connect(tag_in);
            tag_out.connect(tp.o);
            pp.i.connect(pri_in);
            pri_out.connect(pp.o);
        endfunction
    endclass
endpackage
