/* fnvnx_iobuf.h - the game's FILE: MSVC 2008's struct _iobuf, and the CRT stdio / lowio functions that take it
 * (stream S4b-crt; implementation platform/crt/iob.c).
 *
 * Decision: a FILE* in game code is a pointer to this runtime's struct _iobuf with the MSVC x86 layout (32 bytes
 * on ILP32), never a libc FILE. The decomp declares the CRT file functions with _iobuf*, computes stdout/stderr as
 * __iob_func() + 0x20 / + 0x40, and its copy of MSVC's basic_filebuf::_Init takes &_File->_base/_ptr/_cnt. Here
 * _ptr/_base stay NULL and _cnt 0 (no buffer is exposed), so MSVC's inline streambuf code always calls fgetc /
 * fputc / ungetc; _flag mirrors _IOREAD/_IOWRT/_IORW/_IOEOF/_IOERR and _file is the lowio descriptor.
 *
 * Text mode is MSVC's: a descriptor opened without 'b' (fopen) / without _O_BINARY (_open, default _fmode =
 * _O_TEXT) reads CR LF as LF and stops at Ctrl-Z, and writes LF as CR LF; _setmode switches a descriptor,
 * _fdopen follows the descriptor's mode. ftell/fgetpos/fseek work on raw file offsets, as in MSVC.
 *
 * For S1 (DECL1): include this header where the decomp declares stdio functions with _iobuf (instead of those
 * prototypes). In C++ it includes <cstdio> first, then adds overloads of the standard names that take _iobuf*
 * (C++ linkage, so they coexist with libc's FILE* functions) forwarding to fnvnx_iob_*. fopen cannot be
 * overloaded (same parameters as libc's): define FNVNX_IOBUF_FOPEN before including to map fopen / _fsopen to
 * the _iobuf versions (macro), or call fnvnx_iob_fopen. S2 binds the CRT addresses to the fnvnx_iob_* names
 * (platform/crt/crt_symbols.tsv), so calls that still reach libc names by address land here too. */
#ifndef FNVNX_IOBUF_H
#define FNVNX_IOBUF_H

#include <stdarg.h>
#include <stddef.h>
#ifdef __cplusplus
#include <cstdio>
#include <cwchar> /* glibc's wchar.h names fclose in an attribute: it must see fclose before the overload below */
extern "C" {
#else
#include <stdio.h>
#endif

/* struct _iobuf as MSVC 2008 x86 lays it out (stdio.h); offsets are for ILP32 */
struct _iobuf {
    char *_ptr;      /* 0x00 next character of the buffer - always NULL here */
    int _cnt;        /* 0x04 characters left in the buffer - always 0 */
    char *_base;     /* 0x08 the buffer - always NULL */
    int _flag;       /* 0x0c FNVNX_IOREAD/IOWRT/IORW/IOEOF/IOERR */
    int _file;       /* 0x10 lowio descriptor */
    int _charbuf;    /* 0x14 */
    int _bufsiz;     /* 0x18 */
    char *_tmpfname; /* 0x1c */
};
#ifndef __cplusplus
typedef struct _iobuf _iobuf;
#endif

/* _iobuf._flag bits (MSVC stdio.h) */
#define FNVNX_IOREAD 0x0001
#define FNVNX_IOWRT 0x0002
#define FNVNX_IOMYBUF 0x0008
#define FNVNX_IOEOF 0x0010
#define FNVNX_IOERR 0x0020
#define FNVNX_IOSTRG 0x0040
#define FNVNX_IORW 0x0080

/* lowio open flags and modes (MSVC fcntl.h); the MSVC spellings are added where the libc has none */
#define FNVNX_O_RDONLY 0x0000
#define FNVNX_O_WRONLY 0x0001
#define FNVNX_O_RDWR 0x0002
#define FNVNX_O_APPEND 0x0008
#define FNVNX_O_RANDOM 0x0010
#define FNVNX_O_SEQUENTIAL 0x0020
#define FNVNX_O_TEMPORARY 0x0040
#define FNVNX_O_NOINHERIT 0x0080
#define FNVNX_O_CREAT 0x0100
#define FNVNX_O_TRUNC 0x0200
#define FNVNX_O_EXCL 0x0400
#define FNVNX_O_SHORT_LIVED 0x1000
#define FNVNX_O_TEXT 0x4000
#define FNVNX_O_BINARY 0x8000
#define FNVNX_O_WTEXT 0x10000
#define FNVNX_O_U16TEXT 0x20000
#define FNVNX_O_U8TEXT 0x40000
#ifndef _O_TEXT
#define _O_RDONLY FNVNX_O_RDONLY
#define _O_WRONLY FNVNX_O_WRONLY
#define _O_RDWR FNVNX_O_RDWR
#define _O_APPEND FNVNX_O_APPEND
#define _O_CREAT FNVNX_O_CREAT
#define _O_TRUNC FNVNX_O_TRUNC
#define _O_EXCL FNVNX_O_EXCL
#define _O_TEXT FNVNX_O_TEXT
#define _O_BINARY FNVNX_O_BINARY
#define _O_RAW FNVNX_O_BINARY
#define _O_TEMPORARY FNVNX_O_TEMPORARY
#endif

/* the standard streams (the exported _iob array): __iob_func()[0..2] = stdin, stdout, stderr (32-byte stride on ILP32, as MSVC's _iob) */
#define FNVNX_IOB_COUNT 20 /* MSVCRT.DLL's _iob has 20 entries; [0..2] are the standard streams, the rest stay zero */
extern struct _iobuf fnvnx_iob[FNVNX_IOB_COUNT];
int fnvnx_isatty(int fd);
struct _iobuf *__iob_func(void);

/* ---- stdio on _iobuf (the CRT functions of the same name without the fnvnx_iob_ prefix) */
struct _iobuf *fnvnx_iob_fopen(const char *path, const char *mode);   /* Windows path, MSVC mode string */
int fnvnx_iob_fopen_s(struct _iobuf **f, const char *path, const char *mode);
struct _iobuf *fnvnx_iob_fsopen(const char *path, const char *mode, int shflag);
struct _iobuf *fnvnx_iob_fdopen(int fd, const char *mode);              /* _fdopen */
int fnvnx_iob_fclose(struct _iobuf *f);
int fnvnx_iob_fclose_nolock(struct _iobuf *f);
int fnvnx_iob_fcloseall(void);
size_t fnvnx_iob_fread(void *buf, size_t size, size_t count, struct _iobuf *f);
size_t fnvnx_iob_fwrite(const void *buf, size_t size, size_t count, struct _iobuf *f);
int fnvnx_iob_fgetc(struct _iobuf *f);                                   /* also getc, _fgetc_nolock */
int fnvnx_iob_fputc(int c, struct _iobuf *f);                            /* also putc */
int fnvnx_iob_ungetc(int c, struct _iobuf *f);
char *fnvnx_iob_fgets(char *buf, int n, struct _iobuf *f);
int fnvnx_iob_fputs(const char *s, struct _iobuf *f);
int fnvnx_iob_puts(const char *s);
int fnvnx_iob_fprintf(struct _iobuf *f, const char *fmt, ...);          /* MSVC formats (%I64d ...) */
int fnvnx_iob_vfprintf(struct _iobuf *f, const char *fmt, va_list ap);
int fnvnx_iob_printf(const char *fmt, ...);                              /* to __iob_func()[1] */
int fnvnx_iob_vprintf(const char *fmt, va_list ap);
int fnvnx_iob_fseek(struct _iobuf *f, long off, int whence);
long fnvnx_iob_ftell(struct _iobuf *f);
int fnvnx_iob_fseeki64(struct _iobuf *f, long long off, int whence);
long long fnvnx_iob_ftelli64(struct _iobuf *f);
int fnvnx_iob_fgetpos(struct _iobuf *f, long long *pos);                 /* MSVC fpos_t is __int64 */
int fnvnx_iob_fsetpos(struct _iobuf *f, const long long *pos);
void fnvnx_iob_rewind(struct _iobuf *f);
int fnvnx_iob_feof(struct _iobuf *f);
int fnvnx_iob_ferror(struct _iobuf *f);
void fnvnx_iob_clearerr(struct _iobuf *f);
int fnvnx_iob_fflush(struct _iobuf *f);                                  /* NULL: all streams */
int fnvnx_iob_fileno(struct _iobuf *f);
int fnvnx_iob_setvbuf(struct _iobuf *f, char *buf, int mode, size_t size);

/* ---- lowio (the MSVC names are _open, _sopen, _close, _read, _write, _lseek, ...; newlib uses several of
 * those for its system-call layer, hence the prefix) */
int fnvnx_open(const char *path, int oflag, int pmode);
int fnvnx_sopen(const char *path, int oflag, int shflag, int pmode);
int fnvnx_close(int fd);
int fnvnx_read(int fd, void *buf, unsigned int n);
int fnvnx_write(int fd, const void *buf, unsigned int n);
long fnvnx_lseek(int fd, long off, int whence);
long long fnvnx_lseeki64(int fd, long long off, int whence);
long fnvnx_tell(int fd);
long long fnvnx_telli64(int fd);
long fnvnx_filelength(int fd);
int fnvnx_eof(int fd);
int fnvnx_commit(int fd);
int fnvnx_setmode(int fd, int mode);          /* returns the previous mode (_O_TEXT / _O_BINARY), -1 on error */
int fnvnx_set_fmode(int mode);                /* _set_fmode: default mode of fopen/_open without t/b */
int fnvnx_get_fmode(int *mode);
extern int fnvnx_fmode;                       /* _fmode */

#ifdef __cplusplus
}

#ifndef FNVNX_IOBUF_NO_OVERLOADS
/* The standard names for _iobuf* (C++ overloads next to libc's FILE* functions). */
#ifdef _fileno
#undef _fileno
#endif
inline int fclose(_iobuf *f) { return fnvnx_iob_fclose(f); }
inline int _fclose_nolock(_iobuf *f) { return fnvnx_iob_fclose_nolock(f); }
inline int fopen_s(_iobuf **f, const char *p, const char *m) { return fnvnx_iob_fopen_s(f, p, m); }
inline _iobuf *_fsopen(const char *p, const char *m, int sh) { return fnvnx_iob_fsopen(p, m, sh); }
inline _iobuf *_fdopen(int fd, const char *m) { return fnvnx_iob_fdopen(fd, m); }
inline size_t fread(void *b, size_t s, size_t n, _iobuf *f) { return fnvnx_iob_fread(b, s, n, f); }
inline size_t fwrite(const void *b, size_t s, size_t n, _iobuf *f) { return fnvnx_iob_fwrite(b, s, n, f); }
inline int fgetc(_iobuf *f) { return fnvnx_iob_fgetc(f); }
inline int getc(_iobuf *f) { return fnvnx_iob_fgetc(f); }
inline int fputc(int c, _iobuf *f) { return fnvnx_iob_fputc(c, f); }
inline int putc(int c, _iobuf *f) { return fnvnx_iob_fputc(c, f); }
inline int ungetc(int c, _iobuf *f) { return fnvnx_iob_ungetc(c, f); }
inline char *fgets(char *b, int n, _iobuf *f) { return fnvnx_iob_fgets(b, n, f); }
inline int fputs(const char *s, _iobuf *f) { return fnvnx_iob_fputs(s, f); }
inline int vfprintf(_iobuf *f, const char *fmt, va_list ap) { return fnvnx_iob_vfprintf(f, fmt, ap); }
inline int fprintf(_iobuf *f, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
inline int fprintf(_iobuf *f, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    int r = fnvnx_iob_vfprintf(f, fmt, ap);
    va_end(ap);
    return r;
}
inline int fseek(_iobuf *f, long o, int w) { return fnvnx_iob_fseek(f, o, w); }
inline long ftell(_iobuf *f) { return fnvnx_iob_ftell(f); }
inline int _fseeki64(_iobuf *f, long long o, int w) { return fnvnx_iob_fseeki64(f, o, w); }
inline long long _ftelli64(_iobuf *f) { return fnvnx_iob_ftelli64(f); }
inline int fgetpos(_iobuf *f, long long *p) { return fnvnx_iob_fgetpos(f, p); }
inline int fsetpos(_iobuf *f, const long long *p) { return fnvnx_iob_fsetpos(f, p); }
inline void rewind(_iobuf *f) { fnvnx_iob_rewind(f); }
inline int feof(_iobuf *f) { return fnvnx_iob_feof(f); }
inline int ferror(_iobuf *f) { return fnvnx_iob_ferror(f); }
inline void clearerr(_iobuf *f) { fnvnx_iob_clearerr(f); }
inline int fflush(_iobuf *f) { return fnvnx_iob_fflush(f); }
inline int _fileno(_iobuf *f) { return fnvnx_iob_fileno(f); }
inline int setvbuf(_iobuf *f, char *b, int m, size_t n) { return fnvnx_iob_setvbuf(f, b, m, n); }
#endif
#endif

#ifdef FNVNX_IOBUF_FOPEN
#define fopen fnvnx_iob_fopen
#endif

#endif
