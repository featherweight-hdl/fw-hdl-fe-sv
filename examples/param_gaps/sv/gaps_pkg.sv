// Designs for parameterized emission beyond fifo_chain (core-layer1.md
// §6.6, M8(b)): value and struct (shape) class parameters, `if` on
// parameters, and port and bundle arrays in loops over parameters. Each
// must emit as ONE parameterized module (tests/e2e/test_param_gaps.py).
`include "fw_hdl_macros.svh"

package gaps_pkg;
    import fw_hdl_pkg::*;

    // A shape: the widths of a component or bundle, grouped in one
    // struct-valued class parameter (§3.9).
    typedef struct packed {
        int data_w;
        int depth;
    } shape_t;

    // ---------------------------------------------------------------- leaves

    // fw_rv_fifo with its width a value class parameter.
    class wfifo #(int W = 8) extends fw_component implements fw_extern_if;
        typedef struct { int depth; } params_t;
        static function params_t mk_params(int depth = 2);
            params_t p;
            p.depth = depth;
            return p;
        endfunction
        `fw_component_params
        `fw_extern_utils(wfifo #(W), "fw_rv_fifo")

        fw_input  #(bit)                        clk, rst;
        fw_mirror #(fw_rv_if #(bit [W-1:0]))    in;
        fw_bundle #(fw_rv_if #(bit [W-1:0]))    out;

        virtual function void build();
            clk = fw_input #(bit)::create("clk", this);
            rst = fw_input #(bit)::create("rst", this);
            in  = fw_mirror #(fw_rv_if #(bit [W-1:0]))::create("in",  this);
            out = fw_bundle #(fw_rv_if #(bit [W-1:0]))::create("out", this);
        endfunction

        virtual function void extern_map(fw_extern_map m);
            m.param("WIDTH", fw_value #(int)::of(W));
            m.param("DEPTH", fw_value #(int)::of(params.depth));
            void'(m.prefix(in,  "in_").rename("valid", "vld").rename("ready", "rdy"));
            void'(m.prefix(out, "out_").rename("valid", "vld").rename("ready", "rdy"));
        endfunction
    endclass

    // rv_wire: a pass-through, no clock.
    class wwire #(int W = 8) extends fw_component implements fw_extern_if;
        `fw_extern_utils(wwire #(W), "rv_wire")

        fw_mirror #(fw_rv_if #(bit [W-1:0]))    in;
        fw_bundle #(fw_rv_if #(bit [W-1:0]))    out;

        virtual function void build();
            in  = fw_mirror #(fw_rv_if #(bit [W-1:0]))::create("in",  this);
            out = fw_bundle #(fw_rv_if #(bit [W-1:0]))::create("out", this);
        endfunction

        virtual function void extern_map(fw_extern_map m);
            m.param("WIDTH", fw_value #(int)::of(W));
            void'(m.prefix(in,  "in_").rename("valid", "vld").rename("ready", "rdy"));
            void'(m.prefix(out, "out_").rename("valid", "vld").rename("ready", "rdy"));
        endfunction
    endclass

    // fw_valid_check on a link (no data pin).
    class vcheck #(int W = 8) extends fw_component implements fw_extern_if;
        `fw_extern_utils(vcheck #(W), "fw_valid_check")

        fw_input   #(bit)                       clk, rst;
        fw_monitor #(fw_rv_if #(bit [W-1:0]))   p;

        virtual function void build();
            clk = fw_input   #(bit)::create("clk", this);
            rst = fw_input   #(bit)::create("rst", this);
            p   = fw_monitor #(fw_rv_if #(bit [W-1:0]))::create("p", this);
        endfunction

        virtual function void extern_map(fw_extern_map m);
            void'(m.prefix(p, "").rename("valid", "vld").rename("ready", "rdy"));
            m.unused(p.data);
        endfunction
    endclass

    // A ready/valid bundle sized by a shape.
    virtual class sh_rv_if #(shape_t S = '{data_w: 8, depth: 2}) extends fw_bundle_base;
        fw_output #(bit)                  valid;
        fw_input  #(bit)                  ready;
        fw_output #(bit [S.data_w-1:0])   data;

        virtual function void build();
            valid = fw_output #(bit)::create("valid", this);
            ready = fw_input  #(bit)::create("ready", this);
            data  = fw_output #(bit [S.data_w-1:0])::create("data", this);
        endfunction
    endclass

    // fw_rv_fifo, sized by a shape, with shaped bundles.
    class sfifo #(shape_t S = '{data_w: 8, depth: 2}) extends fw_component
            implements fw_extern_if;
        `fw_extern_utils(sfifo #(S), "fw_rv_fifo")

        fw_input  #(bit)            clk, rst;
        fw_mirror #(sh_rv_if #(S))  in;
        fw_bundle #(sh_rv_if #(S))  out;

        virtual function void build();
            clk = fw_input #(bit)::create("clk", this);
            rst = fw_input #(bit)::create("rst", this);
            in  = fw_mirror #(sh_rv_if #(S))::create("in",  this);
            out = fw_bundle #(sh_rv_if #(S))::create("out", this);
        endfunction

        virtual function void extern_map(fw_extern_map m);
            m.param("WIDTH", fw_value #(int)::of(S.data_w));
            m.param("DEPTH", fw_value #(int)::of(S.depth));
            void'(m.prefix(in,  "in_").rename("valid", "vld").rename("ready", "rdy"));
            void'(m.prefix(out, "out_").rename("valid", "vld").rename("ready", "rdy"));
        endfunction
    endclass

    // -------------------------------------------- gap 1: value class parameter

    // Two FIFOs in series; the data width is a value class parameter, the
    // depths come from params.
    class wpipe #(int W = 8) extends fw_component;
        typedef struct { int depth; } params_t;
        static function params_t mk_params(int depth = 2);
            params_t p;
            p.depth = depth;
            return p;
        endfunction
        `fw_component_params
        `fw_component_utils(wpipe #(W))

        fw_input  #(bit)                        clk, rst;
        fw_mirror #(fw_rv_if #(bit [W-1:0]))    in;
        fw_bundle #(fw_rv_if #(bit [W-1:0]))    out;
        wfifo #(W)                              a, b;

        virtual function void build();
            clk = fw_input #(bit)::create("clk", this);
            rst = fw_input #(bit)::create("rst", this);
            in  = fw_mirror #(fw_rv_if #(bit [W-1:0]))::create("in",  this);
            out = fw_bundle #(fw_rv_if #(bit [W-1:0]))::create("out", this);
            a = wfifo #(W)::create("a", this, wfifo #(W)::mk_params(.depth(params.depth)));
            b = wfifo #(W)::create("b", this, wfifo #(W)::mk_params(.depth(params.depth + 1)));
        endfunction

        virtual function void connect();
            a.in.connect_up(in);
            a.out.connect(b.in);
            b.out.connect_up(out);
            connect_all('{ '{a.clk, clk}, '{a.rst, rst}, '{b.clk, clk}, '{b.rst, rst} });
        endfunction
    endclass

    // ------------------------------------- gap 1: struct (shape) class parameter

    // in (shaped bundle) -> u (sfifo #(S)) -> v (wfifo #(S.data_w)) -> out.
    class spipe #(shape_t S = '{data_w: 8, depth: 2}) extends fw_component;
        `fw_component_utils(spipe #(S))

        fw_input  #(bit)                            clk, rst;
        fw_mirror #(sh_rv_if #(S))                  in;
        fw_bundle #(fw_rv_if #(bit [S.data_w-1:0])) out;
        sfifo #(S)                                  u;
        wfifo #(S.data_w)                           v;

        virtual function void build();
            clk = fw_input #(bit)::create("clk", this);
            rst = fw_input #(bit)::create("rst", this);
            in  = fw_mirror #(sh_rv_if #(S))::create("in", this);
            out = fw_bundle #(fw_rv_if #(bit [S.data_w-1:0]))::create("out", this);
            u = sfifo #(S)::create("u", this);
            v = wfifo #(S.data_w)::create("v", this, wfifo #(S.data_w)::mk_params(.depth(S.depth)));
        endfunction

        virtual function void connect();
            u.in.connect_up(in);
            v.in.valid.connect(u.out.valid);
            u.out.ready.connect(v.in.ready);
            v.in.data.connect(u.out.data);
            v.out.connect_up(out);
            connect_all('{ '{u.clk, clk}, '{u.rst, rst}, '{v.clk, clk}, '{v.rst, rst} });
        endfunction
    endclass

    // ------------------------------------------------ gap 2: if on parameters

    // A FIFO or a wire, by use_fifo; a checker on the input link when check
    // is set. The conditions are written once in build() and again in
    // connect().
    class cond_pipe #(int W = 8) extends fw_component;
        typedef struct {
            bit use_fifo;
            int depth;
            bit check;
        } params_t;
        static function params_t mk_params(bit use_fifo = 1, int depth = 2, bit check = 1);
            params_t p;
            p.use_fifo = use_fifo; p.depth = depth; p.check = check;
            return p;
        endfunction
        `fw_component_params
        `fw_component_utils(cond_pipe #(W))

        fw_input  #(bit)                        clk, rst;
        fw_mirror #(fw_rv_if #(bit [W-1:0]))    in;
        fw_bundle #(fw_rv_if #(bit [W-1:0]))    out;
        wfifo  #(W)                             f;
        wwire  #(W)                             w;
        vcheck #(W)                             chk;

        virtual function void build();
            clk = fw_input #(bit)::create("clk", this);
            rst = fw_input #(bit)::create("rst", this);
            in  = fw_mirror #(fw_rv_if #(bit [W-1:0]))::create("in",  this);
            out = fw_bundle #(fw_rv_if #(bit [W-1:0]))::create("out", this);
            if (params.use_fifo)
                f = wfifo #(W)::create("f", this, wfifo #(W)::mk_params(.depth(params.depth)));
            else
                w = wwire #(W)::create("w", this);
            if (params.check)
                chk = vcheck #(W)::create("chk", this);
        endfunction

        virtual function void connect();
            if (params.use_fifo) begin
                f.in.connect_up(in);
                f.out.connect_up(out);
                connect_all('{ '{f.clk, clk}, '{f.rst, rst} });
            end else begin
                w.in.connect_up(in);
                w.out.connect_up(out);
            end
            if (params.check) begin
                chk.p.connect(in);
                connect_all('{ '{chk.clk, clk}, '{chk.rst, rst} });
            end
        endfunction
    endclass

    // A stage: ports and params only; realized by a FIFO or a wire. Its
    // ports' width is its value class parameter, which the realizations
    // pass through their extends clauses.
    virtual class stage #(int W = 8) extends fw_component;
        typedef struct { int depth; } params_t;
        static function params_t mk_params(int depth = 2);
            params_t p;
            p.depth = depth;
            return p;
        endfunction
        `fw_component_params

        fw_input  #(bit)                        clk, rst;
        fw_mirror #(fw_rv_if #(bit [W-1:0]))    in;
        fw_bundle #(fw_rv_if #(bit [W-1:0]))    out;

        virtual function void build();
            clk = fw_input #(bit)::create("clk", this);
            rst = fw_input #(bit)::create("rst", this);
            in  = fw_mirror #(fw_rv_if #(bit [W-1:0]))::create("in",  this);
            out = fw_bundle #(fw_rv_if #(bit [W-1:0]))::create("out", this);
        endfunction
    endclass

    class stage_fifo #(int W = 8) extends stage #(W) implements fw_extern_if;
        `fw_extern_utils(stage_fifo #(W), "fw_rv_fifo")

        virtual function void extern_map(fw_extern_map m);
            m.param("WIDTH", fw_value #(int)::of(W));
            m.param("DEPTH", fw_value #(int)::of(params.depth));
            void'(m.prefix(in,  "in_").rename("valid", "vld").rename("ready", "rdy"));
            void'(m.prefix(out, "out_").rename("valid", "vld").rename("ready", "rdy"));
        endfunction
    endclass

    class stage_wire #(int W = 8) extends stage #(W) implements fw_extern_if;
        `fw_extern_utils(stage_wire #(W), "rv_wire")

        virtual function void extern_map(fw_extern_map m);
            m.param("WIDTH", fw_value #(int)::of(W));
            void'(m.prefix(in,  "in_").rename("valid", "vld").rename("ready", "rdy"));
            void'(m.prefix(out, "out_").rename("valid", "vld").rename("ready", "rdy"));
            m.unused(clk);
            m.unused(rst);
        endfunction
    endclass

    // The same child, u, a FIFO or a wire by use_fifo; connect() connects u
    // whichever it is, after the branch.
    class pick_pipe #(int W = 8) extends fw_component;
        typedef struct {
            bit use_fifo;
            int depth;
        } params_t;
        static function params_t mk_params(bit use_fifo = 1, int depth = 2);
            params_t p;
            p.use_fifo = use_fifo; p.depth = depth;
            return p;
        endfunction
        `fw_component_params
        `fw_component_utils(pick_pipe #(W))

        fw_input  #(bit)                        clk, rst;
        fw_mirror #(fw_rv_if #(bit [W-1:0]))    in;
        fw_bundle #(fw_rv_if #(bit [W-1:0]))    out;
        stage #(W)                              u;

        virtual function void build();
            clk = fw_input #(bit)::create("clk", this);
            rst = fw_input #(bit)::create("rst", this);
            in  = fw_mirror #(fw_rv_if #(bit [W-1:0]))::create("in",  this);
            out = fw_bundle #(fw_rv_if #(bit [W-1:0]))::create("out", this);
            if (params.use_fifo)
                u = stage_fifo #(W)::create("u", this, stage #(W)::mk_params(.depth(params.depth)));
            else
                u = stage_wire #(W)::create("u", this);
        endfunction

        virtual function void connect();
            u.in.connect_up(in);
            u.out.connect_up(out);
            connect_all('{ '{u.clk, clk}, '{u.rst, rst} });
        endfunction
    endclass

    // ------------------------------- gap 3: port and bundle arrays in loops

    // n independent lanes: a FIFO per lane between the lane's input and
    // output bundles; fire[i] is lane i's output valid.
    class lanes #(int W = 8) extends fw_component;
        typedef struct {
            int n;
            int depth;
        } params_t;
        static function params_t mk_params(int n = 2, int depth = 2);
            params_t p;
            p.n = n; p.depth = depth;
            return p;
        endfunction
        `fw_component_params
        `fw_component_utils(lanes #(W))

        fw_input  #(bit)                        clk, rst;
        fw_mirror #(fw_rv_if #(bit [W-1:0]))    in[];
        fw_bundle #(fw_rv_if #(bit [W-1:0]))    out[];
        fw_output #(bit)                        fire[];
        wfifo #(W)                              u[];

        virtual function void build();
            clk = fw_input #(bit)::create("clk", this);
            rst = fw_input #(bit)::create("rst", this);
            in   = new[params.n];
            out  = new[params.n];
            fire = new[params.n];
            u    = new[params.n];
            foreach (u[i]) begin
                in[i]   = fw_mirror #(fw_rv_if #(bit [W-1:0]))::create($sformatf("in[%0d]", i), this);
                out[i]  = fw_bundle #(fw_rv_if #(bit [W-1:0]))::create($sformatf("out[%0d]", i), this);
                fire[i] = fw_output #(bit)::create($sformatf("fire[%0d]", i), this);
                u[i]    = wfifo #(W)::create($sformatf("u[%0d]", i), this,
                                             wfifo #(W)::mk_params(.depth(params.depth)));
            end
        endfunction

        virtual function void connect();
            foreach (u[i]) begin
                u[i].in.connect_up(in[i]);
                u[i].out.connect_up(out[i]);
                fire[i].connect(u[i].out.valid);
                connect_all('{ '{u[i].clk, clk}, '{u[i].rst, rst} });
            end
        endfunction
    endclass

    // lanes inside a wrapper: the wrapper's bundle arrays connect, per
    // index, to the child's.
    class lanes_top #(int W = 8) extends fw_component;
        typedef struct {
            int n;
        } params_t;
        static function params_t mk_params(int n = 2);
            params_t p;
            p.n = n;
            return p;
        endfunction
        `fw_component_params
        `fw_component_utils(lanes_top #(W))

        fw_input  #(bit)                        clk, rst;
        fw_mirror #(fw_rv_if #(bit [W-1:0]))    in[];
        fw_bundle #(fw_rv_if #(bit [W-1:0]))    out[];
        lanes #(W)                              x;

        virtual function void build();
            clk = fw_input #(bit)::create("clk", this);
            rst = fw_input #(bit)::create("rst", this);
            in  = new[params.n];
            out = new[params.n];
            for (int i = 0; i < params.n; i++) begin
                in[i]  = fw_mirror #(fw_rv_if #(bit [W-1:0]))::create($sformatf("in[%0d]", i), this);
                out[i] = fw_bundle #(fw_rv_if #(bit [W-1:0]))::create($sformatf("out[%0d]", i), this);
            end
            x = lanes #(W)::create("x", this, lanes #(W)::mk_params(.n(params.n), .depth(1)));
        endfunction

        virtual function void connect();
            for (int i = 0; i < params.n; i++) begin
                x.in[i].connect_up(in[i]);
                x.out[i].connect_up(out[i]);
            end
            connect_all('{ '{x.clk, clk}, '{x.rst, rst} });
        endfunction
    endclass

    // ------------------------------------------ outside the structural subset

    // A case on a parameter choosing the structure: not emitted as a
    // parameterized module (FW-I-EMIT-SPEC).
    class by_case #(int W = 8) extends fw_component;
        typedef struct { int mode; } params_t;
        static function params_t mk_params(int mode = 0);
            params_t p;
            p.mode = mode;
            return p;
        endfunction
        `fw_component_params
        `fw_component_utils(by_case #(W))

        fw_mirror #(fw_rv_if #(bit [W-1:0]))    in;
        fw_bundle #(fw_rv_if #(bit [W-1:0]))    out;
        wwire #(W)                              w;

        virtual function void build();
            in  = fw_mirror #(fw_rv_if #(bit [W-1:0]))::create("in",  this);
            out = fw_bundle #(fw_rv_if #(bit [W-1:0]))::create("out", this);
            case (params.mode)
                0: w = wwire #(W)::create("w", this);
                default: w = wwire #(W)::create("w2", this);
            endcase
        endfunction

        virtual function void connect();
            w.in.connect_up(in);
            w.out.connect_up(out);
        endfunction
    endclass

    // An extern with a parameter whose default is computed from another.
    class dfifo #(int W = 8, int D = W / 4) extends fw_component implements fw_extern_if;
        `fw_extern_utils(dfifo #(W, D), "fw_rv_fifo")

        fw_input  #(bit)                        clk, rst;
        fw_mirror #(fw_rv_if #(bit [W-1:0]))    in;
        fw_bundle #(fw_rv_if #(bit [W-1:0]))    out;

        virtual function void build();
            clk = fw_input #(bit)::create("clk", this);
            rst = fw_input #(bit)::create("rst", this);
            in  = fw_mirror #(fw_rv_if #(bit [W-1:0]))::create("in",  this);
            out = fw_bundle #(fw_rv_if #(bit [W-1:0]))::create("out", this);
        endfunction

        virtual function void extern_map(fw_extern_map m);
            m.param("WIDTH", fw_value #(int)::of(W));
            m.param("DEPTH", fw_value #(int)::of(D));
            void'(m.prefix(in,  "in_").rename("valid", "vld").rename("ready", "rdy"));
            void'(m.prefix(out, "out_").rename("valid", "vld").rename("ready", "rdy"));
        endfunction
    endclass

    // dfifo #(W): D left to its default W / 4, which depends on W: not
    // emitted as a parameterized module (FW-I-EMIT-SPEC).
    class dflt_dep #(int W = 8) extends fw_component;
        `fw_component_utils(dflt_dep #(W))

        fw_input  #(bit)                        clk, rst;
        fw_mirror #(fw_rv_if #(bit [W-1:0]))    in;
        fw_bundle #(fw_rv_if #(bit [W-1:0]))    out;
        dfifo #(W)                              u;

        virtual function void build();
            clk = fw_input #(bit)::create("clk", this);
            rst = fw_input #(bit)::create("rst", this);
            in  = fw_mirror #(fw_rv_if #(bit [W-1:0]))::create("in",  this);
            out = fw_bundle #(fw_rv_if #(bit [W-1:0]))::create("out", this);
            u = dfifo #(W)::create("u", this);
        endfunction

        virtual function void connect();
            u.in.connect_up(in);
            u.out.connect_up(out);
            connect_all('{ '{u.clk, clk}, '{u.rst, rst} });
        endfunction
    endclass

    // fw_rv_fifo whose params default (the depth) is computed from its
    // class parameter.
    class wdfifo #(int W = 8) extends fw_component implements fw_extern_if;
        typedef struct { int depth; } params_t;
        static function params_t mk_params(int depth = W / 2);
            params_t p;
            p.depth = depth;
            return p;
        endfunction
        `fw_component_params
        `fw_extern_utils(wdfifo #(W), "fw_rv_fifo")

        fw_input  #(bit)                        clk, rst;
        fw_mirror #(fw_rv_if #(bit [W-1:0]))    in;
        fw_bundle #(fw_rv_if #(bit [W-1:0]))    out;

        virtual function void build();
            clk = fw_input #(bit)::create("clk", this);
            rst = fw_input #(bit)::create("rst", this);
            in  = fw_mirror #(fw_rv_if #(bit [W-1:0]))::create("in",  this);
            out = fw_bundle #(fw_rv_if #(bit [W-1:0]))::create("out", this);
        endfunction

        virtual function void extern_map(fw_extern_map m);
            m.param("WIDTH", fw_value #(int)::of(W));
            m.param("DEPTH", fw_value #(int)::of(params.depth));
            void'(m.prefix(in,  "in_").rename("valid", "vld").rename("ready", "rdy"));
            void'(m.prefix(out, "out_").rename("valid", "vld").rename("ready", "rdy"));
        endfunction
    endclass

    // Two of them: a takes create()'s default params, b mk_params() named.
    // Their depth is W / 2 of our W, in both.
    class dpipe #(int W = 8) extends fw_component;
        `fw_component_utils(dpipe #(W))

        fw_input  #(bit)                        clk, rst;
        fw_mirror #(fw_rv_if #(bit [W-1:0]))    in;
        fw_bundle #(fw_rv_if #(bit [W-1:0]))    out;
        wdfifo #(W)                             a, b;

        virtual function void build();
            clk = fw_input #(bit)::create("clk", this);
            rst = fw_input #(bit)::create("rst", this);
            in  = fw_mirror #(fw_rv_if #(bit [W-1:0]))::create("in",  this);
            out = fw_bundle #(fw_rv_if #(bit [W-1:0]))::create("out", this);
            a = wdfifo #(W)::create("a", this);
            b = wdfifo #(W)::create("b", this, wdfifo #(W)::mk_params());
        endfunction

        virtual function void connect();
            a.in.connect_up(in);
            a.out.connect(b.in);
            b.out.connect_up(out);
            connect_all('{ '{a.clk, clk}, '{a.rst, rst}, '{b.clk, clk}, '{b.rst, rst} });
        endfunction
    endclass

    // A shape built from a parameter in the class argument: not traced, so
    // the module is emitted per specialization (FW-I-EMIT-SPEC).
    class lit_shape #(int W = 8) extends fw_component;
        `fw_component_utils(lit_shape #(W))

        fw_input  #(bit)                            clk, rst;
        sfifo #('{data_w: W, depth: 2})             u;

        virtual function void build();
            clk = fw_input #(bit)::create("clk", this);
            rst = fw_input #(bit)::create("rst", this);
            u = sfifo #('{data_w: W, depth: 2})::create("u", this);
        endfunction

        virtual function void connect();
            connect_all('{ '{u.clk, clk}, '{u.rst, rst} });
            u.in.valid.tie(0);
            u.in.data.tie(0);
            u.out.ready.tie(1);
        endfunction
    endclass
endpackage
