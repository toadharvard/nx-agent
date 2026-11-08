/* layoutgate.py's MSVC side, after fnvnx_compat.h: the shim's layout fillers exist to make GCC lay classes out as
 * MSVC does (TAIL1's FNVNX_MSVC_BASE_END / _PACK zero-width bit-fields, EBO2's char[0]); MSVC's own rules are the
 * reference here, so they are empty. clang's MS layout, unlike cl, opens a new unit at a zero-width bit-field after an
 * empty base (tes.cpp FUN_0045ae50: m_pkArray at 8, where the exe's 0x0045ae50 reads [ecx+4]). */
#undef FNVNX_MSVC_BASE_END
#undef FNVNX_MSVC_BASE_END_PACK
#undef FNVNX_EBO2
#define FNVNX_MSVC_BASE_END(...)
#define FNVNX_MSVC_BASE_END_PACK(N, ...)
#define FNVNX_EBO2
