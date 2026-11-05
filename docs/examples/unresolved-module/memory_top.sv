module memory_top
  (input logic clk,
   input logic [7:0] address,
   input logic [31:0] write_data,
   output logic [31:0] read_data);

  ram_model u_memory(
    .clk(clk),
    .address(address),
    .write_data(write_data),
    .read_data(read_data)
  );
endmodule
