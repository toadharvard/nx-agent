/* mt.ld - fixed-address ILP32 guest for linhost (tests/s15/mtqemu). TLS laid out as toolchain/guest/guest.ld does it,
 * so toolchain/guest/threads.c finds the same symbols: [TCB][.tdata][.tbss], TPIDR_EL0 -> TCB (ILP32 TCB = 8). */
OUTPUT_FORMAT("elf32-littleaarch64")
ENTRY(_start)
PHDRS
{
	code PT_LOAD FLAGS(5);
	data PT_LOAD FLAGS(6);
	tls  PT_TLS;
}
SECTIONS
{
	. = 0x20000000;   /* above linhost (0x10000000 + its .bss), below qemu-user's stack with -R 0xC0000000 (~0x40800000) */
	.text : { *(.text.mtq_entry) *(.text .text.*) } :code
	.rodata : { *(.rodata .rodata.*) . = ALIGN(8); } :code
	.tls.align : { LONG( MAX( ALIGNOF(.tdata), ALIGNOF(.tbss) ) ) } :code
	PROVIDE_HIDDEN( __tls_align = ADDR(.tls.align) );
	. = ALIGN(0x1000);
	.data : { *(.data .data.*) . = ALIGN(8); } :data
	.init_array : {
		PROVIDE_HIDDEN( __init_array_start = . );
		KEEP(*(SORT(.init_array.*))) KEEP(*(.init_array))
		PROVIDE_HIDDEN( __init_array_end = . );
	} :data
	.tdata : { *(.tdata .tdata.*) . = ALIGN(8); } :data :tls
	PROVIDE_HIDDEN( __tdata_lma     = ADDR(.tdata) );
	PROVIDE_HIDDEN( __tdata_lma_end = ADDR(.tdata) + SIZEOF(.tdata) );
	.tbss : { *(.tbss .tbss.*) *(.tcommon) . = ALIGN(8); } :data :tls
	.bss : { *(.bss .bss.*) *(COMMON) . = ALIGN(8); } :data
	.main.tls ALIGN(MAX(MAX(ALIGNOF(.tdata), ALIGNOF(.tbss)), 8)) :
	{
		. += MAX(MAX(ALIGNOF(.tdata), ALIGNOF(.tbss)), 8);
		PROVIDE_HIDDEN( __tls_start = . );
		. += SIZEOF(.tdata);
		. = ALIGN(ALIGNOF(.tbss));
		. += SIZEOF(.tbss);
		. = ALIGN(8);
	} :data
	PROVIDE_HIDDEN( __tls_tcb = ADDR(.main.tls) );
	PROVIDE_HIDDEN( __tls_end = ADDR(.main.tls) + SIZEOF(.main.tls) );
	/DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
