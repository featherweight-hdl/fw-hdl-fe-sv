// The hand-wired reference for domains_pkg::soc #(bit[7:0]): the same
// modules, with every clock and reset connected by hand, and the ports the
// generated module has. The reset comes in active low; the single-clock
// FIFO's active-high rst is its inverse.
module soc_ref (
    input  logic       clk_a_i,
    input  logic       clk_b_i,
    input  logic       rst_ni,
    input  logic       in_valid,
    output logic       in_ready,
    input  logic [7:0] in_data,
    output logic       out_valid,
    input  logic       out_ready,
    output logic [7:0] out_data
);
    logic       slow, rst;
    logic       a_valid, a_ready, b_valid, b_ready;
    logic [7:0] a_data, b_data;

    assign rst = ~rst_ni;

    clk_div2 div (.clk_i(clk_a_i), .rst_ni(rst_ni), .clk_o(slow));

    // x1: clk_a to clk_a, the same clock: a single-clock FIFO.
    fw_rv_fifo #(.WIDTH(8), .DEPTH(2)) x1 (
        .clk(clk_a_i), .rst(rst),
        .in_data(in_data), .in_vld(in_valid), .in_rdy(in_ready),
        .out_data(a_data), .out_vld(a_valid), .out_rdy(a_ready));

    // x2: clk_a to slow; x3: slow to clk_b. Dual-clock FIFOs.
    async_fifo #(.WIDTH(8)) x2 (
        .wr_clk(clk_a_i), .wr_rst_n(rst_ni),
        .in_valid(a_valid), .in_ready(a_ready), .in_data(a_data),
        .rd_clk(slow), .rd_rst_n(rst_ni),
        .out_valid(b_valid), .out_ready(b_ready), .out_data(b_data));

    async_fifo #(.WIDTH(8)) x3 (
        .wr_clk(slow), .wr_rst_n(rst_ni),
        .in_valid(b_valid), .in_ready(b_ready), .in_data(b_data),
        .rd_clk(clk_b_i), .rd_rst_n(rst_ni),
        .out_valid(out_valid), .out_ready(out_ready), .out_data(out_data));
endmodule
