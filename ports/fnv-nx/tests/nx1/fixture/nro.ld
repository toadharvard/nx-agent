/* three PT_LOAD segments at page boundaries from 0, as libnx's switch.ld lays them out */
OUTPUT_ARCH(aarch64)
ENTRY(_start)
PHDRS { text PT_LOAD FLAGS(5); rodata PT_LOAD FLAGS(4); data PT_LOAD FLAGS(6); dyn PT_DYNAMIC; }
SECTIONS {
	. = 0;
	.text : { *(.crt0) *(.text .text.*) } :text
	. = ALIGN(0x1000);
	.rodata : { *(.rodata .rodata.*) } :rodata
	.dynsym : { *(.dynsym) } :rodata
	.dynstr : { *(.dynstr) } :rodata
	.hash : { *(.hash) } :rodata
	.gnu.hash : { *(.gnu.hash) } :rodata
	.note.gnu.build-id : { *(.note.gnu.build-id) } :rodata
	. = ALIGN(0x1000);
	.data : { *(.data .data.*) } :data
	.dynamic : { *(.dynamic) } :data :dyn
	.got : { *(.got .got.*) } :data
	__bss_start__ = .;
	.bss (NOLOAD) : { *(.bss .bss.*) } :data
	__bss_end__ = .;
	/DISCARD/ : { *(.interp) *(.comment) }
}
