/* fnvnx_names_pre.h - force-included LAST by the name pass (toolchain/vtnames: clang, MSVC ABI, the shim's types).
 * The shim erases the x86 calling-convention keywords for the AArch64 build; the MSVC name of a function encodes
 * its convention, so the name pass gives them back to the compiler. */
#undef __cdecl
#undef __stdcall
#undef __fastcall
#undef __thiscall
