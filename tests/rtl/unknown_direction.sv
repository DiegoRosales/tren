// Unknown-direction fixture.
//
// `vendor_ip` has no definition in this source set. yosys emits the cell
// with no port_directions and no child module, so its pins have no known
// direction. TREN must show the endpoint in both traces, marked, and never
// guess a direction (a wrong guess drops a driver from every trace through
// the instance).
module unknown_direction
  (input  logic a,
   output logic y);

  logic w;
  vendor_ip u_ip(.din(w), .dout(y));
  assign w = a;
endmodule
