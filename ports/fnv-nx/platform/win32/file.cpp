/* file.cpp - file handles, directories and FindFirstFile over the path mapper (path.cpp).
 * I/O is synchronous; an OVERLAPPED request completes before ReadFile/WriteFile return (its event is set). */
#include "w32.h"

#include <stdio.h>
#include <stdlib.h>

struct w32_file {
    struct w32_obj o;
    int fd;
    DWORD access;
};

static void file_destroy(struct w32_obj *o)
{
    struct w32_file *f = (struct w32_file *)o;
    if (f->fd >= 0)
        os_file_close(f->fd);
}

/* ENOENT on a path: FILE_NOT_FOUND when the parent directory exists, else PATH_NOT_FOUND */
static DWORD not_found(const char *host)
{
    char parent[W32_PATH_MAX];
    snprintf(parent, sizeof parent, "%s", host);
    char *s = strrchr(parent, '/');
    if (s && s != parent)
        *s = 0;
    os_stat_t st;
    return os_stat(parent, &st) == 0 && st.is_dir ? ERROR_FILE_NOT_FOUND : ERROR_PATH_NOT_FOUND;
}

static DWORD fail_os(int r, const char *host)
{
    DWORD e = r == -OS_ENOENT ? not_found(host) : w32_os_error(r);
    SetLastError(e);
    return e;
}

static int map_or_fail(const char *win, char *host)
{
    if (!win) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0;
    }
    DWORD e = w32_map_path(win, host, W32_PATH_MAX);
    if (e) {
        SetLastError(e);
        return 0;
    }
    return 1;
}

extern "C" HANDLE CreateFileA(LPCSTR name, DWORD access, DWORD share, void *sa, DWORD disp, DWORD flags, HANDLE tmpl)
{
    W32_UNUSED(share);
    W32_UNUSED(sa);
    W32_UNUSED(tmpl);
    char host[W32_PATH_MAX];
    if (!map_or_fail(name, host)) {
        if (fnvnx_plat_on && name)   /* the platform lane (platstat.cpp) */
            fnvnx_plat_open_failed(name, NULL, GetLastError(), FNVNX_PLAT_RA, "CreateFileA");
        return INVALID_HANDLE_VALUE;
    }
    access = W32_DW(access);
    int of = 0;
    if (access & (GENERIC_READ | 0x0001 /* FILE_READ_DATA */ | 0x10000000 /* GENERIC_ALL */))
        of |= OS_O_READ;
    if (access & (GENERIC_WRITE | 0x0002 /* FILE_WRITE_DATA */ | 0x0004 /* APPEND */ | 0x10000000))
        of |= OS_O_WRITE;
    os_stat_t st;
    int exists = os_stat(host, &st) == 0;
    if (exists && st.is_dir) {
        if (!(W32_DW(flags) & FILE_FLAG_BACKUP_SEMANTICS) || (of & OS_O_WRITE)) {
            SetLastError(ERROR_ACCESS_DENIED);
            return INVALID_HANDLE_VALUE;
        }
        of = OS_O_READ; /* directory handle: only good for attribute queries */
    }
    switch (W32_DW(disp)) {
    case CREATE_NEW: of |= OS_O_CREATE | OS_O_EXCL; break;
    case CREATE_ALWAYS: of |= OS_O_CREATE | OS_O_TRUNC; break;
    case OPEN_EXISTING: break;
    case OPEN_ALWAYS: of |= OS_O_CREATE; break;
    case TRUNCATE_EXISTING: of |= OS_O_TRUNC; break;
    default: SetLastError(ERROR_INVALID_PARAMETER); return INVALID_HANDLE_VALUE;
    }
    if ((of & (OS_O_CREATE | OS_O_TRUNC)) && !(of & OS_O_WRITE)) {
        if (W32_DW(disp) == TRUNCATE_EXISTING) {
            SetLastError(ERROR_INVALID_PARAMETER);
            return INVALID_HANDLE_VALUE;
        }
        of |= OS_O_WRITE; /* creating needs a writable open; the handle still only reads */
    }
    if (!(of & (OS_O_READ | OS_O_WRITE)))
        of |= OS_O_READ; /* access 0: attribute-only handle */
    int fd = os_file_open(host, of);
    if (fd < 0) {
        fail_os(fd, host);
        if (fd == -OS_EEXIST)
            SetLastError(ERROR_FILE_EXISTS);
        else if (fnvnx_plat_on)   /* CREATE_NEW on an existing file is an answer, not a failed open */
            fnvnx_plat_open_failed(name, host, GetLastError(), FNVNX_PLAT_RA, "CreateFileA");
        return INVALID_HANDLE_VALUE;
    }
    struct w32_file *f = (struct w32_file *)w32_obj_new(W32_FILE, sizeof *f, file_destroy);
    if (!f) {
        os_file_close(fd);
        return INVALID_HANDLE_VALUE;
    }
    f->fd = fd;
    f->access = access;
    HANDLE h = w32_handle_open(&f->o);
    DWORD d = W32_DW(disp);
    if (h)
        SetLastError(exists && (d == CREATE_ALWAYS || d == OPEN_ALWAYS) ? ERROR_ALREADY_EXISTS : ERROR_SUCCESS);
    return h ? h : INVALID_HANDLE_VALUE;
}

static struct w32_file *file_of(HANDLE h) { return (struct w32_file *)w32_obj_get(h, W32_FILE); }

static uint64_t ov_offset(LPOVERLAPPED ov) { return ((uint64_t)W32_DW(ov->OffsetHigh) << 32) | W32_DW(ov->Offset); }

static void ov_done(LPOVERLAPPED ov, int64_t n)
{
    ov->Internal = 0;
    ov->InternalHigh = (ULONG_PTR)n;
    if (ov->hEvent)
        SetEvent(ov->hEvent);
}

extern "C" BOOL ReadFile(HANDLE h, LPVOID buf, DWORD n, LPDWORD got, LPOVERLAPPED ov)
{
    int std = w32_std_io ? w32_std_io(h, 0, buf, n, got) : -1;   /* GetStdHandle's pseudo handles (crt_kernel32.cpp) */
    if (std >= 0)
        return std;
    struct w32_file *f = file_of(h);
    if (got)
        *got = 0;
    if (!f)
        return FALSE;
    int64_t r = ov ? os_file_pread(f->fd, buf, W32_DW(n), ov_offset(ov)) : os_file_read(f->fd, buf, W32_DW(n));
    if (r < 0) {
        SetLastError(w32_os_error((int)r));
        return FALSE;
    }
    if (got)
        *got = (DWORD)r;
    if (ov) {
        ov_done(ov, r);
        if (r == 0 && n) {
            SetLastError(ERROR_HANDLE_EOF);
            return FALSE;
        }
    }
    return TRUE;
}

extern "C" BOOL WriteFile(HANDLE h, LPCVOID buf, DWORD n, LPDWORD put, LPOVERLAPPED ov)
{
    int std = w32_std_io ? w32_std_io(h, 1, (void *)buf, n, put) : -1;
    if (std >= 0)
        return std;
    struct w32_file *f = file_of(h);
    if (put)
        *put = 0;
    if (!f)
        return FALSE;
    int64_t done = 0, want = (int64_t)W32_DW(n);
    while (done < want) {
        int64_t r = ov ? os_file_pwrite(f->fd, (const char *)buf + done, (size_t)(want - done), ov_offset(ov) + (uint64_t)done)
                       : os_file_write(f->fd, (const char *)buf + done, (size_t)(want - done));
        if (r <= 0) {
            SetLastError(r < 0 ? w32_os_error((int)r) : 112 /* ERROR_DISK_FULL */);
            if (put)
                *put = (DWORD)done;
            return FALSE;
        }
        done += r;
    }
    if (put)
        *put = (DWORD)done;
    if (ov)
        ov_done(ov, done);
    return TRUE;
}

extern "C" BOOL SetFilePointerEx(HANDLE h, LARGE_INTEGER dist, PLARGE_INTEGER newpos, DWORD method)
{
    struct w32_file *f = file_of(h);
    if (!f)
        return FALSE;
    int w = method == FILE_CURRENT ? OS_SEEK_CUR : method == FILE_END ? OS_SEEK_END : OS_SEEK_SET;
    if (method > FILE_END) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    int64_t cur = os_file_seek(f->fd, 0, OS_SEEK_CUR);
    int64_t base = w == OS_SEEK_SET ? 0 : w == OS_SEEK_CUR ? cur : os_file_seek(f->fd, 0, OS_SEEK_END);
    if (w == OS_SEEK_END)
        os_file_seek(f->fd, cur, OS_SEEK_SET);
    int64_t target = base + (int64_t)dist.QuadPart;
    if (target < 0) {
        SetLastError(131); /* ERROR_NEGATIVE_SEEK */
        return FALSE;
    }
    int64_t r = os_file_seek(f->fd, target, OS_SEEK_SET);
    if (r < 0) {
        SetLastError(w32_os_error((int)r));
        return FALSE;
    }
    if (newpos)
        newpos->QuadPart = r;
    return TRUE;
}

extern "C" DWORD SetFilePointer(HANDLE h, LONG lo, PLONG hi, DWORD method)
{
    LARGE_INTEGER d, np;
    /* without hi, the distance is a signed 32-bit value */
    d.QuadPart = hi ? (LONGLONG)(((uint64_t)(uint32_t)*hi << 32) | (uint32_t)lo) : (LONGLONG)(int32_t)lo;
    if (!SetFilePointerEx(h, d, &np, method))
        return INVALID_SET_FILE_POINTER;
    if (hi)
        *hi = (LONG)(int32_t)(np.QuadPart >> 32);
    else if (np.QuadPart > 0xFFFFFFFFll) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return INVALID_SET_FILE_POINTER;
    }
    SetLastError(ERROR_SUCCESS); /* callers tell a real 0xFFFFFFFF position from failure by this */
    return (DWORD)(np.QuadPart & 0xFFFFFFFFu);
}

extern "C" BOOL GetFileSizeEx(HANDLE h, PLARGE_INTEGER size)
{
    struct w32_file *f = file_of(h);
    os_stat_t st;
    if (!f)
        return FALSE;
    int r = os_file_fstat(f->fd, &st);
    if (r) {
        SetLastError(w32_os_error(r));
        return FALSE;
    }
    size->QuadPart = (LONGLONG)st.size;
    return TRUE;
}

extern "C" DWORD GetFileSize(HANDLE h, LPDWORD hi)
{
    LARGE_INTEGER s;
    if (!GetFileSizeEx(h, &s))
        return INVALID_FILE_SIZE;
    if (hi)
        *hi = (DWORD)((uint64_t)s.QuadPart >> 32);
    SetLastError(ERROR_SUCCESS);
    return (DWORD)((uint64_t)s.QuadPart & 0xFFFFFFFFu);
}

extern "C" BOOL FlushFileBuffers(HANDLE h)
{
    struct w32_file *f = file_of(h);
    return f && os_file_sync(f->fd) == 0;
}

extern "C" BOOL SetEndOfFile(HANDLE h)
{
    struct w32_file *f = file_of(h);
    if (!f)
        return FALSE;
    int64_t pos = os_file_seek(f->fd, 0, OS_SEEK_CUR);
    int r = pos < 0 ? (int)pos : os_file_truncate(f->fd, (uint64_t)pos);
    if (r) {
        SetLastError(w32_os_error(r));
        return FALSE;
    }
    return TRUE;
}

extern "C" BOOL GetFileTime(HANDLE h, LPFILETIME c, LPFILETIME a, LPFILETIME w)
{
    struct w32_file *f = file_of(h);
    os_stat_t st;
    if (!f || os_file_fstat(f->fd, &st))
        return FALSE;
    if (c) w32_set_ft(c, w32_unix_us_to_filetime(st.ctime_us));
    if (a) w32_set_ft(a, w32_unix_us_to_filetime(st.atime_us));
    if (w) w32_set_ft(w, w32_unix_us_to_filetime(st.mtime_us));
    return TRUE;
}

extern "C" BOOL DeleteFileA(LPCSTR name)
{
    char host[W32_PATH_MAX];
    if (!map_or_fail(name, host))
        return FALSE;
    os_stat_t st;
    if (os_stat(host, &st) == 0 && st.is_dir) {
        SetLastError(ERROR_ACCESS_DENIED);
        return FALSE;
    }
    int r = os_remove(host);
    if (r) {
        fail_os(r, host);
        return FALSE;
    }
    return TRUE;
}

extern "C" BOOL CreateDirectoryA(LPCSTR name, void *sa)
{
    W32_UNUSED(sa);
    char host[W32_PATH_MAX];
    if (!map_or_fail(name, host))
        return FALSE;
    int r = os_mkdir(host);
    if (r) {
        if (r == -OS_EEXIST)
            SetLastError(ERROR_ALREADY_EXISTS);
        else
            fail_os(r, host);
        if (r == -OS_ENOENT)
            SetLastError(ERROR_PATH_NOT_FOUND);
        return FALSE;
    }
    return TRUE;
}

extern "C" BOOL RemoveDirectoryA(LPCSTR name)
{
    char host[W32_PATH_MAX];
    if (!map_or_fail(name, host))
        return FALSE;
    int r = os_rmdir(host);
    if (r) {
        fail_os(r, host);
        return FALSE;
    }
    return TRUE;
}

extern "C" BOOL MoveFileA(LPCSTR from, LPCSTR to)
{
    char a[W32_PATH_MAX], b[W32_PATH_MAX];
    if (!map_or_fail(from, a) || !map_or_fail(to, b))
        return FALSE;
    os_stat_t st;
    if (os_stat(a, &st)) {
        SetLastError(not_found(a));
        return FALSE;
    }
    /* MoveFile never replaces; on a case-folding host a pure case change maps to the same file */
    if (os_stat(b, &st) == 0 && strcmp(a, b)) {
        SetLastError(ERROR_ALREADY_EXISTS);
        return FALSE;
    }
    int r = os_rename(a, b);
    if (r) {
        fail_os(r, b);
        return FALSE;
    }
    return TRUE;
}

extern "C" BOOL CopyFileA(LPCSTR from, LPCSTR to, BOOL fail_if_exists)
{
    char a[W32_PATH_MAX], b[W32_PATH_MAX];
    if (!map_or_fail(from, a) || !map_or_fail(to, b))
        return FALSE;
    int in = os_file_open(a, OS_O_READ);
    if (in < 0) {
        fail_os(in, a);
        return FALSE;
    }
    int out = os_file_open(b, OS_O_WRITE | OS_O_CREATE | (fail_if_exists ? OS_O_EXCL : OS_O_TRUNC));
    if (out < 0) {
        os_file_close(in);
        fail_os(out, b);
        if (out == -OS_EEXIST)
            SetLastError(ERROR_FILE_EXISTS);
        return FALSE;
    }
    char buf[64 * 1024];
    int64_t r;
    BOOL ok = TRUE;
    while ((r = os_file_read(in, buf, sizeof buf)) > 0)
        if (os_file_write(out, buf, (size_t)r) != r) {
            ok = FALSE;
            SetLastError(112);
            break;
        }
    if (r < 0) {
        ok = FALSE;
        SetLastError(w32_os_error((int)r));
    }
    os_file_close(in);
    os_file_close(out);
    return ok;
}

static DWORD attrs_of(const os_stat_t *st)
{
    DWORD a = st->is_dir ? FILE_ATTRIBUTE_DIRECTORY : FILE_ATTRIBUTE_ARCHIVE;
    if (st->read_only)
        a |= FILE_ATTRIBUTE_READONLY;
    return a;
}

extern "C" DWORD GetFileAttributesA(LPCSTR name)
{
    char host[W32_PATH_MAX];
    if (!map_or_fail(name, host))
        return INVALID_FILE_ATTRIBUTES;
    os_stat_t st;
    int r = os_stat(host, &st);
    if (r) {
        fail_os(r, host);
        return INVALID_FILE_ATTRIBUTES;
    }
    return attrs_of(&st);
}

/* ---- FindFirstFile: the directory is read once, filtered and sorted like NTFS (case-insensitive) */
struct w32_find {
    struct w32_obj o;
    char dir[W32_PATH_MAX];
    char **names;
    size_t n, next;
};

static void find_destroy(struct w32_obj *o)
{
    struct w32_find *f = (struct w32_find *)o;
    for (size_t i = 0; i < f->n; i++)
        free(f->names[i]);
    free(f->names);
}

static int cmp_names(const void *a, const void *b)
{
    const char *x = *(const char *const *)a, *y = *(const char *const *)b;
    /* "." and ".." first, as NTFS returns them */
    int dx = !strcmp(x, ".") ? 0 : !strcmp(x, "..") ? 1 : 2, dy = !strcmp(y, ".") ? 0 : !strcmp(y, "..") ? 1 : 2;
    if (dx != dy)
        return dx - dy;
    int c = w32_stricmp(x, y);
    return c ? c : strcmp(x, y);
}

static int find_add(struct w32_find *f, const char *name)
{
    if ((f->n & 31) == 0) {
        char **nn = (char **)realloc(f->names, (f->n + 32) * sizeof(char *));
        if (!nn)
            return 0;
        f->names = nn;
    }
    f->names[f->n] = strdup(name);
    return f->names[f->n++] != NULL;
}

static BOOL find_fill(struct w32_find *f, LPWIN32_FIND_DATAA fd)
{
    while (f->next < f->n) {
        const char *name = f->names[f->next++];
        char p[W32_PATH_MAX * 2];
        snprintf(p, sizeof p, "%s/%s", f->dir, name);
        os_stat_t st;
        if (!strcmp(name, ".") || !strcmp(name, ".."))
            snprintf(p, sizeof p, "%s", f->dir);
        if (os_stat(p, &st))
            continue; /* vanished since the listing */
        memset(fd, 0, sizeof *fd);
        fd->dwFileAttributes = attrs_of(&st);
        w32_set_ft(&fd->ftCreationTime, w32_unix_us_to_filetime(st.ctime_us));
        w32_set_ft(&fd->ftLastAccessTime, w32_unix_us_to_filetime(st.atime_us));
        w32_set_ft(&fd->ftLastWriteTime, w32_unix_us_to_filetime(st.mtime_us));
        fd->nFileSizeHigh = st.is_dir ? 0 : (DWORD)(st.size >> 32);
        fd->nFileSizeLow = st.is_dir ? 0 : (DWORD)(st.size & 0xFFFFFFFFu);
        snprintf(fd->cFileName, sizeof fd->cFileName, "%s", name);
        return TRUE;
    }
    SetLastError(ERROR_NO_MORE_FILES);
    return FALSE;
}

extern "C" HANDLE FindFirstFileA(LPCSTR spec, LPWIN32_FIND_DATAA fd)
{
    char norm[W32_PATH_MAX];
    DWORD e = spec ? w32_full_path(spec, norm, sizeof norm) : ERROR_INVALID_PARAMETER;
    if (e) {
        SetLastError(e);
        return INVALID_HANDLE_VALUE;
    }
    size_t sl = strlen(spec);
    if (sl && (spec[sl - 1] == '\\' || spec[sl - 1] == '/')) {
        SetLastError(ERROR_FILE_NOT_FOUND); /* "dir\" is not a valid search */
        return INVALID_HANDLE_VALUE;
    }
    char *slash = strrchr(norm, '\\');
    char pattern[MAX_PATH + 1];
    snprintf(pattern, sizeof pattern, "%s", slash + 1);
    int at_root = slash == norm + 2;
    slash[at_root ? 1 : 0] = 0;
    if (!*pattern) {
        SetLastError(ERROR_FILE_NOT_FOUND);
        return INVALID_HANDLE_VALUE;
    }
    struct w32_find *f = (struct w32_find *)w32_obj_new(W32_FIND, sizeof *f, find_destroy);
    if (!f)
        return INVALID_HANDLE_VALUE;
    e = w32_map_path(norm, f->dir, sizeof f->dir);
    os_dir *d = e ? NULL : os_dir_open(f->dir);
    if (!d) {
        w32_obj_unref(&f->o);
        SetLastError(e ? e : ERROR_PATH_NOT_FOUND);
        return INVALID_HANDLE_VALUE;
    }
    if (!at_root) {
        if (w32_wildcard_match(pattern, "."))
            find_add(f, ".");
        if (w32_wildcard_match(pattern, ".."))
            find_add(f, "..");
    }
    const char *name;
    while ((name = os_dir_next(d)))
        if (w32_wildcard_match(pattern, name))
            find_add(f, name);
    os_dir_close(d);
    if (f->n)
        qsort(f->names, f->n, sizeof(char *), cmp_names);
    if (!find_fill(f, fd)) {
        w32_obj_unref(&f->o);
        SetLastError(ERROR_FILE_NOT_FOUND);
        return INVALID_HANDLE_VALUE;
    }
    HANDLE h = w32_handle_open(&f->o);
    return h ? h : INVALID_HANDLE_VALUE;
}

extern "C" BOOL FindNextFileA(HANDLE h, LPWIN32_FIND_DATAA fd)
{
    struct w32_find *f = (struct w32_find *)w32_obj_get(h, W32_FIND);
    return f ? find_fill(f, fd) : FALSE;
}

extern "C" BOOL FindClose(HANDLE h)
{
    return w32_handle_close(h, W32_FIND);
}
