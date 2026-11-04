/* fnv-nx P6n: flat layout for the Unicorn run (count.py maps 0x10000000-0x60000000) */
ENTRY(_start)
SECTIONS {
    . = 0x10000000;
    .text : { *(.text.start) *(.text .text.*) }
    .rodata : { *(.rodata .rodata.*) }
    .init_array : { __init_array_start = .; KEEP(*(.init_array*)) __init_array_end = .; }
    .data : { *(.data .data.*) }
    .bss : { __bss_start__ = .; *(.bss .bss.* COMMON) __bss_end__ = .; }
    . = ALIGN(0x1000);
    __heap_start = .;
    __end__ = .;
    . = 0x5FF00000;
    __heap_end = .;
    /DISCARD/ : { *(.eh_frame*) }
}
