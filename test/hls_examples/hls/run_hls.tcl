# Set block variable
set block [lindex $argv 2]

# Print block value to console
puts "Block = $block"

# Change directory to block folder
cd ${block}

# Source configuration file
source ./config.tcl

# Set HLS top module name and FPGA part
set top $hls_top
set fpga xc7z010clg400-1

# Open the project and set the top module
open_project -reset hls_project
set_top $top

# Add C++ files to the project
foreach file $cpp_files {
    add_files $file
}

# Open a solution and set the FPGA part
open_solution -reset solution_1
set_part $fpga

# Create a 10ns clock period and source directives
create_clock -period 10.0 -name default
source ./directives.tcl

# Synthesize the design
csynth_design

# Export the design as an IP catalog
export_design -format ip_catalog

# Copy the RTL to the rtl_output directory
exec mkdir -p ./rtl_output/
exec cp -rf ./hls_project/solution_1/impl/verilog ./rtl_output
exec cp ./hls_project/solution_1/impl/ip/component.xml ./rtl_output

# Exit the script
exit
