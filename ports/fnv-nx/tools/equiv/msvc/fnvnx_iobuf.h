/* fnvnx_iobuf.h - MSVC 2008 stand-in for shim/include/fnvnx_iobuf.h in S11 equivalence builds (tools/equiv).
 *
 * The target header is the game's FILE (MSVC's struct _iobuf) and the CRT stdio functions on it (S4b). Under MSVC
 * that is exactly MSVC's own <stdio.h>. DECL1 / DECL2 include it where they drop the decomp's copies. */
#ifndef FNVNX_EQUIV_IOBUF_H
#define FNVNX_EQUIV_IOBUF_H
#include <stdio.h>
#endif
