"""Parameterized IR mapping (core-layer1.md §6.6): what is written over
parameters, and what falls back to a specialization (the specialization
rule), on small designs."""
import zuspec.ir.core as ir

from fw.hdl.fe.sv import elaborate

PKG = """
`include "fw_hdl_macros.svh"
package p;
    import fw_hdl_pkg::*;
    class leaf extends fw_component;
        typedef struct { int w; } params_t;
        static function params_t mk_params(int w = 1);
            params_t p; p.w = w; return p;
        endfunction
        `fw_component_params
        `fw_component_utils(leaf)
        fw_input #(bit) i;
        fw_output #(bit) o;
        virtual function void build();
            i = fw_input #(bit)::create("i", this);
            o = fw_output #(bit)::create("o", this);
        endfunction
    endclass

    // The children's number and parameters follow n: an instance array.
    class arr extends fw_component;
        typedef struct { int n; int k; } params_t;
        static function params_t mk_params(int n = 3, int k = 5);
            params_t p; p.n = n; p.k = k; return p;
        endfunction
        `fw_component_params
        `fw_component_utils(arr)
        fw_input #(bit) i;
        fw_output #(bit) o;
        leaf u[];
        virtual function void build();
            i = fw_input #(bit)::create("i", this);
            o = fw_output #(bit)::create("o", this);
            u = new[params.n];
            foreach (u[j]) u[j] = leaf::create($sformatf("u[%0d]", j), this,
                                               leaf::mk_params(.w(params.k + j)));
        endfunction
        virtual function void connect();
            i.connect(u[0].i);
            for (int j = 1; j < params.n; j++) u[j - 1].o.connect(u[j].i);
            u[params.n - 1].o.connect(o);
        endfunction
    endclass

    // A while loop over a parameter: not in the structural subset.
    class wl extends fw_component;
        typedef struct { int n; } params_t;
        static function params_t mk_params(int n = 2);
            params_t p; p.n = n; return p;
        endfunction
        `fw_component_params
        `fw_component_utils(wl)
        leaf u[$];
        virtual function void build();
            int j = 0;
            while (j < params.n) begin
                u.push_back(leaf::create($sformatf("u[%0d]", j), this));
                j++;
            end
        endfunction
        virtual function void connect();
            foreach (u[j]) u[j].i.tie(0);
        endfunction
    endclass

    // A child that exists only for some parameter values.
    class opt extends fw_component;
        typedef struct { bit has; } params_t;
        static function params_t mk_params(bit has = 0);
            params_t p; p.has = has; return p;
        endfunction
        `fw_component_params
        `fw_component_utils(opt)
        leaf x;
        virtual function void build();
            if (params.has) x = leaf::create("x", this);
        endfunction
    endclass

    // A value computed under a loop over a parameter is tainted.
    class taint extends fw_component;
        typedef struct { int n; } params_t;
        static function params_t mk_params(int n = 2);
            params_t p; p.n = n; return p;
        endfunction
        `fw_component_params
        `fw_component_utils(taint)
        leaf x;
        virtual function void build();
            int s = 0;
            for (int j = 0; j < params.n; j++) s += j;
            x = leaf::create("x", this, leaf::mk_params(.w(s)));
        endfunction
        virtual function void connect();
            x.i.tie(0);
        endfunction
    endclass

    class top extends fw_component;
        `fw_component_utils(top)
        arr a1, a2;
        wl w;
        opt o;
        taint t;
        virtual function void build();
            a1 = arr::create("a1", this);
            a2 = arr::create("a2", this, arr::mk_params(.n(2), .k(1)));
            w = wl::create("w", this);
            o = opt::create("o", this);
            t = taint::create("t", this);
        endfunction
        virtual function void connect();
            a1.i.tie(0);
            a2.i.tie(1);
        endfunction
    endclass
endpackage
"""


def _ir(tmp_path, **kw):
    f = tmp_path / "p.sv"
    f.write_text(PKG)
    r = elaborate([str(f)], top="p::top")
    assert r.ok, "\n".join(map(str, r.diags))
    ctxt, top = r.to_ir(**kw)
    return r, ctxt


def test_instance_array_shared_by_both_instances(tmp_path):
    r, ctxt = _ir(tmp_path)
    arr = ctxt.type_m["arr"]
    assert "arr__1" not in ctxt.type_m                    # n=3 and n=2: one IR type
    assert [tp.name for tp in arr.template_params] == ["n", "k"]
    (u,) = [f for f in arr.fields if f.name == "u"]
    assert isinstance(u.datatype, ir.DataTypeArray) and u.datatype.size_expr.name == "n"
    assert u.index_var == "j"
    w = u.inst_params["w"]
    assert isinstance(w, ir.ExprBin) and w.lhs.name == "k" and w.rhs.name == "j"
    (g,) = arr.generates
    assert isinstance(g, ir.GenerateFor) and g.var == "j" and g.start.value == 1
    top = ctxt.type_m["top"]
    ip = {f.name: f.inst_params for f in top.fields if f.name in ("a1", "a2")}
    assert ip["a1"]["n"].value == 3 and ip["a2"]["n"].value == 2 and ip["a2"]["k"].value == 1


def test_specialization_rule(tmp_path):
    r, ctxt = _ir(tmp_path)
    why = {d.path: d.msg for d in r.ir_diags}
    assert all(d.id == "FW-I-EMIT-SPEC" and d.severity == "info" for d in r.ir_diags)
    assert set(why) == {"top.w", "top.o", "top.t"}
    assert "while loop on a parameter" in why["top.w"]
    assert "branch on parameters" in why["top.o"]
    assert "top.t.x's parameter w" in why["top.t"]
    assert not ctxt.type_m["wl"].template_params        # baked in, unrolled
    assert [f.name for f in ctxt.type_m["wl"].fields] == ["u[0]", "u[1]"]


def test_require_param(tmp_path):
    import pytest
    from fw.hdl.fe.sv.elab import ElabError
    with pytest.raises(ElabError, match="FW-E-EMIT-SPEC"):
        _ir(tmp_path, require_param=True)


def test_per_specialization(tmp_path):
    r, ctxt = _ir(tmp_path, parameterize=False)
    assert r.ir_diags == []
    assert "arr__1" in ctxt.type_m and not ctxt.type_m["arr"].template_params
