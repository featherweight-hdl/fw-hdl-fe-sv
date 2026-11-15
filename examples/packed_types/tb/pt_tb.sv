// Drives the emitted pt_top with the generated package's types and the
// RTL's, and checks every output against a model.
module pt_tb #(parameter int W = 8);
    import pt_rtl_pkg::*;
    import pt_top_types_pkg::*;

    logic         clk = 0, rst = 1;
    cmd_t         cmd, cmd_out;
    logic [W-1:0] sum, model;
    tag_t         tag_in, tag_out;
    pri_e         pri_in, pri_out;
    int           errors = 0;

    pt_top #(.W(W)) dut (.clk(clk), .rst(rst), .cmd(cmd), .cmd_out(cmd_out), .sum(sum),
                         .tag_in(tag_in), .tag_out(tag_out), .pri_in(pri_in),
                         .pri_out(pri_out));

    always #5 clk = ~clk;

    initial begin
        cmd = '{op: OP_NOP, arg: 8'h0};
        tag_in = '0;
        pri_in = PRI_LO;
        model = '0;
        repeat (2) @(posedge clk);
        rst <= 0;
        for (int i = 0; i < 500; i++) begin
            @(negedge clk);
            if (sum !== model) begin
                errors++;
                $display("ERROR: cycle %0d: sum %h, expected %h", i, sum, model);
            end
            cmd.op  = op_e'($urandom_range(0, 3));
            cmd.arg = 8'($urandom);
            tag_in  = '{pri: pri_e'($urandom_range(0, 2)), op: op_e'($urandom_range(0, 3)),
                        id: 4'($urandom)};
            pri_in  = pri_e'($urandom_range(0, 2));
            #1;
            if (cmd_out !== cmd || tag_out !== tag_in || pri_out !== pri_in) begin
                errors++;
                $display("ERROR: cycle %0d: a pass-through differs", i);
            end
            case (cmd.op)
                OP_ADD: model = model + W'(cmd.arg);
                OP_SUB: model = model - W'(cmd.arg);
                OP_CLR: model = '0;
                default: ;
            endcase
        end
        if (errors == 0) $display("PASS: W=%0d", W);
        else $display("FAIL: W=%0d, %0d errors", W, errors);
        $finish;
    end
endmodule
