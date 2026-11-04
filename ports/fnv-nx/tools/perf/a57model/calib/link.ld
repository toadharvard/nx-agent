ENTRY(_start)
SECTIONS {
  . = 0x40200000;
  .text : { *(.text.start) *(.text*) }
  .rodata : { *(.rodata*) }
  .data : { *(.data*) }
  .bss : { *(.bss*) *(COMMON) }
  . = ALIGN(16);
  . = . + 0x10000;
  __stack_top = .;
  __heap = ALIGN(4096);
}
