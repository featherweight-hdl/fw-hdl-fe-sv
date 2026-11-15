// The hand-wired reference for smoke_pkg::fifo_chain (fw-hdl-spec
// tests/sv/smoke/smoke_pkg.sv, core-layer1.md §4.8): N fw_rv_fifo in a row,
// the last one twice as deep when DEEP_TAIL, and fw_valid_check on the
// first link. Same ports as the generated module.
module fifo_chain_ref #(
    parameter int W         = 8,
    parameter int N         = 2,
    parameter int DEPTH     = 2,
    parameter bit DEEP_TAIL = 0
) (
    input  logic         clk,
    input  logic         rst,
    input  logic         in_valid,
    output logic         in_ready,
    input  logic [W-1:0] in_data,
    output logic         out_valid,
    input  logic         out_ready,
    output logic [W-1:0] out_data
);
    logic [W-1:0] d [N+1];
    logic         v [N+1];
    logic         r [N+1];

    assign d[0] = in_data;
    assign v[0] = in_valid;
    assign in_ready = r[0];
    assign out_data = d[N];
    assign out_valid = v[N];
    assign r[N] = out_ready;

    for (genvar i = 0; i < N; i++) begin : f
        fw_rv_fifo #(.WIDTH(W), .DEPTH((DEEP_TAIL && i == N - 1) ? 2 * DEPTH : DEPTH)) q (
            .clk(clk), .rst(rst),
            .in_data(d[i]),   .in_vld(v[i]),   .in_rdy(r[i]),
            .out_data(d[i+1]), .out_vld(v[i+1]), .out_rdy(r[i+1]));
    end

    fw_valid_check mon (.clk(clk), .rst(rst), .vld(v[1]), .rdy(r[1]));
endmodule
