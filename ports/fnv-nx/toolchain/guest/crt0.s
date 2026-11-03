// fnv-nx ILP32 guest module header and entry (assembled with -mabi=ilp32).
//
// The host (LP64, libnx) maps the module at its link address (+16) and calls the module base as
//   uint64_t entry(uint64_t api)   (AAPCS64), see include/fnvnx/hostapi.h. Everything after the branch is
// data, so the module base is never executed past +0.

	.section .crt0, "ax", %progbits
	.global __fnvnx_guest_start
	.type   __fnvnx_guest_start, %function
	.align  2
__fnvnx_guest_start:
	b       __fnvnx_guest_entry                 // +0  entry, C (runtime.c)
	.word   __fnvnx_mod0 - __fnvnx_guest_start  // +4  MOD0 offset, as every Horizon module
	.word   0x47584e46                          // +8  "FNXG"
	.word   2                                   // +12 FNVNX_HOSTAPI_VERSION this guest was built for
	.word   __fnvnx_guest_base                  // +16 link address: the host maps the module exactly here
	.word   __fnvnx_guest_size                  // +20 bytes to map (text..bss, page aligned)
	.size   __fnvnx_guest_start, . - __fnvnx_guest_start

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
