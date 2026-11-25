create_clock -name clk_50 -period 20.000 [get_ports clk_50]
derive_clock_uncertainty
# porty asynchroniczne (UART, wyjscia kanalow, LED)
set_false_path -from [get_ports {uart_rxd reset_n pt_sck pt_mosi pt_cs pt_miso pt_tx pt_rx pt_sda_in pt_scl_in}]
set_false_path -to   [get_ports {uart_txd ch_out[*] led_n[*] clk_base sync_out vccb_off vsel_2v5_n vsel_3v3_n vsel_5v_n pt_sck pt_mosi pt_cs pt_miso pt_tx pt_dir_spi pt_dir_miso pt_dir_tx pt_sda_drv pt_scl_drv pvcc_off psel_2v5_n psel_3v3_n psel_5v_n}]
# JTAG (debug Nios V)
create_clock -name altera_reserved_tck -period 100.000 [get_ports altera_reserved_tck]
set_clock_groups -asynchronous -group [get_clocks altera_reserved_tck]
