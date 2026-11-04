/* devkitPro newlib's <sys/dirent.h> shape, for tests/nx1/syntax.sh only. */
#ifndef FNVNX_NX1_SYS_DIRENT_H
#define FNVNX_NX1_SYS_DIRENT_H
#include <sys/types.h>
#define NAME_MAX 768
struct dirent { ino_t d_ino; unsigned char d_type; char d_name[NAME_MAX + 1]; };
typedef struct { void *dirData; struct dirent fileData; } DIR;
#define DT_UNKNOWN 0
#define DT_DIR 4
#define DT_REG 8
#endif
