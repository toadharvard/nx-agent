/* stdio.c - CRT file functions that take Windows paths: they go through the runtime's path mapper
 * (win32/path.cpp), then to the target libc / os layer.
 *
 * Known gap: MSVC text mode ("r"/"w" without 'b') translates CRLF <-> LF; here files are always binary, so
 * text read by fgets keeps its '\r'. See docs/RUNTIME.md. */
#define _FILE_OFFSET_BITS 64
#include "fnvnx_msvcrt.h"

#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>

/* the platform lane (platform/win32/platstat.cpp; fnvnx_win32_ext.h is not included in C here) */
extern volatile int fnvnx_plat_on;
void fnvnx_plat_open_failed(const char *win, const char *host, unsigned err, unsigned caller, const char *api);
#define PLAT_RA ((unsigned)(unsigned long)__builtin_return_address(0))

#include "../os/os.h"

/* from win32 (C++ side; fnvnx_win32.h is C++-only, so the prototypes are repeated here) */
unsigned long fnvnx_path_map(const char *win, char *host, size_t n);
unsigned long GetCurrentDirectoryA(unsigned long n, char *buf);
int SetCurrentDirectoryA(const char *dir);

#define HOST_MAX 1024

static int map(const char *win, char *host)
{
    if (!win) {
        errno = EINVAL;
        return 0;
    }
    if (fnvnx_path_map(win, host, HOST_MAX)) {
        errno = ENOENT;
        return 0;
    }
    return 1;
}

static int set_errno(int neg)
{
    switch (-neg) {
    case OS_ENOENT: errno = ENOENT; break;
    case OS_EACCES: errno = EACCES; break;
    case OS_EEXIST: errno = EEXIST; break;
    case OS_ENOTEMPTY: errno = ENOTEMPTY; break;
    case OS_ENOTDIR: errno = ENOTDIR; break;
    default: errno = EIO; break;
    }
    return -1;
}







int fnvnx_access(const char *path, int mode)
{
    char host[HOST_MAX];
    if (!map(path, host))
        return -1;
    os_stat_t st;
    int r = os_stat(host, &st);
    if (r)
        return set_errno(r);
    if ((mode & 2) && st.read_only) {
        errno = EACCES;
        return -1;
    }
    return 0;
}






