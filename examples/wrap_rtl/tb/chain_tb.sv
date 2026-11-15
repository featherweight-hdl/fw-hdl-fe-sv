// Runs the generated chain and the hand-wired chain_ref side by side on the
// same random stimulus and requires every output to match on every cycle.
// Also checks the stream itself: values leave in the order they entered.
module chain_tb;
    logic       clk = 0, rst = 1, rst_n = 0;
    logic       in_valid = 0, out_ready = 0;
    logic [7:0] in_data = 0;

    logic       g_in_ready, g_out_valid, r_in_ready, r_out_valid;
    logic [7:0] g_out_data, r_out_data;

    chain dut (
        .clk(clk), .rst(rst), .rst_n(rst_n),
        .in_valid(in_valid), .in_ready(g_in_ready), .in_data(in_data),
        .out_valid(g_out_valid), .out_ready(out_ready), .out_data(g_out_data));

    chain_ref ref_ (
        .clk(clk), .rst(rst), .rst_n(rst_n),
        .in_valid(in_valid), .in_ready(r_in_ready), .in_data(in_data),
        .out_valid(r_out_valid), .out_ready(out_ready), .out_data(r_out_data));

    always #5 clk = ~clk;

    int unsigned seed = 1;
    function automatic int unsigned rnd();
        seed = seed * 1103515245 + 12345;
        return seed >> 16;
    endfunction

    logic [7:0] expect_q[$];
    int errors = 0, sent = 0, recv = 0;
    logic [7:0] next_val = 0;

    initial begin
        repeat (3) @(posedge clk);
        rst <= 0; rst_n <= 1;
        repeat (2000) begin
            @(posedge clk);
            // Compare the generated design with the reference.
            if (g_in_ready !== r_in_ready || g_out_valid !== r_out_valid ||
                    (r_out_valid && g_out_data !== r_out_data)) begin
                $display("MISMATCH t=%0t in_ready %b/%b out_valid %b/%b out_data %h/%h",
                         $time, g_in_ready, r_in_ready, g_out_valid, r_out_valid,
                         g_out_data, r_out_data);
                errors++;
            end
            // Score the stream.
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
            // New stimulus: hold a value until it is taken.
            if (!in_valid || g_in_ready) begin
                in_valid <= (rnd() % 4) != 0;
                if (!in_valid || g_in_ready) begin
                    in_data  <= next_val;
                end
            end
            if (in_valid && g_in_ready) next_val = next_val + 1;
            out_ready <= (rnd() % 3) != 0;
        end
        if (errors == 0 && recv > 100)
            $display("PASS: %0d sent, %0d received, outputs match the reference", sent, recv);
        else
            $display("FAIL: %0d errors, %0d sent, %0d received", errors, sent, recv);
        $finish;
    end
endmodule
