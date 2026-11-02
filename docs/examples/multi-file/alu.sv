module alu (
    input  logic [7:0] a,
    input  logic [7:0] b,
    input  logic       add,
    output logic [7:0] y
);
    assign y = add ? a + b : a - b;
endmodule
