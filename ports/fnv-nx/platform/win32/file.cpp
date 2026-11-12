/* file.cpp - file handles, directories and FindFirstFile over the path mapper (path.cpp).
 * I/O is synchronous; an OVERLAPPED request completes before ReadFile/WriteFile return (its event is set). */
#include "w32.h"

#include <stdio.h>
#include <stdlib.h>

#ifndef DELETE
#define DELETE 0x00010000L
#endif
#ifndef FILE_SHARE_DELETE
#define FILE_SHARE_DELETE 0x00000004
#endif
#ifndef FILE_FLAG_DELETE_ON_CLOSE
#define FILE_FLAG_DELETE_ON_CLOSE 0x04000000
#endif
#ifndef ERROR_BAD_NETPATH
#define ERROR_BAD_NETPATH 53L
#endif
#ifndef FILE_FLAG_NO_BUFFERING
#define FILE_FLAG_NO_BUFFERING 0x20000000
#endif
#ifndef FILE_FLAG_WRITE_THROUGH
#define FILE_FLAG_WRITE_THROUGH 0x80000000u
#endif
#ifndef ERROR_INVALID_FUNCTION
#define ERROR_INVALID_FUNCTION 1L
#endif
#define W32_SECTOR 512   /* logical sector size of the C: volume (what an emulated 512-byte-sector disk reports) */
#ifndef ERROR_FILE_EXISTS
#define ERROR_FILE_EXISTS 80L
#endif
#ifndef ERROR_ALREADY_EXISTS
#define ERROR_ALREADY_EXISTS 183L
#endif

/* Sharing: every open of a file is a share_ent in one table keyed by the canonical (host-resolved, case-folded)
 * path. An open of (rights R/W/D, share S) is granted iff for every entry e: rights(new) is within S(e) and
 * rights(e) is within S(new). Rule and proof: this is exactly the NTFS share-access check, and it is evaluated
 * under one lock together with the open that follows it, so no conflicting pair can coexist. Checks that act
 * through a temporary open (DeleteFile, MoveFile: DELETE with all shares; CopyFile: READ share R, WRITE share 0)
 * use the same predicate. The READONLY attribute lives in a second table of keys (the host has no attribute store
 * on every backend); entries are dropped when the name is deleted or re-created. */
#define SR_R 1
#define SR_W 2
#define SR_D 4
struct share_ent {
    struct share_ent *next, **pprev;
    char host[W32_PATH_MAX];      /* host path of the file (follows a rename) */
    char key[W32_PATH_MAX];       /* "" once the open no longer names a file (deleted or replaced) */
    unsigned rights, share;       /* SR_*, FILE_SHARE_* (same bit order) */
    int doc;                      /* delete when the last open of the key closes */
};
struct ro_ent { struct ro_ent *next; char key[W32_PATH_MAX]; };
static os_lock_t g_flock;
static struct share_ent *g_opens;
static struct ro_ent *g_ro;

struct w32_file {
    struct w32_obj o;
    int fd;         /* -1: a directory handle the host could not open (attribute queries only) */
    DWORD access;
    int async, append, is_dir, linked;
    int dirty;      /* written to: CloseHandle stamps the write time (fnvnx_clock.h) */
    int dev;        /* 1: the NUL device (no descriptor, no share entry) */
    int nobuf, wt;  /* FILE_FLAG_NO_BUFFERING (sector rules) / FILE_FLAG_WRITE_THROUGH */
    struct share_ent se;
};

#define ACC_READ (GENERIC_READ | 0x0001 /* FILE_READ_DATA */ | 0x10000000 /* GENERIC_ALL */)
#define ACC_WDATA (GENERIC_WRITE | 0x0002 /* FILE_WRITE_DATA */ | 0x10000000)
#define ACC_WRITE (ACC_WDATA | 0x0004 /* FILE_APPEND_DATA */)

static void make_key(const char *host, char *key)
{
    size_t i = 0;
    for (; host[i] && i < W32_PATH_MAX - 1; i++)
        key[i] = (char)(host[i] >= 'A' && host[i] <= 'Z' ? host[i] + 32 : host[i]);
    key[i] = 0;
}
static unsigned rights_of(DWORD access)
{
    return ((access & ACC_READ) ? SR_R : 0) | ((access & ACC_WRITE) ? SR_W : 0) |
           ((access & (DELETE | 0x10000000)) ? SR_D : 0);
}
/* callers hold g_flock */
static int share_conflict(const char *key, unsigned rights, unsigned share)
{
    if (!rights) /* an open with no data/delete access (attribute queries) is outside share arbitration, as on NTFS */
        return 0;
    for (struct share_ent *e = g_opens; e; e = e->next)
        if (e->rights && e->key[0] && !strcmp(e->key, key) && ((rights & ~e->share) || (e->rights & ~share)))
            return 1;
    return 0;
}
static void ent_link(struct share_ent *e) { e->pprev = &g_opens; e->next = g_opens; if (g_opens) g_opens->pprev = &e->next; g_opens = e; }
static void ent_unlink(struct share_ent *e) { *e->pprev = e->next; if (e->next) e->next->pprev = e->pprev; }
static void ent_rekey(const char *from, const char *to_host, const char *to_key)
{
    for (struct share_ent *e = g_opens; e; e = e->next)
        if (!strcmp(e->key, from)) {
            snprintf(e->host, sizeof e->host, "%s", to_host);
            snprintf(e->key, sizeof e->key, "%s", to_key);
        }
}
static void ent_retire(const char *key) { for (struct share_ent *e = g_opens; e; e = e->next) if (!strcmp(e->key, key)) e->key[0] = 0; }
static int ro_has(const char *key) { for (struct ro_ent *r = g_ro; r; r = r->next) if (!strcmp(r->key, key)) return 1; return 0; }
static void ro_clear(const char *key)
{
    for (struct ro_ent **pp = &g_ro; *pp; pp = &(*pp)->next)
        if (!strcmp((*pp)->key, key)) { struct ro_ent *r = *pp; *pp = r->next; free(r); return; }
}
static void ro_set(const char *key)
{
    if (ro_has(key))
        return;
    struct ro_ent *r = (struct ro_ent *)malloc(sizeof *r);
    if (!r)
        return;
    snprintf(r->key, sizeof r->key, "%s", key);
    r->next = g_ro;
    g_ro = r;
}
/* attributes of a host path (callers hold g_flock) */
static DWORD attrs_locked(const char *host, const os_stat_t *st)
{
    char key[W32_PATH_MAX];
    make_key(host, key);
    DWORD a = st->is_dir ? FILE_ATTRIBUTE_DIRECTORY : FILE_ATTRIBUTE_ARCHIVE;
    if (st->read_only || (!st->is_dir && ro_has(key)))
        a |= FILE_ATTRIBUTE_READONLY;
    return a;
}

static void file_destroy(struct w32_obj *o)
{
    struct w32_file *f = (struct w32_file *)o;
    if (f->fd >= 0)
        os_file_close(f->fd);
    if (f->dirty)
        clk_file_stamp(f->se.host, CLK_FS_WRITE);
    if (!f->linked)
        return;
    os_lock(&g_flock);
    ent_unlink(&f->se);
    if (f->se.doc && f->se.key[0]) { /* the last open of the file removes it; an earlier one passes the duty on */
        struct share_ent *other = NULL;
        for (struct share_ent *e = g_opens; e && !other; e = e->next)
            if (!strcmp(e->key, f->se.key))
                other = e;
        if (other)
            other->doc = 1;
        else {
            if (f->is_dir)
                os_rmdir(f->se.host);
            else
                os_remove(f->se.host);
            clk_file_removed(f->se.host);
            ro_clear(f->se.key);
        }
    }
    os_unlock(&g_flock);
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

/* Names inside the layer are UTF-8 on the host side. A names arrive in the ANSI code page and are converted when
 * they hold non-ASCII bytes; W names are converted by CreateFileW and enter through w32_create_file_utf8, so
 * one file has one internal name whichever API opens it. */
static int utf16_to_utf8(const WCHAR *w, char *out, size_t n)
{
    size_t o = 0;
    for (; *w; w++) {
        uint32_t c = (uint16_t)*w;
        if (c >= 0xD800 && c < 0xDC00 && (uint16_t)w[1] >= 0xDC00 && (uint16_t)w[1] < 0xE000) {
            c = 0x10000 + ((c - 0xD800) << 10) + ((uint16_t)*++w - 0xDC00);
        } else if (c >= 0xD800 && c < 0xE000)
            return -1; /* unpaired surrogate */
        int k = c < 0x80 ? 1 : c < 0x800 ? 2 : c < 0x10000 ? 3 : 4;
        if (o + (size_t)k + 1 > n)
            return -2;
        if (k == 1)
            out[o++] = (char)c;
        else {
            out[o++] = (char)((k == 2 ? 0xC0 : k == 3 ? 0xE0 : 0xF0) | (c >> (6 * (k - 1))));
            for (int i = k - 2; i >= 0; i--)
                out[o++] = (char)(0x80 | ((c >> (6 * i)) & 0x3F));
        }
    }
    out[o] = 0;
    return 0;
}
static const char *ansi_to_utf8(const char *a, char *tmp, size_t n)
{
    const unsigned char *p = (const unsigned char *)a;
    while (*p && *p < 0x80)
        p++;
    if (!*p)
        return a; /* ASCII: identical */
    WCHAR w[W32_PATH_MAX];
    int k = MultiByteToWideChar(CP_ACP, 0, a, -1, w, W32_PATH_MAX);
    return k > 0 && utf16_to_utf8(w, tmp, n) == 0 ? tmp : NULL;
}

static int map_or_fail(const char *win, char *host, int utf8 = 0)
{
    char conv[W32_PATH_MAX * 2];
    if (win && !utf8) {
        win = ansi_to_utf8(win, conv, sizeof conv);
        if (!win) {
            SetLastError(ERROR_INVALID_NAME);
            return 0;
        }
    }
    if (!win) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0;
    }
    const char *chk = win;
    if ((win[0] == '\\' || win[0] == '/') && (win[1] == '\\' || win[1] == '/') && (win[2] == '?' || win[2] == '.') &&
        (win[3] == '\\' || win[3] == '/'))
        chk = win + 4; /* the "\\?\" and "\\.\" prefixes are not wildcards */
    if (strpbrk(chk, "*?\"<>|")) { /* wildcards and reserved characters are not file names */
        SetLastError(ERROR_INVALID_NAME);
        return 0;
    }
    DWORD e = w32_map_path(win, host, W32_PATH_MAX);
    if (e) {
        SetLastError(e);
        return 0;
    }
    return 1;
}

static HANDLE create_core(LPCSTR name, DWORD access, DWORD share, DWORD disp, DWORD flags, char *host, int *rep, int utf8)
{
    if (!name) {
        SetLastError(ERROR_PATH_NOT_FOUND);
        return INVALID_HANDLE_VALUE;
    }
    access = W32_DW(access);
    share = W32_DW(share) & 7;
    disp = W32_DW(disp);
    flags = W32_DW(flags);
    /* a DOS device name or device-namespace name (path.cpp: the Windows 7 rules) is not a file */
    char dev[8];
    int nc = w32_name_class(name, dev, sizeof dev);
    if (nc != W32_NC_FILE && (disp < CREATE_NEW || disp > TRUNCATE_EXISTING)) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return INVALID_HANDLE_VALUE;
    }
    if (nc == W32_NC_UNC) { /* ENV N1: no network interface: the server cannot be reached */
        SetLastError(ERROR_BAD_NETPATH);
        return INVALID_HANDLE_VALUE;
    }
    if (nc == W32_NC_CONSOLE) { /* ENV F5: the process has no console */
        SetLastError(ERROR_INVALID_HANDLE);
        return INVALID_HANDLE_VALUE;
    }
    if (nc == W32_NC_PORT || nc == W32_NC_PIPE || nc == W32_NC_NONE) { /* no serial/parallel port, no pipe, no such object */
        SetLastError(ERROR_FILE_NOT_FOUND);
        return INVALID_HANDLE_VALUE;
    }
    if (nc == W32_NC_NUL) {
        struct w32_file *f = (struct w32_file *)w32_obj_new(W32_FILE, sizeof *f, file_destroy);
        if (!f)
            return INVALID_HANDLE_VALUE;
        f->fd = -1;
        f->dev = 1;
        f->access = access;
        f->async = (flags & FILE_FLAG_OVERLAPPED) != 0;
        HANDLE h = w32_handle_open(&f->o);
        if (!h) {
            w32_obj_unref(&f->o);
            return INVALID_HANDLE_VALUE;
        }
        SetLastError(ERROR_SUCCESS);
        return h;
    }
    if (!map_or_fail(name, host, utf8)) {
        *rep = 1;
        return INVALID_HANDLE_VALUE;
    }
    if (disp < CREATE_NEW || disp > TRUNCATE_EXISTING || (disp == TRUNCATE_EXISTING && !(access & ACC_WDATA))) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return INVALID_HANDLE_VALUE;
    }
    size_t nl = strlen(name);
    int trailing_sep = nl && (name[nl - 1] == '\\' || name[nl - 1] == '/');
    int doc = (flags & FILE_FLAG_DELETE_ON_CLOSE) != 0;
    unsigned rights = rights_of(access) | (doc ? SR_D : 0); /* DELETE_ON_CLOSE needs DELETE sharing */
    char key[W32_PATH_MAX];
    make_key(host, key);
    os_stat_t st;
    int exists = os_stat(host, &st) == 0;
    int isdir = exists && st.is_dir;
    if (isdir) {
        if (!(flags & FILE_FLAG_BACKUP_SEMANTICS)) {
            SetLastError(trailing_sep ? ERROR_PATH_NOT_FOUND : ERROR_ACCESS_DENIED); /* "dir\\" is not a file name */
            return INVALID_HANDLE_VALUE;
        }
        if (disp == CREATE_NEW) {
            SetLastError(ERROR_FILE_EXISTS);
            return INVALID_HANDLE_VALUE;
        }
        if (disp == CREATE_ALWAYS || disp == TRUNCATE_EXISTING || (access & ACC_WRITE)) {
            SetLastError(ERROR_ACCESS_DENIED);
            return INVALID_HANDLE_VALUE;
        }
    } else if (exists && disp == CREATE_NEW) {
        SetLastError(ERROR_FILE_EXISTS);
        return INVALID_HANDLE_VALUE;
    }
    if (!exists)
        ro_clear(key); /* a name that does not exist has no attributes left over */
    if (exists && !isdir && (st.read_only || ro_has(key)) &&
        ((access & ACC_WRITE) || disp == CREATE_ALWAYS || disp == TRUNCATE_EXISTING || doc)) {
        SetLastError(ERROR_ACCESS_DENIED);
        return INVALID_HANDLE_VALUE;
    }
    if (share_conflict(key, rights, share)) {
        SetLastError(ERROR_SHARING_VIOLATION);
        return INVALID_HANDLE_VALUE;
    }
    int of = 0;
    if (access & ACC_READ)
        of |= OS_O_READ;
    if (access & ACC_WRITE)
        of |= OS_O_WRITE;
    int append = !isdir && (access & 0x0004 /* FILE_APPEND_DATA */) && !(access & ACC_WDATA);
    if (isdir)
        of = OS_O_READ; /* directory handle: only good for attribute queries */
    else {
        switch (disp) {
        case CREATE_NEW: of |= OS_O_CREATE | OS_O_EXCL; break;
        case CREATE_ALWAYS: of |= OS_O_CREATE | OS_O_TRUNC; break;
        case OPEN_ALWAYS: of |= OS_O_CREATE; break;
        case TRUNCATE_EXISTING: of |= OS_O_TRUNC; break;
        default: break; /* OPEN_EXISTING */
        }
        if ((of & (OS_O_CREATE | OS_O_TRUNC)) && !(of & OS_O_WRITE))
            of |= OS_O_WRITE; /* creating needs a writable open; the handle still only reads */
        if (!(of & (OS_O_READ | OS_O_WRITE)))
            of |= OS_O_READ; /* access 0: attribute-only handle */
        if (append)
            of |= OS_O_APPEND;
    }
    int fd = os_file_open(host, of);
    if (fd < 0 && isdir)
        fd = -1; /* fsdev cannot open() a directory: the handle still serves GetFileAttributes-style queries */
    else if (fd < 0) {
        fail_os(fd, host);
        if (fd == -OS_EEXIST)
            SetLastError(ERROR_FILE_EXISTS);
        else
            *rep = 2;
        return INVALID_HANDLE_VALUE;
    }
    struct w32_file *f = (struct w32_file *)w32_obj_new(W32_FILE, sizeof *f, file_destroy);
    if (!f) {
        if (fd >= 0)
            os_file_close(fd);
        return INVALID_HANDLE_VALUE;
    }
    f->fd = fd;
    f->access = access;
    f->async = (flags & FILE_FLAG_OVERLAPPED) != 0;
    f->nobuf = (flags & FILE_FLAG_NO_BUFFERING) != 0;
    f->wt = (flags & FILE_FLAG_WRITE_THROUGH) != 0;
    f->append = append;
    f->is_dir = isdir;
    snprintf(f->se.host, sizeof f->se.host, "%s", host);
    snprintf(f->se.key, sizeof f->se.key, "%s", key);
    f->se.rights = rights;
    f->se.share = share;
    f->se.doc = doc;
    HANDLE h = w32_handle_open(&f->o);
    if (!h) {
        w32_obj_unref(&f->o); /* not linked: the destroy only closes the descriptor */
        return INVALID_HANDLE_VALUE;
    }
    f->linked = 1;
    ent_link(&f->se);
    if (!isdir && !exists)
        clk_file_stamp(host, CLK_FS_CREATE);                                  /* a new file: created now */
    else if (!isdir && (disp == CREATE_ALWAYS || disp == TRUNCATE_EXISTING))
        clk_file_stamp(host, CLK_FS_WRITE);                                   /* replaced or truncated: written now */
    if (!exists || disp == CREATE_ALWAYS) { /* a created or replaced file takes the attributes of this call */
        ro_clear(key);
        if (flags & FILE_ATTRIBUTE_READONLY)
            ro_set(key);
    }
    SetLastError(exists && (disp == CREATE_ALWAYS || disp == OPEN_ALWAYS) ? ERROR_ALREADY_EXISTS : ERROR_SUCCESS);
    return h;
}

/* the one implementation: name is ANSI (utf8 == 0) or UTF-8 (CreateFileW) */
bool w32_file_writers_possible(HANDLE h)
{
    struct w32_obj *o = w32_obj_get_ref(h, W32_FILE);
    if (!o)
        return true;   /* not a file: nothing is known, so the answer that excludes the exact private copy */
    struct w32_file *f = (struct w32_file *)o;
    os_lock(&g_flock);
    bool w = (f->se.rights & SR_W) || (f->se.share & FILE_SHARE_WRITE);
    os_unlock(&g_flock);
    w32_obj_unref(o);
    return w;
}

HANDLE w32_create_file_name(LPCSTR name, DWORD access, DWORD share, DWORD disp, DWORD flags, int utf8)
{
    char host[W32_PATH_MAX];
    int rep = 0;
    os_lock(&g_flock);
    HANDLE h = create_core(name, access, share, disp, flags, host, &rep, utf8);
    os_unlock(&g_flock);
    if (h == INVALID_HANDLE_VALUE && rep && fnvnx_plat_on)   /* the platform lane (platstat.cpp) */
        fnvnx_plat_open_failed(name, rep == 2 ? host : NULL, GetLastError(), FNVNX_NOTE_RA, "CreateFileA");
    return h;
}

extern "C" HANDLE CreateFileA(LPCSTR name, DWORD access, DWORD share, void *sa, DWORD disp, DWORD flags, HANDLE tmpl)
{
    /* hTemplateFile only supplies extended attributes, which this file system has none of. lpSecurityAttributes'
     * bInheritHandle marks the handle inheritable by a child process: the manifest has no CreateProcess,
     * DuplicateHandle or GetHandleInformation, so the process can observe no difference. */
    W32_UNUSED(sa);
    W32_UNUSED(tmpl);
    return w32_create_file_name(name, access, share, disp, flags, 0);
}

static struct w32_file *file_of(HANDLE h) { return (struct w32_file *)w32_obj_get(h, W32_FILE); }

static uint64_t ov_offset(LPOVERLAPPED ov) { return ((uint64_t)W32_DW(ov->OffsetHigh) << 32) | W32_DW(ov->Offset); }

/* an OVERLAPPED request: pending state while it runs (event reset), then status/length and the event set;
 * this layer performs every request to completion before returning, which the API permits (returns TRUE) */
static HANDLE ov_event(LPOVERLAPPED ov) { return (HANDLE)((uintptr_t)ov->hEvent & ~(uintptr_t)1); }
static void ov_begin(LPOVERLAPPED ov)
{
    ov->Internal = 0x103; /* STATUS_PENDING */
    ov->InternalHigh = 0;
    if (ov_event(ov))
        ResetEvent(ov_event(ov));
}
static void ov_done(LPOVERLAPPED ov, ULONG_PTR status, int64_t n)
{
    ov->Internal = status;
    ov->InternalHigh = (ULONG_PTR)n;
    if (ov_event(ov))
        SetEvent(ov_event(ov));
}

/* common argument rules of ReadFile/WriteFile; returns the file or NULL with the error set */
static struct w32_file *io_file(HANDLE h, LPDWORD count, LPOVERLAPPED ov, DWORD need)
{
    struct w32_file *f = file_of(h);
    if (count)
        *count = 0;
    if (!f)
        return NULL;
    if (f->async && !ov) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return NULL;
    }
    if (!(f->access & need) || (f->fd < 0 && !f->dev)) {
        SetLastError(ERROR_ACCESS_DENIED);
        return NULL;
    }
    return f;
}

/* FILE_FLAG_NO_BUFFERING: the buffer address, the length and the file offset are multiples of the sector size */
static int nobuf_ok(struct w32_file *f, const void *buf, DWORD n, LPOVERLAPPED ov)
{
    if (!f->nobuf || f->dev)
        return 1;
    int64_t pos = ov ? (int64_t)ov_offset(ov) : os_file_seek(f->fd, 0, OS_SEEK_CUR);
    if (((uintptr_t)buf % W32_SECTOR) || (W32_DW(n) % W32_SECTOR) || (pos % W32_SECTOR)) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0;
    }
    return 1;
}

extern "C" BOOL ReadFile(HANDLE h, LPVOID buf, DWORD n, LPDWORD got, LPOVERLAPPED ov)
{
    struct w32_file *f = io_file(h, got, ov, ACC_READ);
    if (!f)
        return FALSE;
    if (!nobuf_ok(f, buf, n, ov))
        return FALSE;
    if (ov)
        ov_begin(ov);
    uint64_t off = ov ? ov_offset(ov) : 0;
    /* ReadFile returns short only at EOF: loop over the partial reads the host may return */
    int64_t r = 0, want = (int64_t)W32_DW(n);
    while (r < want) {
        int64_t k = f->dev ? 0                                  /* NUL: end of file at once */
                    : ov ? os_file_pread(f->fd, (char *)buf + r, (size_t)(want - r), off + (uint64_t)r)
                         : os_file_read(f->fd, (char *)buf + r, (size_t)(want - r));
        if (k < 0) {
            SetLastError(w32_os_error((int)k));
            if (got)
                *got = (DWORD)r;
            if (ov)
                ov_done(ov, 0xC0000001u /* STATUS_UNSUCCESSFUL */, r);
            return FALSE;
        }
        if (k == 0)
            break;
        r += k;
    }
    if (got)
        *got = (DWORD)r;
    if (ov && !f->async && !f->dev)
        os_file_seek(f->fd, (int64_t)(off + (uint64_t)r), OS_SEEK_SET); /* synchronous handle: the pointer moves */
    if (ov) {
        if (r == 0 && want) { /* with an OVERLAPPED the end of file is an error, on any handle */
            ov_done(ov, 0xC0000011u /* STATUS_END_OF_FILE */, 0);
            SetLastError(ERROR_HANDLE_EOF);
            return FALSE;
        }
        ov_done(ov, 0, r);
    }
    return TRUE;
}

extern "C" BOOL WriteFile(HANDLE h, LPCVOID buf, DWORD n, LPDWORD put, LPOVERLAPPED ov)
{
    struct w32_file *f = io_file(h, put, ov, ACC_WRITE);
    if (!f)
        return FALSE;
    if (!nobuf_ok(f, buf, n, ov))
        return FALSE;
    if (ov)
        ov_begin(ov);
    uint64_t off = ov ? ov_offset(ov) : 0;
    int64_t done = 0, want = (int64_t)W32_DW(n);
    while (done < want) {
        /* an append-only handle writes at the end whatever the offset (O_APPEND) */
        int64_t r = f->dev ? want - done                        /* NUL: discarded, reported written */
                    : ov ? os_file_pwrite(f->fd, (const char *)buf + done, (size_t)(want - done), off + (uint64_t)done)
                         : os_file_write(f->fd, (const char *)buf + done, (size_t)(want - done));
        if (r <= 0) {
            SetLastError(r < 0 ? w32_os_error((int)r) : 112 /* ERROR_DISK_FULL */);
            if (put)
                *put = (DWORD)done;
            if (ov)
                ov_done(ov, 0xC0000001u, done);
            return FALSE;
        }
        done += r;
    }
    if (put)
        *put = (DWORD)done;
    if (!f->dev && done > 0) {
        f->dirty = 1;
        clk_file_stamp(f->se.host, CLK_FS_WRITE);
    }
    if (f->wt && !f->dev)
        os_file_sync(f->fd);                                    /* WRITE_THROUGH: on the medium before return */
    if (ov && !f->async && !f->dev) {
        os_stat_t st;
        int64_t end = f->append && os_file_fstat(f->fd, &st) == 0 ? (int64_t)st.size : (int64_t)(off + (uint64_t)done);
        os_file_seek(f->fd, end, OS_SEEK_SET);
    }
    if (ov)
        ov_done(ov, 0, done);
    return TRUE;
}

extern "C" BOOL SetFilePointerEx(HANDLE h, LARGE_INTEGER dist, PLARGE_INTEGER newpos, DWORD method)
{
    struct w32_file *f = file_of(h);
    if (!f)
        return FALSE;
    method = W32_DW(method);
    if (f->dev) { /* the NUL device has no content: its position stays 0 */
        if (method > FILE_END) {
            SetLastError(ERROR_INVALID_PARAMETER);
            return FALSE;
        }
        if (newpos)
            newpos->QuadPart = 0;
        return TRUE;
    }
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
    int r = f->fd < 0 ? 0 : os_file_fstat(f->fd, &st);
    if (f->fd < 0)
        st.size = 0; /* directory handle */
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
    else if ((uint64_t)s.QuadPart >> 32) { /* the size does not fit the one DWORD the caller gave room for */
        SetLastError(ERROR_INVALID_PARAMETER);
        return INVALID_FILE_SIZE;
    }
    SetLastError(ERROR_SUCCESS);
    return (DWORD)((uint64_t)s.QuadPart & 0xFFFFFFFFu);
}

extern "C" BOOL FlushFileBuffers(HANDLE h)
{
    struct w32_file *f = file_of(h);
    if (!f)
        return FALSE;
    if (!(f->access & ACC_WRITE) || (f->fd < 0 && !f->dev)) { /* FlushFileBuffers needs GENERIC_WRITE */
        SetLastError(ERROR_ACCESS_DENIED);
        return FALSE;
    }
    if (f->dev)
        return TRUE; /* nothing is buffered */
    int r = os_file_sync(f->fd);
    if (r) {
        SetLastError(w32_os_error(r));
        return FALSE;
    }
    return TRUE;
}

extern "C" BOOL SetEndOfFile(HANDLE h)
{
    struct w32_file *f = file_of(h);
    if (!f)
        return FALSE;
    if (!(f->access & ACC_WRITE) || (f->fd < 0 && !f->dev)) {
        SetLastError(ERROR_ACCESS_DENIED);
        return FALSE;
    }
    if (f->dev)
        return TRUE;
    int64_t pos = os_file_seek(f->fd, 0, OS_SEEK_CUR);
    int r = pos < 0 ? (int)pos : os_file_truncate(f->fd, (uint64_t)pos);
    if (r) {
        SetLastError(w32_os_error(r));
        return FALSE;
    }
    f->dirty = 1;
    clk_file_stamp(f->se.host, CLK_FS_WRITE);
    return TRUE;
}

extern "C" BOOL GetFileTime(HANDLE h, LPFILETIME c, LPFILETIME a, LPFILETIME w)
{
    struct w32_file *f = file_of(h);
    os_stat_t st;
    if (f && f->dev) {
        SetLastError(ERROR_INVALID_FUNCTION); /* a device has no time stamps */
        return FALSE;
    }
    if (!f || os_file_fstat(f->fd, &st))
        return FALSE;
    int64_t ft[3];
    if (clk_file_times(f->se.host, ft)) {   /* the verification mode's stamps */
        if (c) w32_set_ft(c, (ULONGLONG)ft[0]);
        if (a) w32_set_ft(a, (ULONGLONG)ft[1]);
        if (w) w32_set_ft(w, (ULONGLONG)ft[2]);
        return TRUE;
    }
    if (c) w32_set_ft(c, w32_unix_us_to_filetime(st.ctime_us));
    if (a) w32_set_ft(a, w32_unix_us_to_filetime(st.atime_us));
    if (w) w32_set_ft(w, w32_unix_us_to_filetime(st.mtime_us));
    return TRUE;
}

/* a W name is UTF-16: the layer's internal UTF-8 name, as CreateFileW makes it (one rule for every W file call) */
static BOOL wide_name(LPCWSTR w, char *out, size_t n)
{
    if (!w) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    if (utf16_to_utf8(w, out, n)) {
        SetLastError(ERROR_INVALID_NAME);
        return FALSE;
    }
    return TRUE;
}

static BOOL delete_core(LPCSTR name, int utf8)
{
    char host[W32_PATH_MAX], key[W32_PATH_MAX];
    if (!map_or_fail(name, host, utf8))
        return FALSE;
    make_key(host, key);
    BOOL ok = FALSE;
    os_lock(&g_flock);
    os_stat_t st;
    if (os_stat(host, &st) == 0 && (st.is_dir || st.read_only || ro_has(key))) { /* a directory or a read-only file */
        SetLastError(ERROR_ACCESS_DENIED);
    } else if (share_conflict(key, SR_D, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)) {
        SetLastError(ERROR_SHARING_VIOLATION); /* an open of the file does not share DELETE */
    } else {
        int r = os_remove(host);
        if (r)
            fail_os(r, host);
        else {
            ent_retire(key);
            ro_clear(key);
            clk_file_removed(host);
            ok = TRUE;
        }
    }
    os_unlock(&g_flock);
    return ok;
}

extern "C" BOOL DeleteFileA(LPCSTR name) { return delete_core(name, 0); }

extern "C" BOOL DeleteFileW(LPCWSTR name)
{
    char u[W32_PATH_MAX * 2];
    return wide_name(name, u, sizeof u) && delete_core(u, 1);
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
    clk_file_stamp(host, CLK_FS_CREATE);
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
    clk_file_removed(host);
    return TRUE;
}

static BOOL move_core(LPCSTR from, LPCSTR to, int utf8)
{
    char a[W32_PATH_MAX], b[W32_PATH_MAX], ka[W32_PATH_MAX], kb[W32_PATH_MAX];
    if (!map_or_fail(from, a, utf8) || !map_or_fail(to, b, utf8))
        return FALSE;
    make_key(a, ka);
    make_key(b, kb);
    BOOL ok = FALSE;
    os_lock(&g_flock);
    os_stat_t st;
    if (os_stat(a, &st)) {
        SetLastError(not_found(a));
    } else if (os_stat(b, &st) == 0 && strcmp(a, b)) {
        /* MoveFile never replaces; on a case-folding host a pure case change maps to the same file */
        SetLastError(ERROR_ALREADY_EXISTS);
    } else if (share_conflict(ka, SR_D, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)) {
        SetLastError(ERROR_SHARING_VIOLATION); /* renaming needs DELETE access to the source */
    } else {
        int r = os_rename(a, b);
        if (r)
            fail_os(r, b);
        else {
            int ro = ro_has(ka);
            if (strcmp(ka, kb)) {
                ro_clear(ka);
                ro_clear(kb);
            }
            ent_rekey(ka, b, kb);
            if (ro)
                ro_set(kb);
            clk_file_moved(a, b);
            ok = TRUE;
        }
    }
    os_unlock(&g_flock);
    return ok;
}

extern "C" BOOL MoveFileA(LPCSTR from, LPCSTR to) { return move_core(from, to, 0); }

extern "C" BOOL MoveFileW(LPCWSTR from, LPCWSTR to)
{
    char a[W32_PATH_MAX * 2], b[W32_PATH_MAX * 2];
    return wide_name(from, a, sizeof a) && wide_name(to, b, sizeof b) && move_core(a, b, 1);
}

extern "C" BOOL CopyFileA(LPCSTR from, LPCSTR to, BOOL fail_if_exists)
{
    char a[W32_PATH_MAX], b[W32_PATH_MAX];
    if (!map_or_fail(from, a) || !map_or_fail(to, b))
        return FALSE;
    if (!strcmp(a, b)) { /* onto itself: the destination would truncate the source */
        SetLastError(ERROR_SHARING_VIOLATION);
        return FALSE;
    }
    /* the copy holds both files open for its whole length: the source READ sharing READ, the destination WRITE
     * sharing nothing; the temporary entries make a concurrent open see exactly that */
    struct share_ent sa, sb;
    memset(&sa, 0, sizeof sa);
    memset(&sb, 0, sizeof sb);
    make_key(a, sa.key);
    make_key(b, sb.key);
    snprintf(sa.host, sizeof sa.host, "%s", a);
    snprintf(sb.host, sizeof sb.host, "%s", b);
    sa.rights = SR_R;
    sa.share = FILE_SHARE_READ;
    sb.rights = SR_W;
    sb.share = 0;
    os_lock(&g_flock);
    os_stat_t st, dt;
    int in = -1, out = -1, src_ro = 0;
    BOOL ok = FALSE;
    int sx = os_stat(a, &st), dx = os_stat(b, &dt) == 0;
    if (sx) {
        fail_os(sx, a);
    } else if (st.is_dir) {
        SetLastError(ERROR_ACCESS_DENIED);
    } else if (dx && dt.is_dir) {
        SetLastError(fail_if_exists ? ERROR_FILE_EXISTS : ERROR_ACCESS_DENIED);
    } else if (dx && fail_if_exists) {
        SetLastError(ERROR_FILE_EXISTS);
    } else if (dx && (dt.read_only || ro_has(sb.key))) {
        SetLastError(ERROR_ACCESS_DENIED);
    } else if (share_conflict(sa.key, sa.rights, sa.share) || share_conflict(sb.key, sb.rights, sb.share)) {
        SetLastError(ERROR_SHARING_VIOLATION);
    } else if ((in = os_file_open(a, OS_O_READ)) < 0) {
        fail_os(in, a);
    } else if ((out = os_file_open(b, OS_O_WRITE | OS_O_CREATE | (fail_if_exists ? OS_O_EXCL : OS_O_TRUNC))) < 0) {
        os_file_close(in);
        fail_os(out, b);
        if (out == -OS_EEXIST)
            SetLastError(ERROR_FILE_EXISTS);
    } else {
        src_ro = st.read_only || ro_has(sa.key);
        ent_link(&sa);
        ent_link(&sb);
        ok = TRUE;
    }
    os_unlock(&g_flock);
    if (!ok)
        return FALSE;
    static __thread char buf[64 * 1024];
    int64_t r;
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
    os_lock(&g_flock);
    ent_unlink(&sa);
    ent_unlink(&sb);
    ro_clear(sb.key); /* CopyFile carries the attributes: the copy is read-only iff the source is */
    if (ok && src_ro)
        ro_set(sb.key);
    if (ok)
        clk_file_stamp(b, CLK_FS_CREATE);   /* the copy is a file written now */
    os_unlock(&g_flock);
    return ok;
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
    os_lock(&g_flock);
    DWORD a = attrs_locked(host, &st);
    os_unlock(&g_flock);
    return a;
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
        os_lock(&g_flock);
        fd->dwFileAttributes = attrs_locked(p, &st);
        os_unlock(&g_flock);
        int64_t cft[3];
        if (clk_file_times(p, cft)) {   /* the verification mode's stamps */
            w32_set_ft(&fd->ftCreationTime, (ULONGLONG)cft[0]);
            w32_set_ft(&fd->ftLastAccessTime, (ULONGLONG)cft[1]);
            w32_set_ft(&fd->ftLastWriteTime, (ULONGLONG)cft[2]);
        } else {
        w32_set_ft(&fd->ftCreationTime, w32_unix_us_to_filetime(st.ctime_us));
        w32_set_ft(&fd->ftLastAccessTime, w32_unix_us_to_filetime(st.atime_us));
        w32_set_ft(&fd->ftLastWriteTime, w32_unix_us_to_filetime(st.mtime_us));
        }
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

/* ---- temporary files (kernel32 GetTempPathA, GetTempFileNameA/W) */
#ifndef ERROR_DIRECTORY
#define ERROR_DIRECTORY 267L
#endif
#ifndef ERROR_BUFFER_OVERFLOW
#define ERROR_BUFFER_OVERFLOW 111L
#endif
extern "C" DWORD GetEnvironmentVariableA(LPCSTR, LPSTR, DWORD);   /* crt_kernel32.cpp: the process environment block */
extern "C" UINT GetWindowsDirectoryA(LPSTR, UINT);
extern "C" void GetSystemTimeAsFileTime(LPFILETIME);

/* The documented lookup order: the TMP, TEMP, USERPROFILE variables of the environment block, else the Windows
 * directory; the result ends with a backslash. Size protocol: the length without the NUL when the buffer holds it,
 * else the size needed with the NUL. */
extern "C" DWORD GetTempPathA(DWORD n, LPSTR buf)
{
    static const char *const vars[] = { "TMP", "TEMP", "USERPROFILE" };
    n = W32_DW(n);
    char *dir = NULL;
    size_t len = 0;
    for (const char *v : vars) {
        DWORD need = GetEnvironmentVariableA(v, NULL, 0);   /* 0: unset, 1: empty */
        if (need <= 1)
            continue;
        if (!(dir = (char *)malloc((size_t)need + 2))) {
            SetLastError(ERROR_NOT_ENOUGH_MEMORY);
            return 0;
        }
        DWORD got = GetEnvironmentVariableA(v, dir, need);
        if (got && got < need) {
            len = got;
            break;
        }
        free(dir);   /* changed meanwhile: as unset */
        dir = NULL;
    }
    if (!dir) {
        UINT need = GetWindowsDirectoryA(NULL, 0);
        if (need <= 1 || !(dir = (char *)malloc((size_t)need + 2))) {
            SetLastError(need <= 1 ? ERROR_PATH_NOT_FOUND : ERROR_NOT_ENOUGH_MEMORY);
            return 0;
        }
        UINT got = GetWindowsDirectoryA(dir, need);
        if (!got || got >= need) {
            free(dir);
            SetLastError(ERROR_PATH_NOT_FOUND);
            return 0;
        }
        len = got;
    }
    if (dir[len - 1] != '\\')
        dir[len++] = '\\';
    dir[len] = 0;
    DWORD ret;
    if (n > len && buf) {
        memcpy(buf, dir, len + 1);
        ret = (DWORD)len;
    } else if (n > len) {
        SetLastError(ERROR_INVALID_PARAMETER);
        ret = 0;
    } else {
        ret = (DWORD)len + 1;
    }
    free(dir);
    return ret;
}

/* the name is `base` + "<hex>.tmp" (base = directory, separator and the prefix, in the layer's name encoding `enc`);
 * `dir` must be an existing directory. unique != 0: the name only, with that number. unique == 0: a number from the
 * system time (1..65535, wrapping), the first whose file can be created exclusively; the file is created and closed.
 * Returns the number used; 0 with the error set. */
static UINT temp_core(const char *dir, const char *base, int enc, UINT unique, char *tail)
{
    char conv[W32_PATH_MAX * 2], host[W32_PATH_MAX], name[W32_PATH_MAX * 2];
    const char *d = enc ? dir : ansi_to_utf8(dir, conv, sizeof conv);
    os_stat_t st;
    if (!d || w32_map_path(d, host, sizeof host) || os_stat(host, &st) || !st.is_dir) {
        SetLastError(ERROR_DIRECTORY);
        return 0;
    }
    if (unique) {
        snprintf(tail, 16, "%X.tmp", (unsigned)(unique & 0xFFFF));
        return unique;
    }
    FILETIME ft;
    GetSystemTimeAsFileTime(&ft);
    unsigned u = (unsigned)((w32_ft64(&ft) / 10000) % 0xFFFF) + 1;
    for (unsigned i = 0; i < 0xFFFF; i++, u = u % 0xFFFF + 1) {
        snprintf(tail, 16, "%X.tmp", u);
        snprintf(name, sizeof name, "%s%s", base, tail);
        HANDLE h = w32_create_file_name(name, GENERIC_WRITE, 0, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, enc);
        if (h != INVALID_HANDLE_VALUE) {
            CloseHandle(h);
            return u;
        }
        if (GetLastError() != ERROR_FILE_EXISTS)
            return 0;
    }
    SetLastError(ERROR_FILE_EXISTS);   /* all 65535 names are taken */
    return 0;
}

/* a path longer than MAX_PATH - 14 leaves no room for "\\pre1234.tmp" */
#define TEMP_PATH_MAX 246

extern "C" UINT GetTempFileNameA(LPCSTR path, LPCSTR prefix, UINT unique, LPSTR out)
{
    unique = W32_DW(unique);
    if (!path || !prefix || !out) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0;
    }
    size_t pl = strlen(path), k = 0;
    if (pl > TEMP_PATH_MAX) {
        SetLastError(ERROR_BUFFER_OVERFLOW);
        return 0;
    }
    char base[TEMP_PATH_MAX + 8], tail[16];
    memcpy(base, path, pl);
    if (!pl || path[pl - 1] != '\\')
        base[pl++] = '\\';
    while (k < 3 && prefix[k])   /* the first three characters */
        base[pl++] = prefix[k++];
    base[pl] = 0;
    UINT u = temp_core(path, base, 0, unique, tail);
    if (u) {   /* at most 259 bytes with the NUL, by the path limit */
        memcpy(out, base, pl);
        memcpy(out + pl, tail, strlen(tail) + 1);
    }
    return u;
}

extern "C" UINT GetTempFileNameW(LPCWSTR path, LPCWSTR prefix, UINT unique, LPWSTR out)
{
    unique = W32_DW(unique);
    if (!path || !prefix || !out) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0;
    }
    size_t pl = 0, k = 0;
    while (path[pl])
        pl++;
    if (pl > TEMP_PATH_MAX) {
        SetLastError(ERROR_BUFFER_OVERFLOW);
        return 0;
    }
    WCHAR wbase[TEMP_PATH_MAX + 8];
    char dir8[TEMP_PATH_MAX * 3 + 4], base8[TEMP_PATH_MAX * 3 + 16], tail[16];
    memcpy(wbase, path, pl * sizeof(WCHAR));
    if (!pl || path[pl - 1] != '\\')
        wbase[pl++] = '\\';
    while (k < 3 && prefix[k])
        wbase[pl + k] = prefix[k], k++;
    pl += k;
    wbase[pl] = 0;
    if (utf16_to_utf8(path, dir8, sizeof dir8) || utf16_to_utf8(wbase, base8, sizeof base8)) {
        SetLastError(ERROR_INVALID_NAME);
        return 0;
    }
    UINT u = temp_core(dir8, base8, 1, unique, tail);
    if (u) {
        memcpy(out, wbase, pl * sizeof(WCHAR));
        for (size_t i = 0; (out[pl + i] = (WCHAR)(unsigned char)tail[i]) != 0; i++) {}
    }
    return u;
}

/* GetFileType: a NUL handle is a character device (crt_kernel32.cpp asks) */
bool w32_file_is_device(HANDLE h)
{
    struct w32_obj *o = w32_obj_get_ref(h, W32_FILE);
    if (!o)
        return false;
    bool d = ((struct w32_file *)o)->dev != 0;
    w32_obj_unref(o);
    return d;
}
