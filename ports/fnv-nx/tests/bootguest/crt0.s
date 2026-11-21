// Test fixture of the boot stage: the guest side of host API v3 as the stage implements it. The product's own copy lives with the guest runtime's owner.
// fnv-nx ILP32 guest module header and entry (assembled with -mabi=ilp32).
//
// The host (LP64, libnx) maps the module (at its link address +16 if fixed, anywhere if relocatable, +24 bit 0)
// and calls the module base as   uint64_t entry(uint64_t api)   (AAPCS64), see include/fnvnx/hostapi.h.
// Everything after the branch is data, so the module base is never executed past +0.
// +16/+20/+24 are written by elf2nso.py from the ELF (a PIE cannot hold its link address in .text: an absolute
// word there would need a text relocation).

	// Input sections by the rule "data never in an ax section": .crt0 "ax" holds the entry branch only; the header words and the
	// MOD0 block are "a". guest.ld places them first in .text, in this order, so the bytes and the layout are those of one section.
	.section .crt0, "ax", %progbits
	.global __fnvnx_guest_start
	.type   __fnvnx_guest_start, %function
	.align  2
__fnvnx_guest_start:
	b       __fnvnx_guest_entry                 // +0  entry, C (runtime.c)
	.size   __fnvnx_guest_start, . - __fnvnx_guest_start

	.section .crt0.hdr, "a", %progbits
	.align  2
	.word   __fnvnx_mod0 - . + 4                // +4  MOD0 offset, as every Horizon module (this word is at +4 of the module)
	.word   0x47584e46                          // +8  "FNXG"
	.word   3                                   // +12 FNVNX_HOSTAPI_VERSION this guest was built for
	.word   0                                   // +16 link address of the first segment (elf2nso)
	.word   0                                   // +20 bytes to map, text..bss, page aligned (elf2nso)
	.word   0                                   // +24 flags: bit 0 relocatable (ET_DYN: R_AARCH64_P32_RELATIVE)
	.word   0                                   // +28 reserved

	.section .crt0.mod0, "a", %progbits
	.align  2
	.weak   _DYNAMIC                            // absent in the fixed-address (non-PIE) build
	.global __fnvnx_mod0
__fnvnx_mod0:
	.ascii  "MOD0"
	.word   _DYNAMIC             - __fnvnx_mod0
	.word   __bss_start__        - __fnvnx_mod0
	.word   __bss_end__          - __fnvnx_mod0
	.word   __eh_frame_hdr_start - __fnvnx_mod0
	.word   __eh_frame_hdr_end   - __fnvnx_mod0
	.word   0                                   // runtime module object: unused

	// Same homebrew MOD0 extensions as libnx (debuggers and crash reporters read them).
	.ascii  "LNY0"
	.word   __got_start__        - __fnvnx_mod0
	.word   __got_end__          - __fnvnx_mod0
	.ascii  "LNY1"
	.word   __relro_start        - __fnvnx_mod0
	.word   __data_start         - __fnvnx_mod0
	.ascii  "LNY2"
	.word   0x1
	.word   0x0

	// TPIDR_EL0 read for -mtp=soft code (libnx's convention name); the guest's TLS register is TPIDR_EL0.
	.section .text.__aarch64_read_tp, "ax", %progbits
	.global __aarch64_read_tp
	.type   __aarch64_read_tp, %function
	.align  2
__aarch64_read_tp:
	mrs     x0, tpidr_el0
	ret
	.size   __aarch64_read_tp, . - __aarch64_read_tp
