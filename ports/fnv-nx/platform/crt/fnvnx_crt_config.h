#ifndef FNVNX_CRT_CONFIG_H
#define FNVNX_CRT_CONFIG_H
/* Settings the CRT files (iob.c, stdio.c) share, defined once. It is included before any system header: _FILE_OFFSET_BITS is a feature
 * macro. */
#define _FILE_OFFSET_BITS 64   /* off_t is 64-bit: the MSVC CRT's _lseeki64 / _ftelli64 family */
#define FNVNX_CRT_HOST_MAX 1024   /* the longest host path the path mapper writes (fnvnx_path_map's buffer) */
#endif
