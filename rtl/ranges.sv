// Declared-range fixture.
//
// Every signal here is declared with a range that is not [width-1:0]:
// a descending range that does not end at 0, an ascending range, and a
// scalar. The demo (rtl/demo.sv) cannot carry these cases - the Pilot
// drives assert its line numbers - so the range cases live in this file.
module ranges
  (input  logic [7:1] down,
   input  logic [0:7] up,
   input  logic       sc,
   output logic [3:0] o);

  logic [15:8] nd;
  logic [0:3]  nu;
  logic        ns;

  assign nd = {down, sc};
  assign nu = up[0:3];
  assign ns = sc;
  assign o  = {nd[15:14], nu[0:1]} ^ {3'b000, ns};
endmodule
