module nibble_counter (
    input  logic       clk,
    input  logic       rst,
    input  logic       en,
    output logic [3:0] count
);
    logic [3:0] next;

    assign next = count + 4'd1;

    always @(posedge clk) begin
        if (rst)
            count <= 4'd0;
        else if (en)
            count <= next;
    end
endmodule

module counter (
    input  logic       clk,
    input  logic       rst,
    input  logic       en,
    output logic [3:0] count
);
    nibble_counter u_count (
        .clk   (clk),
        .rst   (rst),
        .en    (en),
        .count (count)
    );
endmodule
