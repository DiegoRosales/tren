module alu_top (
    input  logic [7:0] a,
    input  logic [7:0] b,
    input  logic       add,
    output logic [7:0] y
);
    alu u_alu (
        .a   (a),
        .b   (b),
        .add (add),
        .y   (y)
    );
endmodule
