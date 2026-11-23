# Questa:  cd sim ; vsim -c -do run_sim.do   (lub .\run_sim.ps1)
if {[file exists work]} { vdel -all -lib work }
vlib work
vcom -2008 ../quartus/ip/seq_gen16/seq_channel.vhd ../quartus/ip/seq_gen16/seq_gen16.vhd tb_seq_gen16.vhd
vcom -2008 ../quartus/reset_ctrl.vhd tb_reset_ctrl.vhd
vsim -c -onfinish stop work.tb_seq_gen16
run -all
quit -sim
vsim -c -onfinish stop work.tb_reset_ctrl
run -all
quit -f
