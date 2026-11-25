# Platform Designer (Qsys) component: tester protokolow SPI / I2C / UART (zlacze J6)
package require -exact qsys 16.0

set_module_property NAME          proto_io
set_module_property VERSION       1.0
set_module_property DISPLAY_NAME  "Protocol tester (SPI / I2C / UART)"
set_module_property GROUP         "Custom"
set_module_property AUTHOR        ""
set_module_property EDITABLE      false
set_module_property INTERNAL      false

add_fileset QUARTUS_SYNTH QUARTUS_SYNTH "" ""
set_fileset_property QUARTUS_SYNTH TOP_LEVEL proto_io
add_fileset_file pt_ram.vhd   VHDL PATH pt_ram.vhd
add_fileset_file proto_io.vhd VHDL PATH proto_io.vhd TOP_LEVEL_FILE

add_fileset SIM_VHDL SIM_VHDL "" ""
set_fileset_property SIM_VHDL TOP_LEVEL proto_io
add_fileset_file pt_ram.vhd   VHDL PATH pt_ram.vhd
add_fileset_file proto_io.vhd VHDL PATH proto_io.vhd

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
add_interface_port s0 avs_address   address   Input  3
add_interface_port s0 avs_read      read      Input  1
add_interface_port s0 avs_write     write     Input  1
add_interface_port s0 avs_writedata writedata Input  32
add_interface_port s0 avs_readdata  readdata  Output 32

# --- linie J6 (eksportowane na piny)
add_interface pins conduit end
set_interface_property pins associatedClock clock
add_interface_port pins sck_o     sck_o     Output 1
add_interface_port pins mosi_o    mosi_o    Output 1
add_interface_port pins cs_o      cs_o      Output 1
add_interface_port pins miso_o    miso_o    Output 1
add_interface_port pins tx_o      tx_o      Output 1
add_interface_port pins dir_spi   dir_spi   Output 1
add_interface_port pins dir_miso  dir_miso  Output 1
add_interface_port pins dir_tx    dir_tx    Output 1
add_interface_port pins sda_drv   sda_drv   Output 1
add_interface_port pins scl_drv   scl_drv   Output 1
add_interface_port pins pins_i    pins_i    Input  8
add_interface_port pins pvcc_ctrl pvcc_ctrl Output 4
