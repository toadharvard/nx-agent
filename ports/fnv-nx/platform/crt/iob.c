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

