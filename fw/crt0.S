/* crt0.S - start bare-metal dla Nios V/m (RV32I), wektor resetu = 0x00000000 */
    .section .text.init, "ax"
    .globl _start
_start:
    .option push
    .option norelax
    la      gp, __global_pointer$
    .option pop
    la      sp, __stack_top
    /* bez instrukcji CSR - dziala na Nios V/c i V/m; przerwania nie sa uzywane (polling) */

    /* zerowanie .bss */
    la      t0, __bss_start
    la      t1, __bss_end
1:  bgeu    t0, t1, 2f
    sw      zero, 0(t0)
    addi    t0, t0, 4
    j       1b
2:
    call    main
3:  j       3b
