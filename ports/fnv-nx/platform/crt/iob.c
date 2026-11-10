/* iob.c - MSVC 2008 stdio and lowio for the game (fnvnx_iobuf.h): struct _iobuf streams over the os layer's
 * file descriptors, with MSVC text mode.
 *
 * Layers, as in the MSVC CRT: lowio descriptors (fnvnx_open / _read / _write / _lseek / _setmode) carry the
 * translation mode; a stream (_iobuf) owns one descriptor and a raw read buffer / write buffer, and translates at
 * its own level so that ftell stays a raw file offset:
 *   read, text mode: CR LF -> LF (also across buffer boundaries), a lone CR stays, Ctrl-Z (0x1A) is end of file
 *     (the descriptor stays at the Ctrl-Z until a seek or clearerr);
 *   write, text mode: LF -> CR LF;
 *   'a' / _O_APPEND: every write goes to the end of the file.
 * The MSVC-visible buffer fields (_ptr, _cnt, _base) stay NULL / 0, so inline MSVC code (basic_filebuf) always
 * calls the functions. The standard streams (descriptors 0-2) report text mode but pass bytes to the host's
 * stdout / stderr unchanged (the host console uses LF); stdin is always at end of file. */
#define _FILE_OFFSET_BITS 64
#include "fnvnx_iobuf.h"
#include "fnvnx_msvcrt.h"

#include <errno.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/* the platform lane (platform/win32/platstat.cpp; fnvnx_win32_ext.h is not included in C here) */
extern volatile int fnvnx_plat_on;
void fnvnx_plat_open_failed(const char *win, const char *host, unsigned err, unsigned caller, const char *api);
#define PLAT_RA ((unsigned)(unsigned long)__builtin_return_address(0))

#include "../os/os.h"

unsigned long fnvnx_path_map(const char *win, char *host, size_t n); /* win32/path.cpp */

#if defined(__ILP32__) || __SIZEOF_POINTER__ == 4
_Static_assert(sizeof(struct _iobuf) == 32 && offsetof(struct _iobuf, _file) == 16, "MSVC FILE layout");
_Static_assert(sizeof(fnvnx_ctypevec) == 16, "_Ctypevec");
#endif
_Static_assert(sizeof(fnvnx_msvc_throw_info) == 16 && sizeof(fnvnx_msvc_catchable_type) == 28, "MSVC EH tables");

#define HOST_MAX 1024
#define MAX_FD 256
#define BUFSZ 4096
#define NUNGET 8
#define CTRLZ 0x1A
#define STREAM_MAGIC 0x494f4246u /* "FBOI" */

/* ---- lowio descriptors */
enum { FD_OPEN = 1, FD_TEXT = 2, FD_CTRLZ = 4, FD_APPEND = 8, FD_DEV = 16, FD_READ = 32, FD_WRITE = 64 };
struct fdent {
    int flags;
    int os;        /* os_file descriptor (not for FD_DEV) */
    char *tmpname; /* _O_TEMPORARY / 'D': host path removed on close */
    os_lock_t lock;
};
static struct fdent g_fd[MAX_FD] = {
    { FD_OPEN | FD_TEXT | FD_DEV | FD_READ, -1, NULL, OS_LOCK_INIT },
    { FD_OPEN | FD_TEXT | FD_DEV | FD_WRITE, -1, NULL, OS_LOCK_INIT },
    { FD_OPEN | FD_TEXT | FD_DEV | FD_WRITE, -1, NULL, OS_LOCK_INIT },
};
static os_lock_t g_fd_lock;

/* MSVCRT.DLL's lowio table, which lifted code reads directly: __pioinfo[fd >> 5][fd & 31], 8-byte ioinfo
 * {osfhnd, osfile, pipech}. osfile bits: 0x01 FOPEN, 0x02 FEOFLAG, 0x20 FAPPEND, 0x40 FDEV, 0x80 FTEXT. Every change of
 * a descriptor's state goes through ioinfo_sync, so the table is the descriptor table seen in MSVCRT's encoding.
 * osfhnd is the os descriptor (the standard devices: 0..2); a closed entry is 0. */
struct fnvnx_ioinfo { uint32_t osfhnd; uint8_t osfile; char pipech; uint8_t pad[2]; };
static struct fnvnx_ioinfo g_info[MAX_FD] = { { 0, 0xC1, 10, { 0 } }, { 1, 0xC1, 10, { 0 } }, { 2, 0xC1, 10, { 0 } } };
struct fnvnx_ioinfo *__pioinfo[64] = { g_info, g_info + 32, g_info + 64, g_info + 96, g_info + 128, g_info + 160,
                                       g_info + 192, g_info + 224 };
struct fnvnx_ioinfo __badioinfo = { 0xFFFFFFFFu, 0x80, 10, { 0 } };
static void ioinfo_sync(struct fdent *d)
{
    struct fnvnx_ioinfo *i = &g_info[d - g_fd];
    int f = d->flags;
    i->osfile = (uint8_t)((f & FD_OPEN ? 0x01 : 0) | (f & FD_CTRLZ ? 0x02 : 0) | (f & FD_APPEND ? 0x20 : 0) |
                          (f & FD_DEV ? 0x40 : 0) | (f & FD_TEXT ? 0x80 : 0));
    i->osfhnd = (f & FD_OPEN) ? (f & FD_DEV ? (uint32_t)(d - g_fd) : (uint32_t)d->os) : 0;
}

/* _isatty: the descriptor is a character device (the standard streams) */
int fnvnx_isatty(int fd) { return fd >= 0 && fd < MAX_FD && (g_fd[fd].flags & (FD_OPEN | FD_DEV)) == (FD_OPEN | FD_DEV); }
int fnvnx_fmode = FNVNX_O_TEXT;

static int fail(int e)
{
    errno = e;
    return -1;
}

static int os_errno(int64_t neg)
{
    switch ((int)-neg) {
    case OS_ENOENT: return ENOENT;
    case OS_EACCES: return EACCES;
    case OS_EEXIST: return EEXIST;
    case OS_ENOTDIR: return ENOENT;
    case OS_EISDIR: return EACCES;
    case OS_ENOSPC: return ENOSPC;
    case OS_EINVAL: return EINVAL;
    case OS_ENOMEM: return ENOMEM;
    default: return EIO;
    }
}

static struct fdent *fd_get(int fd)
{
    if (fd < 0 || fd >= MAX_FD || !(g_fd[fd].flags & FD_OPEN)) {
        errno = EBADF;
        return NULL;
    }
    return &g_fd[fd];
}

static int is_text_mode(int oflag)
{
    if (oflag & FNVNX_O_BINARY)
        return 0;
    if (oflag & (FNVNX_O_TEXT | FNVNX_O_WTEXT | FNVNX_O_U16TEXT | FNVNX_O_U8TEXT))
        return 1;
    return (fnvnx_fmode & FNVNX_O_BINARY) == 0;
}

/* opens the host file and claims a descriptor >= 3 */
static int fd_open(const char *path, int oflag, unsigned caller)
{
    int acc = oflag & 3;
    if (!path || acc == 3)
        return fail(EINVAL);
    char host[HOST_MAX];
    if (fnvnx_path_map(path, host, sizeof host)) {
        if (fnvnx_plat_on)
            fnvnx_plat_open_failed(path, NULL, ENOENT, caller, "_open");
        return fail(ENOENT);
    }
    int of = 0;
    if (acc == FNVNX_O_RDONLY || acc == FNVNX_O_RDWR)
        of |= OS_O_READ;
    if (acc == FNVNX_O_WRONLY || acc == FNVNX_O_RDWR)
        of |= OS_O_WRITE;
    if (oflag & FNVNX_O_CREAT)
        of |= OS_O_CREATE | ((oflag & FNVNX_O_EXCL) ? OS_O_EXCL : 0);
    if (oflag & FNVNX_O_TRUNC) {
        if (!(of & OS_O_WRITE))
            return fail(EINVAL);
        of |= OS_O_TRUNC;
    }
    os_stat_t st;
    if (os_stat(host, &st) == 0 && st.is_dir)
        return fail(EACCES);
    int os = os_file_open(host, of);
    if (os < 0) {
        if (fnvnx_plat_on)
            fnvnx_plat_open_failed(path, host, (unsigned)os_errno(os), caller, "_open");
        return fail(os_errno(os));
    }
    os_lock(&g_fd_lock);
    int fd = 3;
    while (fd < MAX_FD && (g_fd[fd].flags & FD_OPEN))
        fd++;
    if (fd == MAX_FD) {
        os_unlock(&g_fd_lock);
        os_file_close(os);
        return fail(EMFILE);
    }
    struct fdent *d = &g_fd[fd];
    d->os = os;
    d->tmpname = (oflag & FNVNX_O_TEMPORARY) ? strdup(host) : NULL;
    d->flags = FD_OPEN | (is_text_mode(oflag) ? FD_TEXT : 0) | ((oflag & FNVNX_O_APPEND) ? FD_APPEND : 0) |
               ((of & OS_O_READ) ? FD_READ : 0) | ((of & OS_O_WRITE) ? FD_WRITE : 0);
    ioinfo_sync(d);
    os_unlock(&g_fd_lock);
    return fd;
}

static int fd_close(int fd)
{
    struct fdent *d = fd_get(fd);
    if (!d)
        return -1;
    int r = 0;
    os_lock(&g_fd_lock);
    if (!(d->flags & FD_DEV) && os_file_close(d->os) < 0)
        r = fail(EBADF);
    if (d->tmpname) {
        os_remove(d->tmpname);
        free(d->tmpname);
        d->tmpname = NULL;
    }
    d->flags = 0;
    d->os = -1;
    ioinfo_sync(d);
    os_unlock(&g_fd_lock);
    return r;
}

/* raw write of the whole buffer (append mode: at the end); devices go to the host's stdout/stderr */
static int64_t raw_write(struct fdent *d, int fd, const void *p, size_t n)
{
    if (d->flags & FD_DEV) {
        FILE *h = fd == 2 ? stderr : stdout;
        size_t w = fwrite(p, 1, n, h);
        fflush(h);
        return (int64_t)w;
    }
    if (d->flags & FD_APPEND)
        os_file_seek(d->os, 0, OS_SEEK_END);
    size_t done = 0;
    while (done < n) {
        int64_t w = os_file_write(d->os, (const char *)p + done, n - done);
        if (w <= 0)
            return w < 0 ? w : -OS_ENOSPC;
        done += (size_t)w;
    }
    return (int64_t)done;
}

/* translates LF -> CR LF into out (2n bytes); returns the output length */
static size_t lf_to_crlf(const unsigned char *in, size_t n, unsigned char *out)
{
    size_t o = 0;
    for (size_t i = 0; i < n; i++) {
        if (in[i] == '\n')
            out[o++] = '\r';
        out[o++] = in[i];
    }
    return o;
}

/* ---- lowio API */
int fnvnx_open(const char *path, int oflag, int pmode)
{
    (void)pmode;
    return fd_open(path, oflag, PLAT_RA);
}

int fnvnx_sopen(const char *path, int oflag, int shflag, int pmode)
{
    (void)shflag;
    return fnvnx_open(path, oflag, pmode);
}

int fnvnx_close(int fd) { return fd_close(fd); }

int fnvnx_read(int fd, void *buf, unsigned int n)
{
    struct fdent *d = fd_get(fd);
    if (!d)
        return -1;
    if (!buf && n)
        return fail(EINVAL);
    if (!(d->flags & FD_READ))
        return fail(EBADF);
    if (d->flags & FD_DEV)
        return 0;
    os_lock(&d->lock);
    unsigned char *out = (unsigned char *)buf;
    int64_t r;
    if (!(d->flags & FD_TEXT)) {
        r = os_file_read(d->os, buf, n);
        os_unlock(&d->lock);
        return r < 0 ? fail(os_errno(r)) : (int)r;
    }
    size_t o = 0;
    while (o < n && !(d->flags & FD_CTRLZ)) {
        unsigned char raw[1024];
        size_t want = n - o < sizeof raw ? n - o : sizeof raw;
        r = os_file_read(d->os, raw, want);
        if (r < 0) {
            os_unlock(&d->lock);
            return fail(os_errno(r));
        }
        if (r == 0)
            break;
        for (size_t i = 0; i < (size_t)r; i++) {
            unsigned char c = raw[i];
            if (c == CTRLZ) { /* end of file: leave the descriptor at the Ctrl-Z */
                d->flags |= FD_CTRLZ, ioinfo_sync(d);
                os_file_seek(d->os, -(int64_t)((size_t)r - i), OS_SEEK_CUR);
                break;
            }
            if (c == '\r') {
                unsigned char nx;
                if (i + 1 < (size_t)r) {
                    if (raw[i + 1] == '\n') {
                        out[o++] = '\n';
                        i++;
                        continue;
                    }
                } else if (os_file_read(d->os, &nx, 1) == 1) { /* CR ends the chunk: look at the next byte */
                    if (nx == '\n') {
                        out[o++] = '\n';
                        continue;
                    }
                    os_file_seek(d->os, -1, OS_SEEK_CUR);
                }
            }
            out[o++] = c;
        }
        if ((size_t)r < want)
            break;
    }
    os_unlock(&d->lock);
    return (int)o;
}

int fnvnx_write(int fd, const void *buf, unsigned int n)
{
    struct fdent *d = fd_get(fd);
    if (!d)
        return -1;
    if (!buf && n)
        return fail(EINVAL);
    if (!(d->flags & FD_WRITE))
        return fail(EBADF);
    os_lock(&d->lock);
    int64_t r = 0;
    if (!(d->flags & FD_TEXT) || (d->flags & FD_DEV)) {
        r = raw_write(d, fd, buf, n);
    } else {
        const unsigned char *in = (const unsigned char *)buf;
        unsigned char tmp[2048];
        for (size_t i = 0; i < n && r >= 0;) {
            size_t chunk = n - i < sizeof tmp / 2 ? n - i : sizeof tmp / 2;
            size_t l = lf_to_crlf(in + i, chunk, tmp);
            r = raw_write(d, fd, tmp, l);
            i += chunk;
        }
    }
    os_unlock(&d->lock);
    if (r < 0)
        return fail(os_errno(r));
    return (int)n;
}

long long fnvnx_lseeki64(int fd, long long off, int whence)
{
    struct fdent *d = fd_get(fd);
    if (!d)
        return -1;
    if (whence < 0 || whence > 2 || (d->flags & FD_DEV))
        return fail(EINVAL);
    os_lock(&d->lock);
    int64_t r = os_file_seek(d->os, off, whence);
    if (r >= 0)
        d->flags &= ~FD_CTRLZ, ioinfo_sync(d);
    os_unlock(&d->lock);
    return r < 0 ? fail(EINVAL) : r;
}

long fnvnx_lseek(int fd, long off, int whence) { return (long)fnvnx_lseeki64(fd, off, whence); }
long long fnvnx_telli64(int fd) { return fnvnx_lseeki64(fd, 0, 1); }
long fnvnx_tell(int fd) { return (long)fnvnx_telli64(fd); }

long fnvnx_filelength(int fd)
{
    struct fdent *d = fd_get(fd);
    if (!d)
        return -1;
    os_stat_t st;
    if ((d->flags & FD_DEV) || os_file_fstat(d->os, &st) < 0)
        return fail(EBADF);
    return (long)st.size;
}

int fnvnx_eof(int fd)
{
    struct fdent *d = fd_get(fd);
    if (!d)
        return -1;
    if (d->flags & FD_DEV)
        return 1;
    os_stat_t st;
    int64_t pos = os_file_seek(d->os, 0, OS_SEEK_CUR);
    if (pos < 0 || os_file_fstat(d->os, &st) < 0)
        return fail(EBADF);
    return (uint64_t)pos >= st.size;
}

int fnvnx_commit(int fd)
{
    struct fdent *d = fd_get(fd);
    if (!d)
        return -1;
    return (d->flags & FD_DEV) || os_file_sync(d->os) == 0 ? 0 : fail(EBADF);
}

int fnvnx_setmode(int fd, int mode)
{
    struct fdent *d = fd_get(fd);
    if (!d)
        return -1;
    int text;
    if (mode == FNVNX_O_BINARY)
        text = 0;
    else if (mode == FNVNX_O_TEXT || mode == FNVNX_O_WTEXT || mode == FNVNX_O_U16TEXT || mode == FNVNX_O_U8TEXT)
        text = 1;
    else
        return fail(EINVAL);
    os_lock(&d->lock);
    int old = (d->flags & FD_TEXT) ? FNVNX_O_TEXT : FNVNX_O_BINARY;
    d->flags = text ? d->flags | FD_TEXT : d->flags & ~FD_TEXT;
    ioinfo_sync(d);
    os_unlock(&d->lock);
    return old;
}

int fnvnx_set_fmode(int mode)
{
    if (mode != FNVNX_O_TEXT && mode != FNVNX_O_BINARY && mode != FNVNX_O_WTEXT)
        return errno = EINVAL;
    fnvnx_fmode = mode;
    return 0;
}

int fnvnx_get_fmode(int *mode)
{
    if (!mode)
        return errno = EINVAL;
    *mode = fnvnx_fmode;
    return 0;
}

/* ---- streams */
enum { LAST_NONE, LAST_READ, LAST_WRITE };
struct sext {
    int fd;
    int last;
    unsigned char *rbuf; /* raw bytes from the descriptor, rpos..rlen not consumed yet */
    size_t rpos, rlen;
    unsigned char *wbuf; /* raw (translated) bytes not written yet */
    size_t wlen;
    unsigned char ungot[NUNGET];
    int nungot;
    os_lock_t lock;
};
struct stream {
    struct _iobuf iob; /* first: the game's FILE* points here */
    uint32_t magic;
    struct sext x;
    struct stream *next;
};

struct _iobuf fnvnx_iob[FNVNX_IOB_COUNT] = {
    { NULL, 0, NULL, FNVNX_IOREAD, 0, 0, 0, NULL },
    { NULL, 0, NULL, FNVNX_IOWRT, 1, 0, 0, NULL },
    { NULL, 0, NULL, FNVNX_IOWRT, 2, 0, 0, NULL },
};
static struct sext g_stdx[3] = { { 0, 0, NULL, 0, 0, NULL, 0, { 0 }, 0, OS_LOCK_INIT },
                                 { 1, 0, NULL, 0, 0, NULL, 0, { 0 }, 0, OS_LOCK_INIT },
                                 { 2, 0, NULL, 0, 0, NULL, 0, { 0 }, 0, OS_LOCK_INIT } };
static struct stream *g_streams;
static os_lock_t g_streams_lock;

struct _iobuf *__iob_func(void) { return fnvnx_iob; }

static struct sext *ext_of(struct _iobuf *f)
{
    if (!f) {
        errno = EINVAL;
        return NULL;
    }
    if (f >= fnvnx_iob && f < fnvnx_iob + FNVNX_IOB_COUNT) {
        if (f < fnvnx_iob + 3)
            return &g_stdx[f - fnvnx_iob];
        errno = EINVAL; /* _iob[3..19]: the slots MSVCRT hands to fopen are not used here */
        return NULL;
    }
    struct stream *s = (struct stream *)f;
    if (s->magic != STREAM_MAGIC) {
        static int warned;
        if (warned++ < 4)
            os_debug_print("[fnvnx] CRT stdio called with a FILE* that is not an fnvnx _iobuf");
        errno = EINVAL;
        return NULL;
    }
    return &s->x;
}

/* lock the stream; NULL (errno set) if f is not a stream */
static struct sext *lock_stream(struct _iobuf *f)
{
    struct sext *x = ext_of(f);
    if (x)
        os_lock(&x->lock);
    return x;
}

static void set_err(struct _iobuf *f) { f->_flag |= FNVNX_IOERR; }

static int wflush(struct _iobuf *f, struct sext *x)
{
    if (!x->wlen)
        return 0;
    struct fdent *d = fd_get(x->fd);
    int64_t r = d ? raw_write(d, x->fd, x->wbuf, x->wlen) : -OS_EINVAL;
    x->wlen = 0;
    if (r < 0) {
        set_err(f);
        errno = d ? os_errno(r) : EBADF;
        return -1;
    }
    return 0;
}

/* drops read-ahead (and pushed-back characters), moving the descriptor back to the logical position */
static void rdrop(struct sext *x, struct fdent *d)
{
    if (x->rlen > x->rpos && d && !(d->flags & FD_DEV))
        os_file_seek(d->os, -(int64_t)(x->rlen - x->rpos), OS_SEEK_CUR);
    x->rpos = x->rlen = 0;
    x->nungot = 0;
}

static int begin_read(struct _iobuf *f, struct sext *x, struct fdent **pd)
{
    struct fdent *d = fd_get(x->fd);
    if (!d || !(d->flags & FD_READ)) {
        set_err(f);
        errno = EBADF;
        return -1;
    }
    if (x->last == LAST_WRITE && wflush(f, x))
        return -1;
    x->last = LAST_READ;
    *pd = d;
    return 0;
}

static int begin_write(struct _iobuf *f, struct sext *x, struct fdent **pd)
{
    struct fdent *d = fd_get(x->fd);
    if (!d || !(d->flags & FD_WRITE)) {
        set_err(f);
        errno = EBADF;
        return -1;
    }
    if (x->last == LAST_READ)
        rdrop(x, d);
    x->last = LAST_WRITE;
    *pd = d;
    return 0;
}

/* refills the raw buffer; 1 data, 0 end of file, -1 error */
static int rfill(struct _iobuf *f, struct sext *x, struct fdent *d)
{
    if (d->flags & FD_DEV)
        return 0;
    if (!x->rbuf && !(x->rbuf = (unsigned char *)malloc(BUFSZ))) {
        set_err(f);
        errno = ENOMEM;
        return -1;
    }
    int64_t r = os_file_read(d->os, x->rbuf, BUFSZ);
    x->rpos = 0;
    x->rlen = r > 0 ? (size_t)r : 0;
    if (r < 0) {
        set_err(f);
        errno = os_errno(r);
        return -1;
    }
    return r > 0;
}

/* one logical character, or EOF (end of file / Ctrl-Z / error) */
static int getch(struct _iobuf *f, struct sext *x, struct fdent *d)
{
    if (x->nungot)
        return x->ungot[--x->nungot];
    if (d->flags & FD_CTRLZ)
        goto eof;
    if (x->rpos == x->rlen) {
        int r = rfill(f, x, d);
        if (r <= 0) {
            if (r == 0)
                goto eof;
            return EOF;
        }
    }
    int c = x->rbuf[x->rpos];
    if (!(d->flags & FD_TEXT)) {
        x->rpos++;
        return c;
    }
    if (c == CTRLZ) { /* rpos stays on the Ctrl-Z, so ftell reports its offset */
        d->flags |= FD_CTRLZ, ioinfo_sync(d);
        goto eof;
    }
    x->rpos++;
    if (c == '\r') {
        if (x->rpos == x->rlen && rfill(f, x, d) <= 0)
            return '\r';
        if (x->rbuf[x->rpos] == '\n') {
            x->rpos++;
            return '\n';
        }
    }
    return c;
eof:
    f->_flag |= FNVNX_IOEOF;
    return EOF;
}

/* reads up to n logical bytes; returns the count */
static size_t sread(struct _iobuf *f, struct sext *x, struct fdent *d, unsigned char *out, size_t n)
{
    size_t o = 0;
    while (o < n && x->nungot)
        out[o++] = x->ungot[--x->nungot];
    if (!(d->flags & FD_TEXT)) {
        while (o < n) {
            if (x->rpos < x->rlen) {
                size_t k = x->rlen - x->rpos < n - o ? x->rlen - x->rpos : n - o;
                memcpy(out + o, x->rbuf + x->rpos, k);
                x->rpos += k;
                o += k;
                continue;
            }
            if (n - o >= BUFSZ && !(d->flags & FD_DEV)) { /* large binary read: straight from the file */
                int64_t r = os_file_read(d->os, out + o, n - o);
                if (r < 0) {
                    set_err(f);
                    errno = os_errno(r);
                    break;
                }
                if (r == 0) {
                    f->_flag |= FNVNX_IOEOF;
                    break;
                }
                o += (size_t)r;
                continue;
            }
            int r = rfill(f, x, d);
            if (r <= 0) {
                if (r == 0)
                    f->_flag |= FNVNX_IOEOF;
                break;
            }
        }
        return o;
    }
    while (o < n) {
        /* copy a run without CR / Ctrl-Z, then handle one character */
        if (x->rpos < x->rlen && !(d->flags & FD_CTRLZ)) {
            size_t k = 0, lim = x->rlen - x->rpos < n - o ? x->rlen - x->rpos : n - o;
            const unsigned char *p = x->rbuf + x->rpos;
            while (k < lim && p[k] != '\r' && p[k] != CTRLZ)
                k++;
            memcpy(out + o, p, k);
            x->rpos += k;
            o += k;
            if (o == n)
                break;
        }
        int c = getch(f, x, d);
        if (c == EOF)
            break;
        out[o++] = (unsigned char)c;
    }
    return o;
}

static int put_raw(struct _iobuf *f, struct sext *x, struct fdent *d, const unsigned char *p, size_t n)
{
    if (d->flags & FD_DEV) { /* the host console: unbuffered here, unchanged */
        if (raw_write(d, x->fd, p, n) != (int64_t)n) {
            set_err(f);
            return -1;
        }
        return 0;
    }
    if (!x->wbuf && !(x->wbuf = (unsigned char *)malloc(BUFSZ))) {
        set_err(f);
        errno = ENOMEM;
        return -1;
    }
    while (n) {
        if (x->wlen == BUFSZ && wflush(f, x))
            return -1;
        size_t k = BUFSZ - x->wlen < n ? BUFSZ - x->wlen : n;
        memcpy(x->wbuf + x->wlen, p, k);
        x->wlen += k;
        p += k;
        n -= k;
    }
    return 0;
}

static int swrite(struct _iobuf *f, struct sext *x, struct fdent *d, const unsigned char *p, size_t n)
{
    if (!(d->flags & FD_TEXT) || (d->flags & FD_DEV))
        return put_raw(f, x, d, p, n);
    while (n) {
        const unsigned char *nl = (const unsigned char *)memchr(p, '\n', n);
        size_t k = nl ? (size_t)(nl - p) : n;
        if (k && put_raw(f, x, d, p, k))
            return -1;
        if (!nl)
            break;
        if (put_raw(f, x, d, (const unsigned char *)"\r\n", 2))
            return -1;
        p += k + 1;
        n -= k + 1;
    }
    return 0;
}

static long long stell(struct sext *x, struct fdent *d)
{
    if (d->flags & FD_DEV)
        return -1;
    int64_t pos = os_file_seek(d->os, 0, OS_SEEK_CUR);
    if (pos < 0)
        return -1;
    long long l = (long long)pos - (long long)(x->rlen - x->rpos) - x->nungot + (long long)x->wlen;
    if (x->wlen && (d->flags & FD_APPEND)) {
        os_stat_t st;
        if (os_file_fstat(d->os, &st) == 0)
            l = (long long)st.size + (long long)x->wlen;
    }
    return l < 0 ? 0 : l;
}

/* ---- opening */
static int parse_mode(const char *m, int *oflag, int *iobflag)
{
    if (!m)
        return -1;
    while (*m == ' ')
        m++;
    int plus = 0, tb = 0;
    switch (*m) {
    case 'r': *oflag = FNVNX_O_RDONLY; *iobflag = FNVNX_IOREAD; break;
    case 'w': *oflag = FNVNX_O_WRONLY | FNVNX_O_CREAT | FNVNX_O_TRUNC; *iobflag = FNVNX_IOWRT; break;
    case 'a': *oflag = FNVNX_O_WRONLY | FNVNX_O_CREAT | FNVNX_O_APPEND; *iobflag = FNVNX_IOWRT; break;
    default: return -1;
    }
    for (m++; *m; m++) {
        switch (*m) {
        case ' ': break;
        case '+':
            if (plus++)
                return -1;
            *oflag = (*oflag & ~3) | FNVNX_O_RDWR;
            *iobflag = FNVNX_IORW;
            break;
        case 't':
        case 'b':
            if (tb++)
                return -1;
            *oflag |= *m == 't' ? FNVNX_O_TEXT : FNVNX_O_BINARY;
            break;
        case 'c': case 'n': case 'S': case 'R': case 'T': case 'N': break; /* commit / cache hints */
        case 'D': *oflag |= FNVNX_O_TEMPORARY; break;
        case ',': /* ",ccs=UNICODE|UTF-8|UTF-16LE": Unicode text mode, kept as plain text mode here */
            if (tb)
                return -1;
            *oflag |= FNVNX_O_TEXT;
            return 0;
        default: return -1;
        }
    }
    return 0;
}

static struct _iobuf *stream_new(int fd, int iobflag)
{
    struct stream *s = (struct stream *)calloc(1, sizeof *s);
    if (!s) {
        errno = ENOMEM;
        return NULL;
    }
    s->iob._flag = iobflag;
    s->iob._file = fd;
    s->magic = STREAM_MAGIC;
    s->x.fd = fd;
    os_lock(&g_streams_lock);
    s->next = g_streams;
    g_streams = s;
    os_unlock(&g_streams_lock);
    return &s->iob;
}

struct _iobuf *fnvnx_iob_fsopen(const char *path, const char *mode, int shflag)
{
    (void)shflag;
    int oflag, iobflag;
    if (!path || parse_mode(mode, &oflag, &iobflag)) {
        errno = EINVAL;
        return NULL;
    }
    int fd = fd_open(path, oflag, PLAT_RA);
    if (fd < 0)
        return NULL;
    struct _iobuf *f = stream_new(fd, iobflag);
    if (!f)
        fd_close(fd);
    return f;
}

struct _iobuf *fnvnx_iob_fopen(const char *path, const char *mode) { return fnvnx_iob_fsopen(path, mode, 0); }

int fnvnx_iob_fopen_s(struct _iobuf **pf, const char *path, const char *mode)
{
    if (!pf)
        return EINVAL;
    *pf = fnvnx_iob_fsopen(path, mode, 0);
    return *pf ? 0 : errno;
}

/* _fdopen: the stream follows the descriptor's translation mode ('t' / 'b' are only checked, as in MSVC) */
struct _iobuf *fnvnx_iob_fdopen(int fd, const char *mode)
{
    int oflag, iobflag;
    if (parse_mode(mode, &oflag, &iobflag)) {
        errno = EINVAL;
        return NULL;
    }
    struct fdent *d = fd_get(fd);
    if (!d)
        return NULL;
    if (fd < 3) {
        errno = EINVAL; /* the standard streams already have their _iobuf */
        return NULL;
    }
    if (oflag & FNVNX_O_APPEND)
        d->flags |= FD_APPEND, ioinfo_sync(d);
    return stream_new(fd, iobflag);
}

int fnvnx_iob_fclose_nolock(struct _iobuf *f)
{
    struct sext *x = ext_of(f);
    if (!x)
        return EOF;
    os_lock(&x->lock);
    int r = wflush(f, x);
    os_unlock(&x->lock);
    if (f >= fnvnx_iob && f < fnvnx_iob + 3)
        return r ? EOF : 0; /* the host streams stay open */
    struct stream *s = (struct stream *)f;
    os_lock(&g_streams_lock);
    for (struct stream **pp = &g_streams; *pp; pp = &(*pp)->next)
        if (*pp == s) {
            *pp = s->next;
            break;
        }
    os_unlock(&g_streams_lock);
    if (fd_close(x->fd))
        r = -1;
    free(x->rbuf);
    free(x->wbuf);
    s->magic = 0;
    free(s);
    return r ? EOF : 0;
}

int fnvnx_iob_fclose(struct _iobuf *f) { return fnvnx_iob_fclose_nolock(f); }

int fnvnx_iob_fcloseall(void)
{
    int n = 0;
    for (;;) {
        os_lock(&g_streams_lock);
        struct stream *s = g_streams;
        os_unlock(&g_streams_lock);
        if (!s)
            break;
        fnvnx_iob_fclose(&s->iob);
        n++;
    }
    return n;
}

/* ---- reading and writing */
size_t fnvnx_iob_fread(void *buf, size_t size, size_t count, struct _iobuf *f)
{
    if (!size || !count)
        return 0;
    if (!buf || count > (size_t)-1 / size) {
        errno = EINVAL;
        return 0;
    }
    struct sext *x = lock_stream(f);
    if (!x)
        return 0;
    struct fdent *d;
    size_t got = 0;
    if (begin_read(f, x, &d) == 0)
        got = sread(f, x, d, (unsigned char *)buf, size * count);
    os_unlock(&x->lock);
    return got / size;
}

size_t fnvnx_iob_fwrite(const void *buf, size_t size, size_t count, struct _iobuf *f)
{
    if (!size || !count)
        return 0;
    if (!buf || count > (size_t)-1 / size) {
        errno = EINVAL;
        return 0;
    }
    struct sext *x = lock_stream(f);
    if (!x)
        return 0;
    struct fdent *d;
    size_t r = 0;
    if (begin_write(f, x, &d) == 0 && swrite(f, x, d, (const unsigned char *)buf, size * count) == 0)
        r = count;
    os_unlock(&x->lock);
    return r;
}

int fnvnx_iob_fgetc(struct _iobuf *f)
{
    struct sext *x = lock_stream(f);
    if (!x)
        return EOF;
    struct fdent *d;
    int c = begin_read(f, x, &d) == 0 ? getch(f, x, d) : EOF;
    os_unlock(&x->lock);
    return c;
}

int fnvnx_iob_fputc(int c, struct _iobuf *f)
{
    struct sext *x = lock_stream(f);
    if (!x)
        return EOF;
    struct fdent *d;
    unsigned char b = (unsigned char)c;
    int r = begin_write(f, x, &d) == 0 && swrite(f, x, d, &b, 1) == 0 ? b : EOF;
    os_unlock(&x->lock);
    return r;
}

int fnvnx_iob_ungetc(int c, struct _iobuf *f)
{
    if (c == EOF)
        return EOF;
    struct sext *x = lock_stream(f);
    if (!x)
        return EOF;
    int r = EOF;
    struct fdent *d = fd_get(x->fd);
    if (d && (d->flags & FD_READ) && x->nungot < NUNGET) {
        if (x->last == LAST_WRITE)
            wflush(f, x);
        x->last = LAST_READ;
        x->ungot[x->nungot++] = (unsigned char)c;
        f->_flag &= ~FNVNX_IOEOF;
        r = (unsigned char)c;
    }
    os_unlock(&x->lock);
    return r;
}

char *fnvnx_iob_fgets(char *buf, int n, struct _iobuf *f)
{
    if ((!buf && n) || n < 0) {
        errno = EINVAL;
        return NULL;
    }
    if (n == 0)
        return NULL;
    struct sext *x = lock_stream(f);
    if (!x)
        return NULL;
    struct fdent *d;
    char *r = NULL;
    if (begin_read(f, x, &d) == 0) {
        int i = 0;
        while (i < n - 1) {
            int c = getch(f, x, d);
            if (c == EOF)
                break;
            buf[i++] = (char)c;
            if (c == '\n')
                break;
        }
        if (i > 0 && !(f->_flag & FNVNX_IOERR)) {
            buf[i] = 0;
            r = buf;
        }
    }
    os_unlock(&x->lock);
    return r;
}

int fnvnx_iob_fputs(const char *s, struct _iobuf *f)
{
    if (!s) {
        errno = EINVAL;
        return EOF;
    }
    size_t l = strlen(s);
    return fnvnx_iob_fwrite(s, 1, l, f) == l || !l ? 0 : EOF;
}

int fnvnx_iob_puts(const char *s)
{
    if (fnvnx_iob_fputs(s, &fnvnx_iob[1]) == EOF || fnvnx_iob_fputc('\n', &fnvnx_iob[1]) == EOF)
        return EOF;
    return 0;
}

int fnvnx_iob_vfprintf(struct _iobuf *f, const char *fmt, va_list ap)
{
    if (!fmt || !ext_of(f)) {
        errno = EINVAL;
        return -1;
    }
    va_list a2;
    va_copy(a2, ap);
    int n = _vscprintf(fmt, a2);
    va_end(a2);
    if (n < 0)
        return -1;
    char small[512], *b = (size_t)n < sizeof small ? small : (char *)malloc((size_t)n + 1);
    if (!b) {
        errno = ENOMEM;
        return -1;
    }
    _vsnprintf(b, (size_t)n + 1, fmt, ap);
    size_t w = n ? fnvnx_iob_fwrite(b, 1, (size_t)n, f) : 0;
    if (b != small)
        free(b);
    return w == (size_t)n ? n : -1;
}

int fnvnx_iob_fprintf(struct _iobuf *f, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    int r = fnvnx_iob_vfprintf(f, fmt, ap);
    va_end(ap);
    return r;
}

int fnvnx_iob_vprintf(const char *fmt, va_list ap) { return fnvnx_iob_vfprintf(&fnvnx_iob[1], fmt, ap); }

int fnvnx_iob_printf(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    int r = fnvnx_iob_vfprintf(&fnvnx_iob[1], fmt, ap);
    va_end(ap);
    return r;
}

/* ---- positioning, state */
int fnvnx_iob_fseeki64(struct _iobuf *f, long long off, int whence)
{
    if (whence < 0 || whence > 2) {
        errno = EINVAL;
        return -1;
    }
    struct sext *x = lock_stream(f);
    if (!x)
        return -1;
    int r = -1;
    struct fdent *d = fd_get(x->fd);
    if (!d || (d->flags & FD_DEV)) {
        errno = EINVAL;
    } else if (wflush(f, x) == 0) {
        int64_t p;
        if (whence == 2) {
            p = os_file_seek(d->os, off, OS_SEEK_END);
        } else {
            long long target = whence == 1 ? stell(x, d) + off : off;
            p = target < 0 ? -1 : os_file_seek(d->os, target, OS_SEEK_SET);
        }
        if (p < 0) {
            errno = EINVAL;
        } else {
            x->rpos = x->rlen = 0;
            x->nungot = 0;
            x->last = LAST_NONE;
            d->flags &= ~FD_CTRLZ, ioinfo_sync(d);
            f->_flag &= ~FNVNX_IOEOF;
            r = 0;
        }
    }
    os_unlock(&x->lock);
    return r;
}

int fnvnx_iob_fseek(struct _iobuf *f, long off, int whence) { return fnvnx_iob_fseeki64(f, off, whence); }

long long fnvnx_iob_ftelli64(struct _iobuf *f)
{
    struct sext *x = lock_stream(f);
    if (!x)
        return -1;
    struct fdent *d = fd_get(x->fd);
    long long r = d ? stell(x, d) : -1;
    if (r < 0)
        errno = EINVAL;
    os_unlock(&x->lock);
    return r;
}

long fnvnx_iob_ftell(struct _iobuf *f)
{
    long long r = fnvnx_iob_ftelli64(f);
    if (r > 0x7FFFFFFFLL) { /* MSVC long is 32-bit */
        errno = EINVAL;
        return -1;
    }
    return (long)r;
}

int fnvnx_iob_fgetpos(struct _iobuf *f, long long *pos)
{
    if (!pos) {
        errno = EINVAL;
        return -1;
    }
    long long r = fnvnx_iob_ftelli64(f);
    if (r < 0)
        return -1;
    *pos = r;
    return 0;
}

int fnvnx_iob_fsetpos(struct _iobuf *f, const long long *pos)
{
    if (!pos) {
        errno = EINVAL;
        return -1;
    }
    return fnvnx_iob_fseeki64(f, *pos, 0);
}

void fnvnx_iob_rewind(struct _iobuf *f)
{
    if (fnvnx_iob_fseeki64(f, 0, 0) == 0)
        fnvnx_iob_clearerr(f);
}

int fnvnx_iob_feof(struct _iobuf *f) { return ext_of(f) ? f->_flag & FNVNX_IOEOF : 0; }
int fnvnx_iob_ferror(struct _iobuf *f) { return ext_of(f) ? f->_flag & FNVNX_IOERR : 0; }

void fnvnx_iob_clearerr(struct _iobuf *f)
{
    struct sext *x = lock_stream(f);
    if (!x)
        return;
    f->_flag &= ~(FNVNX_IOERR | FNVNX_IOEOF);
    struct fdent *d = fd_get(x->fd);
    if (d)
        d->flags &= ~FD_CTRLZ, ioinfo_sync(d); /* MSVC clears the descriptor's end-of-file flag too */
    os_unlock(&x->lock);
}

/* fflush: writes pending output; on a stream that is reading, keeps the logical position (MSVC 2008 discarded
 * the read buffer, which skipped buffered input - nothing in the game relies on that) */
int fnvnx_iob_fflush(struct _iobuf *f)
{
    if (!f) {
        int r = 0;
        for (int i = 1; i < 3; i++)
            r |= fnvnx_iob_fflush(&fnvnx_iob[i]);
        os_lock(&g_streams_lock);
        for (struct stream *s = g_streams; s; s = s->next) {
            os_lock(&s->x.lock);
            r |= wflush(&s->iob, &s->x);
            os_unlock(&s->x.lock);
        }
        os_unlock(&g_streams_lock);
        return r ? EOF : 0;
    }
    struct sext *x = lock_stream(f);
    if (!x)
        return EOF;
    int r = wflush(f, x);
    if (x->last == LAST_READ) {
        rdrop(x, fd_get(x->fd));
        x->last = LAST_NONE;
    }
    os_unlock(&x->lock);
    return r ? EOF : 0;
}

int fnvnx_iob_fileno(struct _iobuf *f) { return ext_of(f) ? f->_file : -1; }

int fnvnx_iob_setvbuf(struct _iobuf *f, char *buf, int mode, size_t size)
{
    (void)buf;
    /* MSVC: _IOFBF 0x0000, _IOLBF 0x0040, _IONBF 0x0004; size 2..INT_MAX unless unbuffered */
    if (!ext_of(f) || (mode != 0 && mode != 0x40 && mode != 4) || (mode != 4 && (size < 2 || size > 0x7FFFFFFF))) {
        errno = EINVAL;
        return -1;
    }
    return 0;
}
