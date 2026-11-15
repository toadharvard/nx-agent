/* Test fixture of the boot stage: the guest side of host API v3 as the stage implements it. The product's own copy lives with the guest runtime's owner. */
/* fnv-nx ILP32 guest module: ELF32 AArch64, static, linked at a FIXED address (-> guest.nso).
 *
 * Fixed, not PIE: stream S2 resolves the game's references to the original image as ABSOLUTE symbols
 * (__fnv_<addr> in its aliases.ld, added to this link), which a randomly placed PIE could not reach with
 * PC-relative code. The host (host side) maps the guest at its link address, which crt0 stores
 * at module +16.
 *
 * Address rules (32-bit address space, docs/TOOLCHAIN.md "Address space"):
 *   - 0x00400000..0x02100000 is reserved for the original FNV image (headers, .text range and the data
 *     window 0x00fdf000..0x02100000 that S3 maps); no native code or data may be placed there;
 *   - the guest must lie in the 32-bit small map [0x00200000, 0x40000000) (code-memory region);
 *   - default base 0x10000000; override with -Wl,--defsym=FNVNX_GUEST_BASE=0x...
 *
 * Shape as libnx's switch.ld: three PT_LOAD segments text (R-X) / rodata (R--) / data (RW-), page aligned,
 * text first, so elf2nso.py packs it like any module. If built as PIE instead (-pie), runtime.c applies
 * R_AARCH64_P32_RELATIVE relocations itself.
 */
OUTPUT_FORMAT("elf32-littleaarch64")
OUTPUT_ARCH(aarch64:ilp32)
ENTRY(__fnvnx_guest_start)

PHDRS
{
	code   PT_LOAD FLAGS(5) /* Read | Execute */;
	rodata PT_LOAD FLAGS(4) /* Read */;
	data   PT_LOAD FLAGS(6) /* Read | Write */;
	dyn    PT_DYNAMIC;   /* needed for ld to emit an ET_DYN (relocatable guest); empty in a fixed link */
	tls    PT_TLS;
}

SECTIONS
{
	. = DEFINED(FNVNX_GUEST_BASE) ? FNVNX_GUEST_BASE : 0x10000000;
	PROVIDE_HIDDEN( __fnvnx_guest_base = . );

	/* =========== CODE =========== */
	.text :
	{
		KEEP (*(.crt0))
		*(.text.unlikely .text.*_unlikely .text.unlikely.*)
		*(.text.exit .text.exit.*)
		*(.text.startup .text.startup.*)
		*(.text.hot .text.hot.*)
		*(.text .stub .text.* .gnu.linkonce.t.*)
		. = ALIGN(8);
	} :code

	.init : { KEEP( *(.init) ) . = ALIGN(8); } :code
	.plt  : { *(.plt) *(.iplt) . = ALIGN(8); } :code
	.fini : { KEEP( *(.fini) ) . = ALIGN(8); } :code

	/* =========== RODATA =========== */
	. = ALIGN(0x1000);
	.rela.dyn : { *(.rela.*) } :rodata
	.relr.dyn : { *(.relr.*) } :rodata
	.hash     : { *(.hash) } :rodata
	.gnu.hash : { *(.gnu.hash) } :rodata
	.dynsym   : { *(.dynsym) } :rodata
	.dynstr   : { *(.dynstr) } :rodata

	.rodata :
	{
		*(.rodata .rodata.* .gnu.linkonce.r.*)
		. = ALIGN(8);
	} :rodata

	.tls.align : { LONG( MAX( ALIGNOF(.tdata), ALIGNOF(.tbss) ) ) } :rodata
	PROVIDE_HIDDEN( __tls_align = ADDR(.tls.align) );

	.gcc_except_table : { *(.gcc_except_table .gcc_except_table.*) } :rodata
	.eh_frame_hdr     : { *(.eh_frame_hdr) *(.eh_frame_entry .eh_frame_entry.*) } :rodata
	.eh_frame         : { KEEP (*(.eh_frame)) *(.eh_frame.*) } :rodata
	.gnu_extab        : { *(.gnu_extab*) } :rodata
	.exception_ranges : { *(.exception_ranges .exception_ranges*) } :rodata

	PROVIDE_HIDDEN( __eh_frame_hdr_start = ADDR(.eh_frame_hdr) );
	PROVIDE_HIDDEN( __eh_frame_hdr_end   = ADDR(.eh_frame_hdr) + SIZEOF(.eh_frame_hdr) );

	.note.gnu.build-id : { *(.note.gnu.build-id) } :rodata

	/* =========== DATA (relro first: relocated at startup, stays writable as in libnx) =========== */
	. = ALIGN(0x1000);
	PROVIDE_HIDDEN( __relro_start = . );

	.preinit_array :
	{
		PROVIDE_HIDDEN( __preinit_array_start = . );
		KEEP (*(.preinit_array))
		PROVIDE_HIDDEN( __preinit_array_end = . );
	} :data

	.init_array :
	{
		PROVIDE_HIDDEN( __init_array_start = . );
		KEEP( *(SORT_BY_INIT_PRIORITY(.init_array.*) SORT_BY_INIT_PRIORITY(.ctors.*)) )
		/* the runtime libraries' unprioritized constructors (libstdc++'s exception pools, newlib's stack guard)
		 * before everyone else's, so a game unit's static constructor may throw or call the C library
		 * (os.h FNVNX_INIT_*; one input section per KEEP, and `+` is no pattern character: ??) */
		KEEP( *crtbegin*.o(.init_array .ctors) )
		KEEP( *libstdc??.a:*(.init_array .ctors) )
		KEEP( *libsupc??.a:*(.init_array .ctors) )
		KEEP( *libc.a:*(.init_array .ctors) )
		KEEP( *libg.a:*(.init_array .ctors) )
		KEEP( *libm.a:*(.init_array .ctors) )
		KEEP( *libgcc*.a:*(.init_array .ctors) )
		KEEP( *libsysbase.a:*(.init_array .ctors) )
		KEEP( *(.init_array .ctors) )
		PROVIDE_HIDDEN( __init_array_end = . );
	} :data

	.fini_array :
	{
		PROVIDE_HIDDEN( __fini_array_start = . );
		KEEP( *(SORT_BY_INIT_PRIORITY(.fini_array.*) SORT_BY_INIT_PRIORITY(.dtors.*)) )
		KEEP( *(.fini_array .dtors) )
		PROVIDE_HIDDEN( __fini_array_end = . );
	} :data

	.data.rel.ro :
	{
		/* S2 / S1: the native vtables of minus-one classes (the link's .data.rel.ro.fnvnx_m1.* sections, compiled
		   groups renamed by apply), one contiguous range S1's runtime slot rule tests a vptr against */
		. = ALIGN(8);
		PROVIDE_HIDDEN( __fnvnx_m1_start = . );
		KEEP( *(.data.rel.ro.fnvnx_m1 .data.rel.ro.fnvnx_m1.*) )
		PROVIDE_HIDDEN( __fnvnx_m1_end = . );
		/* the same with the destructor pair at slot 4 (the TESForm family): S1 shifts only the slots past 4 */
		PROVIDE_HIDDEN( __fnvnx_m1k4_start = . );
		KEEP( *(.data.rel.ro.fnvnx_m1k4 .data.rel.ro.fnvnx_m1k4.*) )
		PROVIDE_HIDDEN( __fnvnx_m1k4_end = . );
		*(.data.rel.ro.local* .gnu.linkonce.d.rel.ro.local.*)
		*(.data.rel.ro .data.rel.ro.* .gnu.linkonce.d.rel.ro.*)
		. = ALIGN(8);
	} :data

	.dynamic : { *(.dynamic) } :data :dyn

	.got     : { *(.got) *(.igot) } :data
	.got.plt : { *(.got.plt)  *(.igot.plt) } :data

	PROVIDE_HIDDEN( __got_start__ = ADDR(.got) );
	PROVIDE_HIDDEN( __got_end__   = ADDR(.got.plt) + SIZEOF(.got.plt) );

	. = ALIGN(0x1000);
	PROVIDE_HIDDEN( __data_start = . );

	.data :
	{
		*(.data .data.* .gnu.linkonce.d.*)
		SORT(CONSTRUCTORS)
		. = ALIGN(8);
	} :data

	.tdata :
	{
		*(.tdata .tdata.* .gnu.linkonce.td.*)
		. = ALIGN(8);
	} :data :tls

	PROVIDE_HIDDEN( __tdata_lma     = ADDR(.tdata) );
	PROVIDE_HIDDEN( __tdata_lma_end = ADDR(.tdata) + SIZEOF(.tdata) );

	.tbss :
	{
		*(.tbss .tbss.* .gnu.linkonce.tb.*)
		*(.tcommon)
		. = ALIGN(8);
	} :data :tls

	.bss :
	{
		*(.dynbss)
		*(.bss .bss.* .gnu.linkonce.b.*)
		*(COMMON)
		. = ALIGN(8);
	} :data

	/* TLS block of the guest main thread: [8-byte TCB][.tdata][.tbss] (AArch64 variant 1, ILP32 TCB = 8).
	   TPIDR_EL0 points at the TCB; __tls_start is the first .tdata byte. */
	.main.tls ALIGN(MAX(MAX(ALIGNOF(.tdata), ALIGNOF(.tbss)), 8)) :
	{
		. += MAX(MAX(ALIGNOF(.tdata), ALIGNOF(.tbss)), 8);
		PROVIDE_HIDDEN( __tls_start = . );
		. += SIZEOF(.tdata);
		. = ALIGN(ALIGNOF(.tbss));
		. += SIZEOF(.tbss);
		. = ALIGN(8);
	} :data

	PROVIDE_HIDDEN( __tls_tcb     = ADDR(.main.tls) );
	PROVIDE_HIDDEN( __tls_end     = ADDR(.main.tls) + SIZEOF(.main.tls) );
	PROVIDE_HIDDEN( __bss_start__ = ADDR(.bss) );
	PROVIDE_HIDDEN( __bss_end__   = __tls_end );
	PROVIDE_HIDDEN( __end__ = ALIGN(0x1000) );
	PROVIDE_HIDDEN( __fnvnx_guest_size = __end__ - __fnvnx_guest_base );

	ASSERT( __fnvnx_guest_base % 0x1000 == 0, "FNVNX_GUEST_BASE must be page aligned" )

	/DISCARD/ : { *(.group .comment .note .note.GNU-stack .interp) }

	.stab          0 : { *(.stab) }
	.stabstr       0 : { *(.stabstr) }
	.debug          0 : { *(.debug) }
	.line           0 : { *(.line) }
	.debug_srcinfo  0 : { *(.debug_srcinfo) }
	.debug_sfnames  0 : { *(.debug_sfnames) }
	.debug_aranges  0 : { *(.debug_aranges) }
	.debug_pubnames 0 : { *(.debug_pubnames) }
	.debug_info     0 : { *(.debug_info) }
	.debug_abbrev   0 : { *(.debug_abbrev) }
	.debug_line     0 : { *(.debug_line) }
	.debug_frame    0 : { *(.debug_frame) }
	.debug_str      0 : { *(.debug_str) }
	.debug_loc      0 : { *(.debug_loc) }
	.debug_macinfo  0 : { *(.debug_macinfo) }
	.debug_ranges   0 : { *(.debug_ranges) }
	.debug_rnglists 0 : { *(.debug_rnglists) }
	.debug_loclists 0 : { *(.debug_loclists) }
	.debug_line_str 0 : { *(.debug_line_str) }
	.debug_addr     0 : { *(.debug_addr) }
	.debug_str_offsets 0 : { *(.debug_str_offsets) }
}
