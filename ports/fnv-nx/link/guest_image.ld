/* The statements of the guest IMAGE link that name symbols which an input object (crt0.s) defines: the ENTRY and the ASSERTs. They are judged by the image link
   alone; the synthesised-names links (ldsynth) have no crt0 and read link/guest.ld only, which therefore names only symbols it defines itself. */
ENTRY(__fnvnx_guest_start)
	/* crt0.s (rt/guest): the entry branch is the only code of .crt0 ("ax"); the 0x1c header bytes (.crt0.hdr) and the MOD0 block
	   (.crt0.mod0) are data in "a" sections. They follow the branch directly, so the module header words sit at +4..+31 as the host expects. */
	ASSERT(__fnvnx_guest_hdr == __fnvnx_guest_start + 4, ".crt0.hdr must follow the entry branch directly")
	ASSERT(__fnvnx_mod0 == __fnvnx_guest_hdr + 28, ".crt0.mod0 must follow the 28 header bytes directly")
