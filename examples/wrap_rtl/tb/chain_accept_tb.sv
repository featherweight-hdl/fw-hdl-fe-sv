// The all-RTL reference for the wrap_rtl acceptance test (fe-sv
// tests/e2e/test_wrap_rtl_acceptance.py): drives chain_ref by hand with the
// stimulus of package chain_seq_pkg and prints the clock edge of each
// handshake, as the class testbench's monitors do:
//
//   in:  before item n, in_valid stays low for in_gap[n] cycles after the
//        last accept (0: back to back); then data[n] is held until taken.
//   out: out_ready is high, except for out_hold[n] cycles after the n-th
//        transfer out.
//
// chain_seq_pkg (written by the test) gives N, in_gap, out_hold and data.
// Clock and reset are the generated chain_tb's: 10 ns period, first edge
// at 5 ns, reset released at edge 3, stimulus from edge 4. Edge n (at
// 10n-5 ns) prints as $time / 10ns = n.
module chain_accept_tb;
    import chain_seq_pkg::*;

    bit         clk;
    logic       rst = 1;
    logic       in_valid = 0, out_ready = 1;
    logic [7:0] in_data = 0;
    logic       in_ready, out_valid;
    logic [7:0] out_data;

    always #5 clk = ~clk;

    chain_ref dut (
        .clk_i(clk), .rst_i(rst),
        .in_valid(in_valid), .in_ready(in_ready), .in_data(in_data),
        .out_valid(out_valid), .out_ready(out_ready), .out_data(out_data));

    initial begin
        int edges = 200;
        int cyc = 0, ni = 0, no = 0, gap = in_gap[0], hold = 0;
        bit took;
        void'($value$plusargs("edges=%d", edges));
        repeat (edges) begin
            @(posedge clk);
            cyc++;
            if (cyc == 3) rst <= 0;
            took = in_valid && in_ready;
            if (took) begin
                $display("A in %0d @%0d", in_data, $time / 10ns);
                ni++;
                if (ni < N) gap = in_gap[ni];
                in_valid <= 0;
            end
            if ((!in_valid || took) && ni < N && cyc >= 4) begin
                if (gap == 0) begin
                    in_valid <= 1;
                    in_data  <= data[ni];
                end else
                    gap--;
            end
            if (out_valid && out_ready) begin
                $display("A out %0d @%0d", out_data, $time / 10ns);
                hold = out_hold[no];
                no++;
                if (hold > 0) out_ready <= 0;
            end else if (!out_ready) begin
                hold--;
                if (hold == 0) out_ready <= 1;
            end
        end
        $finish;
    end
endmodule
