create_clock -name clk_50 -period 20.000 [get_ports clk_50]
derive_clock_uncertainty
# porty asynchroniczne (UART, wyjscia kanalow, LED)
set_false_path -from [get_ports uart_rxd]
set_false_path -to   [get_ports {uart_txd ch_out[*] led_n[*] clk_base sync_out}]
# JTAG (debug Nios V)
create_clock -name altera_reserved_tck -period 100.000 [get_ports altera_reserved_tck]
set_clock_groups -asynchronous -group [get_clocks altera_reserved_tck]
