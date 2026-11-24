# Platform Designer (Qsys) component: 16-kanalowy generator sekwencji
package require -exact qsys 16.0

set_module_property NAME          seq_gen16
set_module_property VERSION       1.0
set_module_property DISPLAY_NAME  "Sequence Generator x16"
set_module_property GROUP         "Custom"
set_module_property AUTHOR        ""
set_module_property EDITABLE      false
set_module_property INTERNAL      false

add_fileset QUARTUS_SYNTH QUARTUS_SYNTH "" ""
set_fileset_property QUARTUS_SYNTH TOP_LEVEL seq_gen16
add_fileset_file seq_gen16.vhd   VHDL PATH seq_gen16.vhd TOP_LEVEL_FILE
add_fileset_file seq_channel.vhd VHDL PATH seq_channel.vhd

add_fileset SIM_VHDL SIM_VHDL "" ""
set_fileset_property SIM_VHDL TOP_LEVEL seq_gen16
add_fileset_file seq_gen16.vhd   VHDL PATH seq_gen16.vhd
add_fileset_file seq_channel.vhd VHDL PATH seq_channel.vhd

add_parameter CLK_HZ INTEGER 50000000
set_parameter_property CLK_HZ HDL_PARAMETER true
set_parameter_property CLK_HZ SYSTEM_INFO {CLOCK_RATE clock}
set_parameter_property CLK_HZ DISPLAY_NAME "Clock frequency (auto)"

# --- clock / reset
add_interface clock clock end
add_interface_port clock clk clk Input 1

add_interface reset reset end
set_interface_property reset associatedClock clock
set_interface_property reset synchronousEdges DEASSERT
add_interface_port reset reset reset Input 1

# --- Avalon-MM slave
add_interface s0 avalon end
set_interface_property s0 addressUnits WORDS
set_interface_property s0 associatedClock clock
set_interface_property s0 associatedReset reset
set_interface_property s0 readLatency 2
set_interface_property s0 readWaitTime 0
set_interface_property s0 writeWaitTime 0
set_interface_property s0 maximumPendingReadTransactions 0
add_interface_port s0 avs_address   address   Input  15
add_interface_port s0 avs_read      read      Input  1
add_interface_port s0 avs_write     write     Input  1
add_interface_port s0 avs_writedata writedata Input  32
add_interface_port s0 avs_readdata  readdata  Output 32

# --- wyjscia kanalow (eksportowane na piny)
add_interface out conduit end
set_interface_property out associatedClock clock
add_interface_port out ch_out     ch_out     Output 16
add_interface_port out ch_running ch_running Output 16
add_interface_port out clk_base   clk_base   Output 1
add_interface_port out sync_out   sync_out   Output 1
add_interface_port out vccb_ctrl  vccb_ctrl  Output 4
