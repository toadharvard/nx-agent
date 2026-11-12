/* fnv-nx lifted guest: ELF32 AArch64 ILP32, static PIE, linked at base 0 and placed by the host (link stage `ld`).
 *
 * A position independent link: nothing is placed at a fixed address, so there is no address-range ASSERT (the host picks
 * the base; the exe image is mapped by the loader at run time and is not an input).  Absolute data pointers become
 * R_AARCH64_P32_RELATIVE entries of .rela.dyn that the loader applies.
 *
 * Shape as libnx's switch.ld: three PT_LOAD segments text (R-X) / rodata (R--) / data (RW-), page aligned, text first,
 * plus PT_DYNAMIC and PT_TLS.  Order inside the text segment: .crt0 (the entry branch, ENTRY __fnvnx_guest_start), then .crt0.hdr and .crt0.mod0 (data, "a"), then
 * the ordinary code, then the lifted bodies (input section .text.fnvl, one `void L_<addr>(void)` each) as an output
 * section of their own.  The perfect-hash dispatcher table of the lifted code (input section .rodata.fnvl_disp,
 * lift/tools/phf.py table_asm) is kept in rodata.
 */
OUTPUT_FORMAT("elf32-littleaarch64")
OUTPUT_ARCH(aarch64:ilp32)
ENTRY(__fnvnx_guest_start)

PHDRS
{
	code   PT_LOAD FLAGS(5) /* Read | Execute */;
	rodata PT_LOAD FLAGS(4) /* Read */;
	data   PT_LOAD FLAGS(6) /* Read | Write */;
	dyn    PT_DYNAMIC;   /* the loader reads the dynamic relocations from it */
	tls    PT_TLS;
}

SECTIONS
{
	. = 0;
	HIDDEN( __fnvnx_guest_base = . );

	/* =========== CODE =========== */
	/* The first matching statement of the script claims an input section, so `.text.*` would take .text.fnvl.  The
	   name patterns below are `.text.` followed by every suffix except exactly "fnvl", character by character. */
	.text :
	{
		KEEP (*(.crt0))
		KEEP (*(.crt0.hdr))
		KEEP (*(.crt0.mod0))
		*(.text.unlikely .text.*_unlikely .text.unlikely.*)
		*(.text.exit .text.exit.*)
		*(.text.startup .text.startup.*)
		*(.text.hot .text.hot.*)
		*(.text .gnu.linkonce.t.*)
		*(.text.[!f]* .text.f .text.f[!n]* .text.fn .text.fn[!v]* .text.fnv .text.fnv[!l]* .text.fnvl?*)
		. = ALIGN(8);
	} :code

	/* crt0.s (toolchain/guest): the entry branch is the only code of .crt0 ("ax"); the 0x1c header bytes (.crt0.hdr) and the MOD0 block
	   (.crt0.mod0) are data in "a" sections. They follow the branch directly, so the module header words sit at +4..+31 as the host expects. */
	ASSERT(__fnvnx_guest_hdr == __fnvnx_guest_start + 4, ".crt0.hdr must follow the entry branch directly")
	ASSERT(__fnvnx_mod0 == __fnvnx_guest_hdr + 28, ".crt0.mod0 must follow the 28 header bytes directly")

	.text.fnvl :
	{
		KEEP (*(.text.fnvl))
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
		KEEP (*(.rodata.fnvl_disp .rodata.fnvl_disp.*))
		*(.rodata .rodata.* .gnu.linkonce.r.*)
		. = ALIGN(8);
	} :rodata

	.tls.align : { LONG( MAX( ALIGNOF(.tdata), ALIGNOF(.tbss) ) ) } :rodata
	HIDDEN( __tls_align = ADDR(.tls.align) );

	.gcc_except_table : { *(.gcc_except_table .gcc_except_table.*) } :rodata
	.eh_frame_hdr     : { *(.eh_frame_hdr) *(.eh_frame_entry .eh_frame_entry.*) } :rodata
	.eh_frame         : { KEEP (*(.eh_frame)) *(.eh_frame.*) } :rodata
	.gnu_extab        : { *(.gnu_extab*) } :rodata
	.exception_ranges : { *(.exception_ranges .exception_ranges*) } :rodata

	HIDDEN( __eh_frame_hdr_start = ADDR(.eh_frame_hdr) );
	HIDDEN( __eh_frame_hdr_end   = ADDR(.eh_frame_hdr) + SIZEOF(.eh_frame_hdr) );

	.note.gnu.build-id : { *(.note.gnu.build-id) } :rodata

	/* =========== DATA (relro first: relocated at startup, stays writable as in libnx) =========== */
	. = ALIGN(0x1000);
	HIDDEN( __relro_start = . );

	.preinit_array :
	{
		HIDDEN( __preinit_array_start = . );
		KEEP (*(.preinit_array))
		HIDDEN( __preinit_array_end = . );
	} :data

	.init_array :
	{
		HIDDEN( __init_array_start = . );
		KEEP( *(SORT_BY_INIT_PRIORITY(.init_array.*) SORT_BY_INIT_PRIORITY(.ctors.*)) )
		/* the runtime libraries' unprioritized constructors (libstdc++'s exception pools, newlib's stack guard)
		 * before everyone else's, so a game unit's static constructor may throw or call the C library
		 * (platform/os/os.h FNVNX_INIT_*, toolchain/game/init_order.py; one input section per KEEP, and `+` is no pattern character: ??) */
		KEEP( *crtbegin*.o(.init_array .ctors) )
		KEEP( *libstdc??.a:*(.init_array .ctors) )
		KEEP( *libsupc??.a:*(.init_array .ctors) )
		KEEP( *libc.a:*(.init_array .ctors) )
		KEEP( *libg.a:*(.init_array .ctors) )
		KEEP( *libm.a:*(.init_array .ctors) )
		KEEP( *libgcc*.a:*(.init_array .ctors) )
		KEEP( *libsysbase.a:*(.init_array .ctors) )
		KEEP( *(.init_array .ctors) )
		HIDDEN( __init_array_end = . );
	} :data

	.fini_array :
	{
		HIDDEN( __fini_array_start = . );
		KEEP( *(SORT_BY_INIT_PRIORITY(.fini_array.*) SORT_BY_INIT_PRIORITY(.dtors.*)) )
		KEEP( *(.fini_array .dtors) )
		HIDDEN( __fini_array_end = . );
	} :data

	.data.rel.ro :
	{
		. = ALIGN(8);
		*(.data.rel.ro.local* .gnu.linkonce.d.rel.ro.local.*)
		*(.data.rel.ro .data.rel.ro.* .gnu.linkonce.d.rel.ro.*)
		. = ALIGN(8);
	} :data

	.dynamic : { *(.dynamic) } :data :dyn
	/* the start of .dynamic as a section-relative symbol: the linker's own _DYNAMIC is absolute (gate abs) */
	HIDDEN( __fnvnx_dynamic = ADDR(.dynamic) );

	.got     : { *(.got) *(.igot) } :data
	.got.plt : { *(.got.plt)  *(.igot.plt) } :data

	HIDDEN( __got_start__ = ADDR(.got) );
	HIDDEN( __got_end__   = ADDR(.got.plt) + SIZEOF(.got.plt) );

	. = ALIGN(0x1000);
	HIDDEN( __data_start = . );

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

	HIDDEN( __tdata_lma     = ADDR(.tdata) );
	HIDDEN( __tdata_lma_end = ADDR(.tdata) + SIZEOF(.tdata) );

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
		HIDDEN( __tls_start = . );
		. += SIZEOF(.tdata);
		. = ALIGN(ALIGNOF(.tbss));
		. += SIZEOF(.tbss);
		. = ALIGN(8);
	} :data

	HIDDEN( __tls_tcb     = ADDR(.main.tls) );
	HIDDEN( __tls_end     = ADDR(.main.tls) + SIZEOF(.main.tls) );
	HIDDEN( __bss_start__ = ADDR(.bss) );
	HIDDEN( __bss_end__   = __tls_end );
	HIDDEN( __end__ = ALIGN(0x1000) );
	HIDDEN( __fnvnx_guest_size = __end__ - __fnvnx_guest_base );

	ASSERT( __fnvnx_guest_base % 0x1000 == 0, "the guest base must be page aligned" )

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
