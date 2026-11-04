/* Synthetic NRO fixture for tests/nx1 (our own code): the libnx crt0 layout without libnx.
 * 0x00 NroStart (branch over the header, MOD0 offset), 0x10..0x80 room for NroHeader, then MOD0. */
	.section .crt0, "ax"
	.global _start
_start:
	b	1f
	.word	__nx_mod0 - _start
	.ascii	"HOMEBREW"
	.space	0x80 - 0x10, 0          /* NroHeader is written here by the packer */
__nx_mod0:
	.ascii	"MOD0"
	.word	_DYNAMIC - __nx_mod0
	.word	__bss_start__ - __nx_mod0
	.word	__bss_end__ - __nx_mod0
	.word	0                        /* eh_frame_hdr start */
	.word	0                        /* eh_frame_hdr end */
	.word	0                        /* module object */
1:	adrp	x2, value
	ldr	w0, [x2, :lo12:value]
	svc	0x27                     /* svcOutputDebugString: on the homebrew allowlist */
	ret
	.section .rodata
	.asciz	"fnv-nx nx1 fixture"
	.data
value:	.word	42
	.bss
	.space	0x3000
