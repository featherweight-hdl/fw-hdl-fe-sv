// Elaborates the scenario in rt-sv, prints the protocol queries (Q lines),
// then calls the target through std's generated proxy with an order
// listener and a txn recorder (L: call order, T: recorded transactions).
module tb;
    import fw_hdl_pkg::*;
    import fw_hdl_std_pkg::*;
    import fw_hdl_std_pkg_gen_pkg::*;
    import scenario::*;

    class order extends fw_put_if_listener_base #(byte);
        sink k;
        function new(sink k);
            this.k = k;
        endfunction
        virtual function void put_pre(input byte t);
            k.log.push_back($sformatf("pre %0d", t));
        endfunction
        virtual function void put_post(input byte t);
            k.log.push_back($sformatf("post %0d", t));
        endfunction
    endclass

    class recorder implements fw_put_if_txn_sink #(byte);
        fw_put_if_txn #(byte) q [$];
        virtual function void put(input fw_put_if_txn #(byte) t);
            q.push_back(t);
        endfunction
    endclass

    initial begin
        top t;
        int n;
        fw_protocol_type ps, pt;
        fw_api_type req;
        fw_put_if_proxy #(byte) px;
        order ord;
        recorder rec;
        fw_put_if_txn_listener #(byte) tl;

        t = top::create("top", null);
        n = __fw_rt::elaborate(t);
        $display("Q ndiag %0d", __fw_rt::diags.size());
        ps = fw_protocol::of(t.a.b);
        pt = fw_protocol::of(t.k.m);
        $display("Q same %0d", ps == pt);
        $display("Q family %s", ps.family().name());
        $display("Q params %s", ps.params());
        req = ps.api(FW_PROTO_REQ);
        $display("Q req %s", req.family());
        $display("Q rsp %0d", ps.api(FW_PROTO_RSP) == null);
        $display("Q api_protocols %0d", req.protocols().size());

        ord = new(t.k);
        rec = new;
        tl = new(rec);
        px = new(t.k);
        px.add_listener(ord);
        px.add_listener(tl);
        px.put(8'd3);
        px.put(8'd5);
        foreach (t.k.log[i]) $display("L %s", t.k.log[i]);
        foreach (rec.q[i]) $display("T %s", rec.q[i].sprint());
        $finish;
    end
endmodule
