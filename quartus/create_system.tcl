# Tworzy system Platform Designer nios_sys.qsys
#   qsys-script --script=create_system.tcl     (uruchamiac w katalogu quartus/)
#
# Mapa adresow (musi zgadzac sie z fw/hw.h):
#   0x0000_0000  ram          16 KB  on-chip RAM (kod + dane, init z onchip_ram.hex)
#   0x0001_0000  cpu.dm_agent        debug (tylko wariant niosv_m)
#   0x0002_0000  cpu.timer_sw_agent  mtime (tylko wariant niosv_m)
#   0x0002_1000  uart                RS-232 115200 8N1
#   0x0003_0000  proto        32 B   tester protokolow SPI / I2C / UART (J6)
#   0x0004_0000  seqgen       128 KB generator 16 kanalow
package require -exact qsys 16.0

create_system nios_sys
set_project_property DEVICE_FAMILY "MAX 10"
set_project_property DEVICE 10M08SAE144C8G

# ---------------------------------------------------------------- zegar
add_instance clk_0 clock_source
set_instance_parameter_value clk_0 clockFrequency      50000000
set_instance_parameter_value clk_0 clockFrequencyKnown true
set_instance_parameter_value clk_0 resetSynchronousEdges DEASSERT

# ---------------------------------------------------------------- CPU
# niosv_c (domyslnie): ~1 tys. LE mniej, bez debugera JTAG - firmware ladowany
#                      z obrazem FPGA (onchip_ram.hex)
# niosv_m           : z debugerem JTAG (niosv-download / RiscFree debug), ale
#                     uklad 10M08 jest wtedy zapelniony w ~96 %
if {![info exists CPU]} { set CPU niosv_c }

if {[string equal $CPU niosv_m]} {
    add_instance cpu intel_niosv_m
    set_instance_parameter_value cpu enableDebug   1
    set_instance_parameter_value cpu pipelineArch  0
} else {
    add_instance cpu intel_niosv_c
}
set_instance_parameter_value cpu resetSlave    Absolute
set_instance_parameter_value cpu resetOffset   0
set_instance_parameter_value cpu enableAvalonInterface 1

# ---------------------------------------------------------------- RAM
add_instance ram altera_avalon_onchip_memory2
set_instance_parameter_value ram memorySize            16384
set_instance_parameter_value ram dataWidth             32
set_instance_parameter_value ram initMemContent        true
set_instance_parameter_value ram useNonDefaultInitFile true
set_instance_parameter_value ram initializationFileName onchip_ram.hex
set_instance_parameter_value ram blockType             M9K

# ---------------------------------------------------------------- UART RS-232
add_instance uart altera_avalon_uart
set_instance_parameter_value uart baud       115200
set_instance_parameter_value uart fixedBaud  true
set_instance_parameter_value uart parity     NONE
set_instance_parameter_value uart dataBits   8
set_instance_parameter_value uart stopBits   1
set_instance_parameter_value uart useCtsRts  false

# ---------------------------------------------------------------- generator
add_instance seqgen seq_gen16

# ---------------------------------------------------------------- tester protokolow
add_instance proto proto_io

# ---------------------------------------------------------------- polaczenia
foreach {c r} {cpu.clk cpu.reset ram.clk1 ram.reset1 uart.clk uart.reset seqgen.clock seqgen.reset proto.clock proto.reset} {
    add_connection clk_0.clk       $c
    add_connection clk_0.clk_reset $r
}
add_connection cpu.data_manager ram.s1
add_connection cpu.data_manager uart.s1
add_connection cpu.data_manager seqgen.s0
add_connection cpu.data_manager proto.s0
add_connection cpu.instruction_manager ram.s1

if {[string equal $CPU niosv_m]} {
    add_connection cpu.data_manager cpu.dm_agent
    add_connection cpu.data_manager cpu.timer_sw_agent
    add_connection cpu.instruction_manager cpu.dm_agent
    add_connection cpu.platform_irq_rx uart.irq
    set_connection_parameter_value cpu.platform_irq_rx/uart.irq irqNumber 0
    set_connection_parameter_value cpu.data_manager/cpu.dm_agent         baseAddress 0x00010000
    set_connection_parameter_value cpu.instruction_manager/cpu.dm_agent  baseAddress 0x00010000
    set_connection_parameter_value cpu.data_manager/cpu.timer_sw_agent   baseAddress 0x00020000
}

set_connection_parameter_value cpu.data_manager/ram.s1               baseAddress 0x00000000
set_connection_parameter_value cpu.instruction_manager/ram.s1        baseAddress 0x00000000
set_connection_parameter_value cpu.data_manager/uart.s1              baseAddress 0x00021000
set_connection_parameter_value cpu.data_manager/seqgen.s0            baseAddress 0x00040000
set_connection_parameter_value cpu.data_manager/proto.s0             baseAddress 0x00030000

# ---------------------------------------------------------------- eksporty
set_interface_property clk    EXPORT_OF clk_0.clk_in
set_interface_property reset  EXPORT_OF clk_0.clk_in_reset
set_interface_property uart   EXPORT_OF uart.external_connection
set_interface_property seqgen EXPORT_OF seqgen.out
set_interface_property proto  EXPORT_OF proto.pins

save_system nios_sys.qsys
