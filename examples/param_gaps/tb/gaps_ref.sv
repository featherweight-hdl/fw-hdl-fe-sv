// Hand-wired references for the designs of gaps_pkg (sv/gaps_pkg.sv),
// written from the classes, not from the generated modules. The testbenches
// run each generated module beside its reference on the same stimulus.

// wpipe #(W) with depth D: two FIFOs in series, depths D and D+1.
module wpipe_ref #(
    parameter int W = 8,
    parameter int D = 2
) (
    input  logic         clk, rst,
    input  logic         in_valid,
    output logic         in_ready,
    input  logic [W-1:0] in_data,
    output logic         out_valid,
    input  logic         out_ready,
    output logic [W-1:0] out_data
);
    logic         mid_vld, mid_rdy;
    logic [W-1:0] mid_data;
    fw_rv_fifo #(.WIDTH(W), .DEPTH(D)) a (
        .clk(clk), .rst(rst),
        .in_data(in_data), .in_vld(in_valid), .in_rdy(in_ready),
        .out_data(mid_data), .out_vld(mid_vld), .out_rdy(mid_rdy));
    fw_rv_fifo #(.WIDTH(W), .DEPTH(D + 1)) b (
        .clk(clk), .rst(rst),
        .in_data(mid_data), .in_vld(mid_vld), .in_rdy(mid_rdy),
        .out_data(out_data), .out_vld(out_valid), .out_rdy(out_ready));
endmodule

// spipe #(S): two FIFOs of width S.data_w and depth S.depth in series.
module spipe_ref #(
    parameter int W = 8,
    parameter int D = 2
) (
    input  logic         clk, rst,
    input  logic         in_valid,
    output logic         in_ready,
    input  logic [W-1:0] in_data,
    output logic         out_valid,
    input  logic         out_ready,
    output logic [W-1:0] out_data
);
    logic         mid_vld, mid_rdy;
    logic [W-1:0] mid_data;
    fw_rv_fifo #(.WIDTH(W), .DEPTH(D)) u (
        .clk(clk), .rst(rst),
        .in_data(in_data), .in_vld(in_valid), .in_rdy(in_ready),
        .out_data(mid_data), .out_vld(mid_vld), .out_rdy(mid_rdy));
    fw_rv_fifo #(.WIDTH(W), .DEPTH(D)) v (
        .clk(clk), .rst(rst),
        .in_data(mid_data), .in_vld(mid_vld), .in_rdy(mid_rdy),
        .out_data(out_data), .out_vld(out_valid), .out_rdy(out_ready));
endmodule

// cond_pipe #(W): a FIFO of depth D, or a wire; a checker on the input link.
module cond_pipe_ref #(
    parameter int W        = 8,
    parameter bit USE_FIFO = 1,
    parameter int D        = 2,
    parameter bit CHECK    = 1
) (
    input  logic         clk, rst,
    input  logic         in_valid,
    output logic         in_ready,
    input  logic [W-1:0] in_data,
    output logic         out_valid,
    input  logic         out_ready,
    output logic [W-1:0] out_data
);
    if (USE_FIFO) begin : g_fifo
        fw_rv_fifo #(.WIDTH(W), .DEPTH(D)) f (
            .clk(clk), .rst(rst),
            .in_data(in_data), .in_vld(in_valid), .in_rdy(in_ready),
            .out_data(out_data), .out_vld(out_valid), .out_rdy(out_ready));
    end else begin : g_wire
        assign out_data  = in_data;
        assign out_valid = in_valid;
        assign in_ready  = out_ready;
    end
    if (CHECK) begin : g_check
        fw_valid_check chk (.clk(clk), .rst(rst), .vld(in_valid), .rdy(in_ready));
    end
endmodule

// lanes #(W) with n lanes of depth D: a FIFO per lane; fire = out_valid.
module lanes_ref #(
    parameter int W = 8,
    parameter int N = 2,
    parameter int D = 2
) (
    input  logic         clk, rst,
    input  logic         in_valid  [N],
    output logic         in_ready  [N],
    input  logic [W-1:0] in_data   [N],
    output logic         out_valid [N],
    input  logic         out_ready [N],
    output logic [W-1:0] out_data  [N],
    output logic         fire      [N]
);
    for (genvar i = 0; i < N; i++) begin : g_lane
        fw_rv_fifo #(.WIDTH(W), .DEPTH(D)) u (
            .clk(clk), .rst(rst),
            .in_data(in_data[i]), .in_vld(in_valid[i]), .in_rdy(in_ready[i]),
            .out_data(out_data[i]), .out_vld(out_valid[i]), .out_rdy(out_ready[i]));
        assign fire[i] = out_valid[i];
    end
endmodule

// dpipe #(W): two FIFOs in series, each of depth W / 2 (the params default
// of the FIFO class, computed from its class parameter).
module dpipe_ref #(
    parameter int W = 8
) (
    input  logic         clk, rst,
    input  logic         in_valid,
    output logic         in_ready,
    input  logic [W-1:0] in_data,
    output logic         out_valid,
    input  logic         out_ready,
    output logic [W-1:0] out_data
);
    logic         mid_vld, mid_rdy;
    logic [W-1:0] mid_data;
    fw_rv_fifo #(.WIDTH(W), .DEPTH(W / 2)) a (
        .clk(clk), .rst(rst),
        .in_data(in_data), .in_vld(in_valid), .in_rdy(in_ready),
        .out_data(mid_data), .out_vld(mid_vld), .out_rdy(mid_rdy));
    fw_rv_fifo #(.WIDTH(W), .DEPTH(W / 2)) b (
        .clk(clk), .rst(rst),
        .in_data(mid_data), .in_vld(mid_vld), .in_rdy(mid_rdy),
        .out_data(out_data), .out_vld(out_valid), .out_rdy(out_ready));
endmodule
