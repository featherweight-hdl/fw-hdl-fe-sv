// Runs the generated fifo_chain and the hand-wired fifo_chain_ref side by
// side, at the parameters given with -G (N stages, data width W, DEEP_TAIL),
// on the same random stimulus, and requires every output to match on every
// cycle. Also checks the stream: values leave in the order they entered.
module fifo_chain_tb #(
    parameter int W         = 8,
    parameter int N         = 2,
    parameter bit DEEP_TAIL = 0
);
    logic         clk = 0, rst = 1;
    logic         in_valid = 0, out_ready = 0;
    logic [W-1:0] in_data = 0;

    logic         g_in_ready, g_out_valid, r_in_ready, r_out_valid;
    logic [W-1:0] g_out_data, r_out_data;

    fifo_chain #(.T(logic [W-1:0]), .n(N), .deep_tail(DEEP_TAIL)) dut (
        .clk(clk), .rst(rst),
        .in_valid(in_valid), .in_ready(g_in_ready), .in_data(in_data),
        .out_valid(g_out_valid), .out_ready(out_ready), .out_data(g_out_data));

    fifo_chain_ref #(.W(W), .N(N), .DEEP_TAIL(DEEP_TAIL)) ref_ (
        .clk(clk), .rst(rst),
        .in_valid(in_valid), .in_ready(r_in_ready), .in_data(in_data),
        .out_valid(r_out_valid), .out_ready(out_ready), .out_data(r_out_data));

    always #5 clk = ~clk;

    int unsigned seed = 1;
    function automatic int unsigned rnd();
        seed = seed * 1103515245 + 12345;
        return seed >> 16;
    endfunction

    logic [W-1:0] expect_q[$];
    int errors = 0, sent = 0, recv = 0;
    logic [W-1:0] next_val = 0;

    initial begin
        repeat (3) @(posedge clk);
        rst <= 0;
        repeat (2000) begin
            @(posedge clk);
            if (g_in_ready !== r_in_ready || g_out_valid !== r_out_valid ||
                    (r_out_valid && g_out_data !== r_out_data)) begin
                $display("MISMATCH t=%0t in_ready %b/%b out_valid %b/%b out_data %h/%h",
                         $time, g_in_ready, r_in_ready, g_out_valid, r_out_valid,
                         g_out_data, r_out_data);
                errors++;
            end
            if (in_valid && g_in_ready) begin
                expect_q.push_back(in_data);
                sent++;
            end
            if (g_out_valid && out_ready) begin
                if (expect_q.size() == 0 || expect_q[0] !== g_out_data) begin
                    $display("ORDER t=%0t got %h", $time, g_out_data);
                    errors++;
                end else
                    void'(expect_q.pop_front());
                recv++;
            end
            if (!in_valid || g_in_ready) begin
                in_valid <= (rnd() % 4) != 0;
                in_data  <= next_val;
            end
            if (in_valid && g_in_ready) next_val = next_val + 1;
            out_ready <= (rnd() % 3) != 0;
        end
        if (errors == 0 && recv > 100)
            $display("PASS: N=%0d W=%0d DEEP_TAIL=%0d: %0d sent, %0d received, outputs match the reference",
                     N, W, DEEP_TAIL, sent, recv);
        else
            $display("FAIL: %0d errors, %0d sent, %0d received", errors, sent, recv);
        $finish;
    end
endmodule
