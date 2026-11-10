// Compiled BEFORE the model files (see sail_build.sh): registers read by the instruction-fetch splice x86_fetch.sail.
$ifndef _DEFAULT_DEC
	default Order dec
$endif
$include <prelude.sail>
register code_base : bits(48)
register code_bytes : bits(128)
