# Questa:  cd sim ; vsim -c -do run_sim.do
if {[file exists work]} { vdel -all -lib work }
vlib work
vcom -2008 ../quartus/ip/seq_gen16/seq_channel.vhd ../quartus/ip/seq_gen16/seq_gen16.vhd tb_seq_gen16.vhd
vsim -c work.tb_seq_gen16
run -all
quit -f
