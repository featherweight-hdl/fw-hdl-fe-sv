// One stream: a generated module beside its reference (gaps_ref.sv). The
// design is chosen with +define+: DUT_WPIPE, DUT_SPIPE, DUT_DPIPE,
// DUT_COND_PIPE or DUT_PICK_PIPE (checked against cond_pipe_ref: the same structure, no
// checker); its parameters with -G.
module pipe_tb #(
    parameter int W        = 8,
    parameter int D        = 2,
    parameter bit USE_FIFO = 1,
    parameter bit CHECK    = 1
);
    logic         clk = 0, rst = 1;
    logic         in_valid, out_ready;
    logic [W-1:0] in_data;
    logic         g_in_ready, g_out_valid, r_in_ready, r_out_valid;
    logic [W-1:0] g_out_data, r_out_data;
    int           errors, recv;

    always #5 clk = ~clk;

`ifdef DUT_WPIPE
    wpipe #(.W(W), .depth(D)) dut (
`elsif DUT_SPIPE
    spipe #(.S_data_w(W), .S_depth(D)) dut (
`elsif DUT_DPIPE
    dpipe #(.W(W)) dut (
`elsif DUT_PICK_PIPE
    pick_pipe #(.W(W), .use_fifo(USE_FIFO), .depth(D)) dut (
`else
    cond_pipe #(.W(W), .use_fifo(USE_FIFO), .depth(D), .check(CHECK)) dut (
`endif
        .clk(clk), .rst(rst),
        .in_valid(in_valid), .in_ready(g_in_ready), .in_data(in_data),
        .out_valid(g_out_valid), .out_ready(out_ready), .out_data(g_out_data));

`ifdef DUT_WPIPE
    wpipe_ref #(.W(W), .D(D)) ref_ (
`elsif DUT_SPIPE
    spipe_ref #(.W(W), .D(D)) ref_ (
`elsif DUT_DPIPE
    dpipe_ref #(.W(W)) ref_ (
`elsif DUT_PICK_PIPE
    cond_pipe_ref #(.W(W), .USE_FIFO(USE_FIFO), .D(D), .CHECK(0)) ref_ (
`else
    cond_pipe_ref #(.W(W), .USE_FIFO(USE_FIFO), .D(D), .CHECK(CHECK)) ref_ (
`endif
        .clk(clk), .rst(rst),
        .in_valid(in_valid), .in_ready(r_in_ready), .in_data(in_data),
        .out_valid(r_out_valid), .out_ready(out_ready), .out_data(r_out_data));

    rv_lane_check #(.W(W)) chk (
        .clk(clk), .rst(rst),
        .in_valid(in_valid), .in_data(in_data), .out_ready(out_ready),
        .g_in_ready(g_in_ready), .r_in_ready(r_in_ready),
        .g_out_valid(g_out_valid), .r_out_valid(r_out_valid),
        .g_out_data(g_out_data), .r_out_data(r_out_data),
        .errors(errors), .recv(recv));

    initial begin
        repeat (3) @(posedge clk);
        rst <= 0;
        repeat (2000) @(posedge clk);
        if (errors == 0 && recv > 100)
            $display("PASS: W=%0d D=%0d USE_FIFO=%0d CHECK=%0d: %0d received, outputs match the reference",
                     W, D, USE_FIFO, CHECK, recv);
        else
            $display("FAIL: %0d errors, %0d received", errors, recv);
        $finish;
    end
endmodule
