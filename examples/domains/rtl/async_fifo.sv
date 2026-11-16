// Existing RTL that the example wraps: a dual-clock ready/valid FIFO of
// 2**AW entries. The write and read pointers cross between the clocks in
// Gray code, through two flops each. Both resets are active low and
// asynchronous.
module async_fifo #(
    parameter int WIDTH = 8,
    parameter int AW    = 2
) (
    input  logic             wr_clk,
    input  logic             wr_rst_n,
    input  logic             in_valid,
    output logic             in_ready,
    input  logic [WIDTH-1:0] in_data,
    input  logic             rd_clk,
    input  logic             rd_rst_n,
    output logic             out_valid,
    input  logic             out_ready,
    output logic [WIDTH-1:0] out_data
);
    localparam int DEPTH = 1 << AW;
    localparam logic [AW:0] ONE = 1;

    logic [WIDTH-1:0] mem [DEPTH];
    logic [AW:0] wbin, wgray, rbin, rgray;
    logic [AW:0] rgray_w1, rgray_w2, wgray_r1, wgray_r2;
    logic [AW:0] wbin_n, rbin_n;
    logic        full, empty;

    assign wbin_n = wbin + ONE;
    assign rbin_n = rbin + ONE;

    // Write side: full when the write pointer is one lap ahead of the
    // (synchronized) read pointer.
    assign full     = wgray == {~rgray_w2[AW:AW-1], rgray_w2[AW-2:0]};
    assign in_ready = !full;

    always_ff @(posedge wr_clk or negedge wr_rst_n)
        if (!wr_rst_n) begin
            wbin  <= '0;
            wgray <= '0;
        end else if (in_valid && !full) begin
            wbin  <= wbin_n;
            wgray <= (wbin_n >> 1) ^ wbin_n;
        end

    always_ff @(posedge wr_clk)
        if (in_valid && !full) mem[wbin[AW-1:0]] <= in_data;

    always_ff @(posedge wr_clk or negedge wr_rst_n)
        if (!wr_rst_n) begin
            rgray_w1 <= '0;
            rgray_w2 <= '0;
        end else begin
            rgray_w1 <= rgray;
            rgray_w2 <= rgray_w1;
        end

    // Read side: empty when the read pointer meets the (synchronized)
    // write pointer.
    assign empty     = rgray == wgray_r2;
    assign out_valid = !empty;
    assign out_data  = mem[rbin[AW-1:0]];

    always_ff @(posedge rd_clk or negedge rd_rst_n)
        if (!rd_rst_n) begin
            rbin  <= '0;
            rgray <= '0;
        end else if (out_ready && !empty) begin
            rbin  <= rbin_n;
            rgray <= (rbin_n >> 1) ^ rbin_n;
        end

    always_ff @(posedge rd_clk or negedge rd_rst_n)
        if (!rd_rst_n) begin
            wgray_r1 <= '0;
            wgray_r2 <= '0;
        end else begin
            wgray_r1 <= wgray;
            wgray_r2 <= wgray_r1;
        end
endmodule
