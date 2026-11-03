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

#include "../os/os.h"

/* from win32 (C++ side; fnvnx_win32.h is C++-only, so the prototypes are repeated here) */
unsigned long fnvnx_path_map(const char *win, char *host, size_t n);
unsigned long GetCurrentDirectoryA(unsigned long n, char *buf);
int SetCurrentDirectoryA(const char *dir);

#define HOST_MAX 1024

_Static_assert(sizeof(struct fnvnx_msvc_stat) == 48, "struct _stat64i32 of MSVC 2008 is 48 bytes");

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

/* MSVC mode strings: r w a, '+', b/t, plus extensions (c n S R T D N, ",ccs=...") the target fopen rejects */
static void clean_mode(const char *in, char *out)
{
    size_t o = 0;
    for (const char *p = in; *p && *p != ',' && o < 6; p++)
        if (strchr("rwa+b", *p))
            out[o++] = *p;
    if (!memchr(out, 'b', o) && o < 7)
        out[o++] = 'b';
    out[o] = 0;
}

FILE *fnvnx_fopen(const char *path, const char *mode)
{
    char host[HOST_MAX], m[8];
    if (!map(path, host) || !mode)
        return NULL;
    clean_mode(mode, m);
    return fopen(host, m);
}

errno_t fnvnx_fopen_s(FILE **f, const char *path, const char *mode)
{
    if (!f)
        return FNVNX_EINVAL;
    *f = fnvnx_fopen(path, mode);
    return *f ? 0 : errno;
}

FILE *fnvnx_fsopen(const char *path, const char *mode, int share)
{
    (void)share;
    return fnvnx_fopen(path, mode);
}

int fnvnx_remove(const char *path)
{
    char host[HOST_MAX];
    if (!map(path, host))
        return -1;
    int r = os_remove(host);
    return r ? set_errno(r) : 0;
}

int fnvnx_unlink(const char *path) { return fnvnx_remove(path); }

int fnvnx_rename(const char *from, const char *to)
{
    char a[HOST_MAX], b[HOST_MAX];
    if (!map(from, a) || !map(to, b))
        return -1;
    os_stat_t st;
    if (os_stat(b, &st) == 0 && strcmp(a, b)) { /* MSVC rename does not replace */
        errno = EACCES;
        return -1;
    }
    int r = os_rename(a, b);
    return r ? set_errno(r) : 0;
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

errno_t fnvnx_access_s(const char *path, int mode) { return fnvnx_access(path, mode) ? errno : 0; }

int fnvnx_mkdir(const char *path)
{
    char host[HOST_MAX];
    if (!map(path, host))
        return -1;
    int r = os_mkdir(host);
    return r ? set_errno(r) : 0;
}

int fnvnx_rmdir(const char *path)
{
    char host[HOST_MAX];
    if (!map(path, host))
        return -1;
    int r = os_rmdir(host);
    return r ? set_errno(r) : 0;
}

char *fnvnx_getcwd(char *buf, int n)
{
    char tmp[HOST_MAX];
    unsigned long l = GetCurrentDirectoryA(sizeof tmp, tmp);
    if (!buf) {
        size_t sz = n > (int)l ? (size_t)n : l + 1;
        buf = (char *)malloc(sz);
        if (!buf)
            return NULL;
        n = (int)sz;
    }
    if ((unsigned long)n <= l) {
        errno = ERANGE;
        return NULL;
    }
    memcpy(buf, tmp, l + 1);
    return buf;
}

int fnvnx_chdir(const char *dir)
{
    if (!SetCurrentDirectoryA(dir)) {
        errno = ENOENT;
        return -1;
    }
    return 0;
}

int _fseeki64(FILE *f, long long off, int whence) { return fseeko(f, (off_t)off, whence); }
long long _ftelli64(FILE *f) { return (long long)ftello(f); }
int fnvnx_fileno(FILE *f) { return fileno(f); }

int fnvnx_stat(const char *path, struct fnvnx_msvc_stat *out)
{
    char host[HOST_MAX];
    if (!map(path, host))
        return -1;
    /* MSVC _stat rejects a trailing separator except on a root ("C:\") */
    size_t l = strlen(path);
    if (l > 3 && (path[l - 1] == '\\' || path[l - 1] == '/')) {
        errno = ENOENT;
        return -1;
    }
    os_stat_t st;
    int r = os_stat(host, &st);
    if (r)
        return set_errno(r);
    memset(out, 0, sizeof *out);
    out->st_dev = out->st_rdev = 2; /* drive C: */
    out->st_mode = (uint16_t)(st.is_dir ? 0040000 | 0111 : 0100000);
    out->st_mode |= (uint16_t)(st.read_only ? 0444 : 0666);
    out->st_nlink = 1;
    out->st_size = (int32_t)(st.size > 0x7FFFFFFF ? 0x7FFFFFFF : st.size);
    out->st_atime_ = st.atime_us / 1000000;
    out->st_mtime_ = st.mtime_us / 1000000;
    out->st_ctime_ = st.ctime_us / 1000000;
    return 0;
}
