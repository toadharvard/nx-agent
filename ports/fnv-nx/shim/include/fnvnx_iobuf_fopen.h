/* fnv-nx shim: fopen on the game's FILE. prepare.py DECL1 includes it in place of the decomp's own
 * `_iobuf* fopen(const char*, const char*)` prototype (DECL1-FOPEN). fopen has libc's parameters, so no C++ overload
 * can return the runtime's struct _iobuf (fnvnx_iobuf.h): from here on, fopen in this unit is fnvnx_iob_fopen, the
 * provider of the CRT fopen that units.json binds (platform/crt/crt_symbols.tsv). That holds for std::fopen too (MSVC's
 * std::fopen is the same CRT function). <cstdio> comes first (through fnvnx_iobuf.h), so libc's own declaration keeps
 * its name; libstdc++'s headers only call fopen out of line. */
#ifndef FNVNX_IOBUF_FOPEN_H
#define FNVNX_IOBUF_FOPEN_H
#include "fnvnx_iobuf.h"
#ifdef __cplusplus
namespace std { using ::fnvnx_iob_fopen; }
#endif
#ifndef fopen
#define fopen fnvnx_iob_fopen
#endif
#endif
