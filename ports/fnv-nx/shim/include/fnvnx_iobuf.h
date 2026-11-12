/* fnv-nx shim: the game's FILE (MSVC struct _iobuf) and the CRT stdio functions on it - S4b's header
 * (platform/crt/fnvnx_iobuf.h), reachable from the game's include path. prepare.py DECL1 includes it where it
 * drops the decomp's own _iobuf prototypes of the stdio functions; DECL2 where it drops a copy of struct _iobuf. */
#include "../../platform/crt/fnvnx_iobuf.h"
