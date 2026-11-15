// Compiled BEFORE the model files (see sail_build.sh): registers read by the instruction-fetch splice x86_fetch.sail.
$ifndef _DEFAULT_DEC
	default Order dec
$endif
$include <prelude.sail>
register code_base : bits(48)
register code_bytes : bits(128)
// Segment base of FS and GS (patch sail_patches/segment_bases.sail): free 32-bit values, symbolic in every proof run (the prover binds them to the variables fs_base / gs_base
// that the DSL forms use).  Without them the driver cannot tell which memory access of an instruction goes through FS / GS (a push uses SS, a string destination ES).
register fs_base_reg : bits(32)
register gs_base_reg : bits(32)
