// generate_top.sv - a top-level parameter selects the generated lanes.

module lane
  (input  logic [7:0] a,
   output logic [7:0] y);

  assign y = ~a;
endmodule

module generate_top #(parameter int LANES = 2)
  (input  logic [7:0] a,
   output logic [7:0] y);

  logic [(LANES+1)*8-1:0] stage;

  assign stage[7:0] = a;

  for (genvar i = 0; i < LANES; i++) begin : g
    lane u_lane(
      .a(stage[i*8 +: 8]),
      .y(stage[(i+1)*8 +: 8])
    );
  end

  if (LANES > 2) begin : g_wide
    lane u_wide(.a(stage[7:0]), .y());
  end

  assign y = stage[(LANES+1)*8-1 -: 8];
endmodule
