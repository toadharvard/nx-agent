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
#include "fnvnx_crt_config.h"
#include "fnvnx_iobuf.h"
#include "fnvnx_msvcrt.h"

#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/* the platform lane (platform/win32/platstat.cpp; fnvnx_win32_ext.h is not included in C here) */
extern volatile int fnvnx_plat_on;
void fnvnx_plat_open_failed(const char *win, const char *host, unsigned err, unsigned caller, const char *api);
#define PLAT_RA ((unsigned)(unsigned long)__builtin_return_address(0))

#include "../os/os.h"

unsigned long fnvnx_path_map(const char *win, char *host, size_t n); /* win32/path.cpp */

/* kernel32 (platform/win32), the objects a descriptor holds: HANDLE = void * (ILP32) */
typedef void *HANDLE;
int ReadFile(HANDLE h, void *buf, unsigned n, unsigned *got, void *overlapped);
int WriteFile(HANDLE h, const void *buf, unsigned n, unsigned *put, void *overlapped);
int SetFilePointerEx(HANDLE h, long long dist, long long *newpos, unsigned method);
unsigned GetFileType(HANDLE h);
unsigned GetLastError(void);
#define MSVCRT_NO_CONSOLE_FD (-2)
#define MSVCRT_NO_CONSOLE ((HANDLE)MSVCRT_NO_CONSOLE_FD)
#define INVALID_HANDLE ((HANDLE)(intptr_t)-1)

#if defined(__ILP32__) || __SIZEOF_POINTER__ == 4
_Static_assert(sizeof(struct _iobuf) == 32 && offsetof(struct _iobuf, _file) == 16, "MSVC FILE layout");
_Static_assert(sizeof(fnvnx_ctypevec) == 16, "_Ctypevec");
#endif

#define MAX_FD 2048   /* MSVCRT_MAX_FILES (Wine 9.0 file.c, _MSVCR_VER >= 80) */
#define BUFSZ 4096
#define NUNGET 8
#define CTRLZ 0x1A
#define STREAM_MAGIC 0x494f4246u /* "FBOI" */

/* ---- lowio descriptors: the table below (Wine 9.0 ioinfo) is the only descriptor state. A descriptor holds the kernel32 HANDLE of its object
 * (Wine file.c msvcrt_alloc_fd / msvcrt_set_fd); every read, write and seek is ReadFile / WriteFile / SetFilePointerEx on that handle, so the
 * kernel32 handle table is the one source of truth for what a descriptor refers to, its access and its position. */
static os_lock_t g_fdlock[MAX_FD]; /* per descriptor; zero is the unlocked state (OS_LOCK_INIT) */
/* The lowio table, as Wine 9.0 dlls/msvcrt/file.c defines it for the exe's CRT (_MSVCR_VER >= 80: MSVCRT_MAX_FILES 2048,
 * MSVCRT_FD_BLOCK_SIZE 32, `ioinfo` with handle, wxflag, lookahead[3], exflag, crit, textmode/unicode, pipech2, startpos,
 * utf8translations, dbcsBuffer, dbcsBufferUsed; __badioinfo = { INVALID_HANDLE_VALUE, WX_TEXT }; __pioinfo = the array of
 * MAX_FILES / BLOCK_SIZE block pointers). The offsets are those of the i386 build of that struct. wxflag bits are Wine's WX_*
 * (0x01 OPEN, 0x02 ATEOF, 0x20 APPEND, 0x40 TTY, 0x80 TEXT); lookahead[0..2] start as '\n' (file.c msvcrt_set_fd). The
 * handle is the Win32 HANDLE of the descriptor (INVALID_HANDLE_VALUE when free, MSVCRT_NO_CONSOLE for a standard stream without a console). */
#define MSVCRT_MAX_FILES 2048
#define MSVCRT_FD_BLOCK_SIZE 32
struct fnvnx_ioinfo {
    uint32_t handle;
    uint8_t wxflag;
    char lookahead[3];
    int exflag;
    uint32_t crit[6]; /* RTL_CRITICAL_SECTION, 32-bit: unused here, descriptors lock through g_fdlock */
    char textmode : 7;
    char unicode : 1;
    char pipech2[2];
    uint32_t startpos[2]; /* __int64 at 4-byte alignment, as in the i386 struct */
    int utf8translations;
    char dbcsBuffer[1];
    int dbcsBufferUsed;
};
_Static_assert(offsetof(struct fnvnx_ioinfo, wxflag) == 4 && offsetof(struct fnvnx_ioinfo, exflag) == 8 &&
                   offsetof(struct fnvnx_ioinfo, crit) == 12 && offsetof(struct fnvnx_ioinfo, pipech2) == 37 &&
                   offsetof(struct fnvnx_ioinfo, startpos) == 40 && offsetof(struct fnvnx_ioinfo, utf8translations) == 48 &&
                   offsetof(struct fnvnx_ioinfo, dbcsBufferUsed) == 56 && sizeof(struct fnvnx_ioinfo) == 60,
               "Wine 9.0 file.c ioinfo (i386, _MSVCR_VER >= 80)");
#define WX_OPEN 0x01
#define WX_ATEOF 0x02
#define WX_APPEND 0x20
#define WX_TTY 0x40
#define WX_TEXT 0x80
/* Initial state = the result of Wine 9.0 msvcrt_init_io on the declared machine: no startup lpReserved2 descriptors, and the standard handles
 * are NULL (crt_kernel32.cpp g_std) whose GetFileType is FILE_TYPE_UNKNOWN, so descriptors 0-2 are WX_OPEN | WX_TEXT | WX_TTY with
 * MSVCRT_NO_CONSOLE (file.c msvcrt_init_io); every other descriptor is free (handle INVALID_HANDLE_VALUE, as get_ioinfo_alloc_block sets it). */
#define IOINFO_STD { 0xFFFFFFFEu, WX_OPEN | WX_TTY | WX_TEXT, { '\n', '\n', '\n' }, 0, { 0 }, 0, 0, { 0 }, { 0 }, 0, { 0 }, 0 }
#define IOINFO_FREE { 0xFFFFFFFFu, 0, { 0 }, 0, { 0 }, 0, 0, { 0 }, { 0 }, 0, { 0 }, 0 }
static struct fnvnx_ioinfo g_info[MSVCRT_MAX_FILES] = {
    [0] = IOINFO_STD, [1] = IOINFO_STD, [2] = IOINFO_STD,
    [3 ... MSVCRT_MAX_FILES - 1] = IOINFO_FREE,
};
struct fnvnx_ioinfo *__pioinfo[MSVCRT_MAX_FILES / MSVCRT_FD_BLOCK_SIZE] = {   /* every block exists: MAX_FD == MSVCRT_MAX_FILES */
    g_info + 0, g_info + 32, g_info + 64, g_info + 96,
    g_info + 128, g_info + 160, g_info + 192, g_info + 224,
    g_info + 256, g_info + 288, g_info + 320, g_info + 352,
    g_info + 384, g_info + 416, g_info + 448, g_info + 480,
    g_info + 512, g_info + 544, g_info + 576, g_info + 608,
    g_info + 640, g_info + 672, g_info + 704, g_info + 736,
    g_info + 768, g_info + 800, g_info + 832, g_info + 864,
    g_info + 896, g_info + 928, g_info + 960, g_info + 992,
    g_info + 1024, g_info + 1056, g_info + 1088, g_info + 1120,
    g_info + 1152, g_info + 1184, g_info + 1216, g_info + 1248,
    g_info + 1280, g_info + 1312, g_info + 1344, g_info + 1376,
    g_info + 1408, g_info + 1440, g_info + 1472, g_info + 1504,
    g_info + 1536, g_info + 1568, g_info + 1600, g_info + 1632,
    g_info + 1664, g_info + 1696, g_info + 1728, g_info + 1760,
    g_info + 1792, g_info + 1824, g_info + 1856, g_info + 1888,
    g_info + 1920, g_info + 1952, g_info + 1984, g_info + 2016
};
_Static_assert(MAX_FD == MSVCRT_MAX_FILES, "one block pointer per MSVCRT_FD_BLOCK_SIZE descriptors");
struct fnvnx_ioinfo __badioinfo = { 0xFFFFFFFFu, WX_TEXT, { 0 }, 0, { 0 }, 0, 0, { 0 }, { 0 }, 0, { 0 }, 0 };
/* Wine 9.0 dlls/msvcrt/errno.c msvcrt_set_errno: the CRT errno of a Win32 error (the cases of that switch, grouped as its fallthroughs group them) */
static int win_errno(unsigned err)
{
    switch (err) {
    case 5: case 65: case 82: case 132: case 167: case 83: case 16: case 108: case 158: case 12: case 32: case 33: return EACCES;
    case 2: case 18: case 161: case 53: case 15: case 67: case 206: case 3: return ENOENT;
    case 1117: return EIO;
    case 11: return ENOEXEC;
    case 6: return EBADF;
    case 14: case 9: case 1816: case 7: return ENOMEM;
    case 170: return EBUSY;
    case 183: case 80: return EEXIST;
    case 1200: return ENODEV;
    case 4: return EMFILE;
    case 112: return ENOSPC;
    case 109: return EPIPE;
    case 1131: return EDEADLK;
    case 145: return ENOTEMPTY;
    case 10: return E2BIG;
    case 128: case 129: return ECHILD;
    case 89: case 164: case 215: return EAGAIN;
    default: return EINVAL;
    }
}

/* _isatty (file.c): the descriptor is open and its wxflag says a character device */
int fnvnx_isatty(int fd) { return fd >= 0 && fd < MAX_FD && (g_info[fd].wxflag & (WX_OPEN | WX_TTY)) == (WX_OPEN | WX_TTY); }

static int fail(int e)
{
    errno = e;
    return -1;
}

/* the open descriptor, or NULL with errno EBADF (file.c: get_ioinfo / handle == INVALID_HANDLE_VALUE) */
static struct fnvnx_ioinfo *fd_get(int fd)
{
    if (fd < 0 || fd >= MAX_FD || !(g_info[fd].wxflag & WX_OPEN) || (HANDLE)(uintptr_t)g_info[fd].handle == INVALID_HANDLE) {
        errno = EBADF;
        return NULL;
    }
    return &g_info[fd];
}
static inline HANDLE fd_handle(const struct fnvnx_ioinfo *d) { return (HANDLE)(uintptr_t)d->handle; }
static inline os_lock_t *fd_lock(const struct fnvnx_ioinfo *d) { return &g_fdlock[d - g_info]; }

/* msvcrt_alloc_fd / msvcrt_set_fd (file.c 553-584): the lowest free descriptor from 3 takes the handle; -1 (errno EMFILE) when the table is full.
 * The table lock is the lock of the descriptor being claimed: claiming goes through one compare-and-swap on handle. */
static int alloc_fd(HANDLE h, unsigned flag)
{
    for (int fd = 3; fd < MAX_FD; fd++) {
        uint32_t expect = 0xFFFFFFFFu;
        if (__atomic_compare_exchange_n(&g_info[fd].handle, &expect, (uint32_t)(uintptr_t)h, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST)) {
            struct fnvnx_ioinfo *i = &g_info[fd];
            i->lookahead[0] = i->lookahead[1] = i->lookahead[2] = '\n';
            i->textmode = 0;
            i->unicode = 0;
            __atomic_store_n(&i->wxflag, (uint8_t)(WX_OPEN | (flag & (WX_APPEND | WX_TEXT | WX_TTY))), __ATOMIC_SEQ_CST);
            return fd;
        }
    }
    errno = EMFILE;
    return -1;
}

/* _get_osfhandle (Wine 9.0 file.c 2082): the descriptor's HANDLE; INVALID_HANDLE_VALUE with EBADF when it has none */
intptr_t fnvnx_get_osfhandle(int fd)
{
    if (fd < 0 || fd >= MAX_FD) {
        errno = EBADF;
        return -1;
    }
    HANDLE h = fd_handle(&g_info[fd]);
    if (h == INVALID_HANDLE)
        errno = EBADF;
    return (intptr_t)h;
}

/* _open_osfhandle (Wine 9.0 file.c 2660): oflags _O_TEXT 0x4000, _O_APPEND 0x8, _O_BINARY 0x8000 select the mode; no mode means binary */
#define MSVCRT_O_APPEND 0x0008
#define MSVCRT_O_TEXT 0x4000
#define MSVCRT_O_BINARY 0x8000
#define FILE_TYPE_UNKNOWN 0
#define FILE_TYPE_CHAR 2
int fnvnx_open_osfhandle(intptr_t handle, int oflags)
{
    if (!(oflags & (MSVCRT_O_BINARY | MSVCRT_O_TEXT)))
        oflags |= MSVCRT_O_BINARY;
    unsigned type = GetFileType((HANDLE)handle);
    if (type == FILE_TYPE_UNKNOWN && GetLastError() != 0)
        return fail(win_errno(GetLastError()));
    unsigned flags = type == FILE_TYPE_CHAR ? WX_TTY : 0;
    if (oflags & MSVCRT_O_APPEND)
        flags |= WX_APPEND;
    if (oflags & MSVCRT_O_TEXT)
        flags |= WX_TEXT;
    return alloc_fd((HANDLE)handle, flags);
}

/* raw write of the whole buffer (append mode: at the end): WriteFile on the descriptor's handle; the Win32 error is left in the return as -err */
static int64_t raw_write(struct fnvnx_ioinfo *d, const void *p, size_t n)
{
    HANDLE h = fd_handle(d);
    if (d->wxflag & WX_APPEND) {
        long long np;
        SetFilePointerEx(h, 0, &np, 2 /* FILE_END */);
    }
    size_t done = 0;
    while (done < n) {
        unsigned w = 0;
        if (!WriteFile(h, (const char *)p + done, (unsigned)(n - done), &w, NULL))
            return -(int64_t)GetLastError();
        if (!w)
            return -(int64_t)112; /* ERROR_DISK_FULL */
        done += w;
    }
    return (int64_t)done;
}

/* errno of a failed write (Wine file.c _write: ERROR_ACCESS_DENIED is EBADF there, every other error msvcrt_set_errno) */
static int write_errno(int64_t neg) { return neg == -5 ? EBADF : win_errno((unsigned)-neg); }

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
    struct fnvnx_ioinfo *d = fd_get(fd);
    if (!d)
        return -1;
    if (!buf && n)
        return fail(EINVAL);
    os_lock(fd_lock(d));
    int64_t r = 0;
    if (!(d->wxflag & WX_TEXT) || (d->wxflag & WX_TTY)) {
        r = raw_write(d, buf, n);
    } else {
        const unsigned char *in = (const unsigned char *)buf;
        unsigned char tmp[2048];
        for (size_t i = 0; i < n && r >= 0;) {
            size_t chunk = n - i < sizeof tmp / 2 ? n - i : sizeof tmp / 2;
            size_t l = lf_to_crlf(in + i, chunk, tmp);
            r = raw_write(d, tmp, l);
            i += chunk;
        }
    }
    os_unlock(fd_lock(d));
    if (r < 0)
        return fail(write_errno(r));
    return (int)n;
}

long long fnvnx_lseeki64(int fd, long long off, int whence)
{
    struct fnvnx_ioinfo *d = fd_get(fd);
    if (!d)
        return -1;
    if (whence < 0 || whence > 2)
        return fail(EINVAL);
    os_lock(fd_lock(d));
    long long pos = 0;
    int ok = SetFilePointerEx(fd_handle(d), off, &pos, (unsigned)whence);   /* FILE_BEGIN / CURRENT / END are 0 / 1 / 2 */
    unsigned err = ok ? 0 : GetLastError();
    if (ok)
        d->wxflag &= (uint8_t)~WX_ATEOF;
    os_unlock(fd_lock(d));
    return ok ? pos : fail(win_errno(err));
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
    /* _file = MSVCRT_NO_CONSOLE_FD: file.c msvcrt_init_io sets it for a standard stream whose handle is MSVCRT_NO_CONSOLE (descriptors 0-2 above) */
    { NULL, 0, NULL, FNVNX_IOREAD, MSVCRT_NO_CONSOLE_FD, 0, 0, NULL },
    { NULL, 0, NULL, FNVNX_IOWRT, MSVCRT_NO_CONSOLE_FD, 0, 0, NULL },
    { NULL, 0, NULL, FNVNX_IOWRT, MSVCRT_NO_CONSOLE_FD, 0, 0, NULL },
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
    struct fnvnx_ioinfo *d = fd_get(x->fd);
    int64_t r = d ? raw_write(d, x->wbuf, x->wlen) : -6 /* ERROR_INVALID_HANDLE */;
    x->wlen = 0;
    if (r < 0) {
        set_err(f);
        errno = d ? write_errno(r) : EBADF;
        return -1;
    }
    return 0;
}

/* drops read-ahead (and pushed-back characters), moving the descriptor back to the logical position */
static void rdrop(struct sext *x, struct fnvnx_ioinfo *d)
{
    if (x->rlen > x->rpos && d && !(d->wxflag & WX_TTY))
        SetFilePointerEx(fd_handle(d), -(long long)(x->rlen - x->rpos), NULL, 1 /* FILE_CURRENT */);
    x->rpos = x->rlen = 0;
    x->nungot = 0;
}

static int begin_read(struct _iobuf *f, struct sext *x, struct fnvnx_ioinfo **pd)
{
    struct fnvnx_ioinfo *d = fd_get(x->fd);
    if (!d) {
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

static int begin_write(struct _iobuf *f, struct sext *x, struct fnvnx_ioinfo **pd)
{
    struct fnvnx_ioinfo *d = fd_get(x->fd);
    if (!d) {
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
static int rfill(struct _iobuf *f, struct sext *x, struct fnvnx_ioinfo *d)
{
    if (d->wxflag & WX_TTY) /* a character device (the standard streams) has no data to refill from */
        return 0;
    if (!x->rbuf && !(x->rbuf = (unsigned char *)malloc(BUFSZ))) {
        set_err(f);
        errno = ENOMEM;
        return -1;
    }
    unsigned got = 0;
    int ok = ReadFile(fd_handle(d), x->rbuf, BUFSZ, &got, NULL);
    unsigned err = ok ? 0 : GetLastError();
    x->rpos = 0;
    x->rlen = ok ? got : 0;
    if (!ok && err != 109 /* ERROR_BROKEN_PIPE: end of file */) {
        set_err(f);
        errno = err == 5 ? EBADF : win_errno(err);   /* file.c _read: ERROR_ACCESS_DENIED is EBADF */
        return -1;
    }
    return ok && got > 0;
}

/* one logical character, or EOF (end of file / Ctrl-Z / error) */
static int getch(struct _iobuf *f, struct sext *x, struct fnvnx_ioinfo *d)
{
    if (x->nungot)
        return x->ungot[--x->nungot];
    if (d->wxflag & WX_ATEOF)
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
    if (!(d->wxflag & WX_TEXT)) {
        x->rpos++;
        return c;
    }
    if (c == CTRLZ) { /* rpos stays on the Ctrl-Z, so ftell reports its offset */
        d->wxflag |= WX_ATEOF;
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
static size_t sread(struct _iobuf *f, struct sext *x, struct fnvnx_ioinfo *d, unsigned char *out, size_t n)
{
    size_t o = 0;
    while (o < n && x->nungot)
        out[o++] = x->ungot[--x->nungot];
    if (!(d->wxflag & WX_TEXT)) {
        while (o < n) {
            if (x->rpos < x->rlen) {
                size_t k = x->rlen - x->rpos < n - o ? x->rlen - x->rpos : n - o;
                memcpy(out + o, x->rbuf + x->rpos, k);
                x->rpos += k;
                o += k;
                continue;
            }
            if (n - o >= BUFSZ && !(d->wxflag & WX_TTY)) { /* large binary read: straight from the file */
                unsigned got = 0;
                int ok = ReadFile(fd_handle(d), out + o, (unsigned)(n - o), &got, NULL);
                unsigned err = ok ? 0 : GetLastError();
                int64_t r = ok ? (int64_t)got : (err == 109 ? 0 : -1);
                if (r < 0) {
                    set_err(f);
                    errno = err == 5 ? EBADF : win_errno(err);
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
        if (x->rpos < x->rlen && !(d->wxflag & WX_ATEOF)) {
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

static int put_raw(struct _iobuf *f, struct sext *x, struct fnvnx_ioinfo *d, const unsigned char *p, size_t n)
{
    if (d->wxflag & WX_TTY) { /* a character device: unbuffered, unchanged */
        int64_t w = raw_write(d, p, n);
        if (w != (int64_t)n) {
            set_err(f);
            errno = w < 0 ? write_errno(w) : ENOSPC;
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

static int swrite(struct _iobuf *f, struct sext *x, struct fnvnx_ioinfo *d, const unsigned char *p, size_t n)
{
    if (!(d->wxflag & WX_TEXT) || (d->wxflag & WX_TTY))
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
    struct fnvnx_ioinfo *d;
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
    struct fnvnx_ioinfo *d;
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

