// n lanes: generated lanes (or lanes_top, with +define+DUT_LANES_TOP)
// beside lanes_ref (gaps_ref.sv), every lane driven and checked on its own.
module lanes_tb #(
    parameter int W = 8,
    parameter int N = 2,
    parameter int D = 2
);
`ifdef DUT_LANES_TOP
    localparam int RD = 1;          // lanes_top makes its lanes with depth 1
`else
    localparam int RD = D;
`endif
    logic         clk = 0, rst = 1;
    logic         in_valid [N], out_ready [N];
    logic [W-1:0] in_data [N];
    logic         g_in_ready [N], g_out_valid [N], r_in_ready [N], r_out_valid [N];
    logic [W-1:0] g_out_data [N], r_out_data [N];
    logic         g_fire [N], r_fire [N];
    int           errors [N], recv [N];

    always #5 clk = ~clk;

`ifdef DUT_LANES_TOP
    lanes_top #(.W(W), .n(N)) dut (
        .clk(clk), .rst(rst),
        .in_valid(in_valid), .in_ready(g_in_ready), .in_data(in_data),
        .out_valid(g_out_valid), .out_ready(out_ready), .out_data(g_out_data));
    for (genvar i = 0; i < N; i++) begin : g_fire_of
        assign g_fire[i] = g_out_valid[i];
    end
`else
    lanes #(.W(W), .n(N), .depth(D)) dut (
        .clk(clk), .rst(rst),
        .in_valid(in_valid), .in_ready(g_in_ready), .in_data(in_data),
        .out_valid(g_out_valid), .out_ready(out_ready), .out_data(g_out_data),
        .fire(g_fire));
`endif

    lanes_ref #(.W(W), .N(N), .D(RD)) ref_ (
        .clk(clk), .rst(rst),
        .in_valid(in_valid), .in_ready(r_in_ready), .in_data(in_data),
        .out_valid(r_out_valid), .out_ready(out_ready), .out_data(r_out_data),
        .fire(r_fire));

    for (genvar i = 0; i < N; i++) begin : g_lane
        rv_lane_check #(.W(W), .SEED(i + 1)) chk (
            .clk(clk), .rst(rst),
            .in_valid(in_valid[i]), .in_data(in_data[i]), .out_ready(out_ready[i]),
            .g_in_ready(g_in_ready[i]), .r_in_ready(r_in_ready[i]),
            .g_out_valid(g_out_valid[i]), .r_out_valid(r_out_valid[i]),
            .g_out_data(g_out_data[i]), .r_out_data(r_out_data[i]),
            .errors(errors[i]), .recv(recv[i]));
    end

    int fire_errors = 0;
    always @(posedge clk)
        for (int i = 0; i < N; i++)
            if (!rst && g_fire[i] !== r_fire[i]) fire_errors <= fire_errors + 1;

    initial begin
        int errs, least;
        repeat (3) @(posedge clk);
        rst <= 0;
        repeat (2000) @(posedge clk);
        errs = fire_errors;
        least = recv[0];
        for (int i = 0; i < N; i++) begin
            errs += errors[i];
            if (recv[i] < least) least = recv[i];
        end
        if (errs == 0 && least > 100)
            $display("PASS: W=%0d N=%0d D=%0d: every lane matches the reference", W, N, RD);
        else
            $display("FAIL: %0d errors, at least %0d received per lane", errs, least);
        $finish;
    end
endmodule
