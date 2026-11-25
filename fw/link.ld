/* link.ld - caly program w 12 KB on-chip RAM (12 blokow M9K; 16 KB nie miesci sie z testerem protokolow) */
OUTPUT_ARCH("riscv")
ENTRY(_start)

MEMORY
{
    ram (rwx) : ORIGIN = 0x00000000, LENGTH = 12K
}

SECTIONS
{
    .text : {
        KEEP(*(.text.init))
        *(.text .text.*)
    } > ram

    .rodata : { *(.rodata .rodata.* .srodata .srodata.*) } > ram

    .data : {
        *(.data .data.*)
        __global_pointer$ = . + 0x800;
        *(.sdata .sdata.*)
    } > ram

    .bss (NOLOAD) : {
        __bss_start = .;
        *(.sbss .sbss.* .bss .bss.* COMMON)
        . = ALIGN(4);
        __bss_end = .;
    } > ram

    __stack_top = ORIGIN(ram) + LENGTH(ram);
    __stack_size = 1024;
    ASSERT(__bss_end + __stack_size <= __stack_top, "Brak miejsca na stos w RAM")
}
