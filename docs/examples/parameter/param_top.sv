module adder #(parameter int WIDTH = 8)
  (input  logic [WIDTH-1:0] a,
   input  logic [WIDTH-1:0] b,
   output logic [WIDTH-1:0] y);

  assign y = a + b;
endmodule

module param_top #(parameter int WIDTH = 8)
  (input  logic [WIDTH-1:0] a,
   input  logic [WIDTH-1:0] b,
   output logic [WIDTH-1:0] y);

  adder #(WIDTH) u_adder (.a(a), .b(b), .y(y));
endmodule
