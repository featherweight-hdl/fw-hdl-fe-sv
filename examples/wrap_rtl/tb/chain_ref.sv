// The hand-wired reference for wrap_pkg::chain #(bit[7:0]): the same two
// modules connected by hand, with the ports the generated module has.
module chain_ref #(
    parameter int DEPTH = 4
) (
    input  logic       clk,
    input  logic       rst,
    input  logic       rst_n,
    input  logic       in_valid,
    output logic       in_ready,
    input  logic [7:0] in_data,
    output logic       out_valid,
    input  logic       out_ready,
    output logic [7:0] out_data
);
    logic       f_valid, f_ready;
    logic [7:0] f_data;

    fw_rv_fifo #(.WIDTH(8), .DEPTH(DEPTH)) fifo (
        .clk(clk), .rst(rst),
        .in_data(in_data), .in_vld(in_valid), .in_rdy(in_ready),
        .out_data(f_data), .out_vld(f_valid), .out_rdy(f_ready));

    axis_reg #(.DATA_W(8)) slice (
        .aclk(clk), .aresetn(rst_n),
        .s_axis_tdata(f_data), .s_axis_tvalid(f_valid), .s_axis_tready(f_ready),
        .s_axis_tlast(1'b1),
        .m_axis_tdata(out_data), .m_axis_tvalid(out_valid), .m_axis_tready(out_ready),
        .m_axis_tlast());
endmodule
