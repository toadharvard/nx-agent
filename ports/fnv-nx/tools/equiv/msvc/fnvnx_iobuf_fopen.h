/* fnvnx_iobuf_fopen.h - MSVC 2008 stand-in for shim/include/fnvnx_iobuf_fopen.h in S11 equivalence builds
 * (tools/equiv). On the target fopen becomes the runtime's fnvnx_iob_fopen (the game's FILE); under MSVC the game's
 * FILE is MSVC's own, so this is <stdio.h> and fopen stays fopen. */
#ifndef FNVNX_EQUIV_IOBUF_FOPEN_H
#define FNVNX_EQUIV_IOBUF_FOPEN_H
#include <stdio.h>
#endif
