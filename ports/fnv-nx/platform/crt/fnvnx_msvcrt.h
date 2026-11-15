/* fnvnx_msvcrt.h - MSVC 2008 CRT functions the game expects and newlib/glibc lack or name differently.
 *
 * Standard C functions (memcpy, strlen, sin, qsort, ...) come from the target libc. Functions whose MSVC
 * behaviour differs from the libc function of the same name, or that would collide with it, have an fnvnx_
 * name here (the exports of the msvcrt table, msvcrt_names.tsv). No exe address is bound to them by name.
 * Types follow the MSVC x86 ABI where they reach game data (errno_t = int, struct _stat64i32 layout). */
#ifndef FNVNX_MSVCRT_H
#define FNVNX_MSVCRT_H

#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#include "fnvnx_callback.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef int errno_t;
#ifndef _TRUNCATE
#define _TRUNCATE ((size_t)-1)
#endif
#define FNVNX_EINVAL 22
#define FNVNX_ERANGE 34
#define FNVNX_STRUNCATE 80

/* ---- strings */
int _stricmp(const char *, const char *);
int _strnicmp(const char *, const char *, size_t);
char *_strdup(const char *);
size_t fnvnx_wcslen16(const unsigned short *);
char *_itoa(int, char *, int);
char *_ultoa(unsigned long, char *, int);

/* ---- formatted output: MSVC semantics */
int _snprintf(char *, size_t, const char *, ...);          /* no terminator when full; -1 on truncation */
int _vsnprintf(char *, size_t, const char *, va_list);

/* ---- the one formatter and the one scanner (msvcrt_format.cpp), parameterised by an argument source.
 * *_format / *_scan read a native va_list; *_words / fnvnx_msvcrt_va_* read the x86 stack words a translated
 * caller passes (32-bit words in memory order: int and pointers one word, long long and double two words, low
 * first; a "va_list" coming from x86 code is a pointer to such words). *_format writes at most n characters and
 * returns the full length (-1: a %S/%ls string that does not convert); fnvnx_msvc_bound applies the _snprintf
 * rules to that. */
int fnvnx_msvc_format(char *buf, size_t n, const char *fmt, va_list ap);
int fnvnx_msvc_bound(char *buf, size_t n, int len);
int fnvnx_msvc_scan(const char *in, const char *fmt, va_list ap);
double fnvnx_msvc_strtod(const char *s, char **end);
int fnvnx_msvcrt_va_sprintf(char *dst, const char *fmt, const uint32_t *args);
int fnvnx_msvcrt_va_vsnprintf(char *dst, size_t count, const char *fmt, const uint32_t *args);
int fnvnx_msvcrt_va_snprintf(char *dst, size_t count, const char *fmt, const uint32_t *args);
int fnvnx_msvcrt_va_sscanf(const char *src, const char *fmt, const uint32_t *args);
int fnvnx_msvcrt_wc_to_byte(unsigned short wc, unsigned char *out);   /* current code page; 1 when it converts */
int fnvnx_msvcrt_byte_to_wc(unsigned char b, unsigned short *out);

int fnvnx_access(const char *, int);
#define _MCW_EM 0x0008001F
#define _MCW_RC 0x00000300
#define _MCW_PC 0x00030000
#define _MCW_DN 0x03000000
#define _RC_NEAR 0x00000000
#define _RC_DOWN 0x00000100
#define _RC_UP 0x00000200
#define _RC_CHOP 0x00000300
#define _PC_64 0x00000000
#define _PC_53 0x00010000
#define _PC_24 0x00020000
#define _EM_INEXACT 0x00000001
#define _EM_UNDERFLOW 0x00000002
#define _EM_OVERFLOW 0x00000004
#define _EM_ZERODIVIDE 0x00000008
#define _EM_INVALID 0x00000010
#define _EM_DENORMAL 0x00080000
#define _DN_SAVE 0x00000000
#define _DN_FLUSH 0x01000000
#define _CW_DEFAULT (_RC_NEAR + _PC_53 + _EM_INVALID + _EM_ZERODIVIDE + _EM_OVERFLOW + _EM_UNDERFLOW + _EM_INEXACT + _EM_DENORMAL)

#define _HEAPOK (-2)

/* ---- process / runtime support */
int *_errno(void);
/* atexit / _onexit / doexit / exit / _cexit / _c_exit: the exit-handler list of exitlist.cpp (handlers are game function
 * pointers, run through the boundary's guest_invoke). fnvnx_exit_register: 0, or -1 for a NULL handler / no memory. */
int fnvnx_exit_register(uintptr_t handler);
FNVNX_CALLBACK_T(fnvnx_atexit_fn, void, (void));
FNVNX_CALLBACK_T(_onexit_t, int, (void));
void fnvnx_doexit(int code, int quick, int retcaller);
int fnvnx_rand(void);                        /* MSVC LCG, RAND_MAX 0x7FFF, per thread */
void fnvnx_srand(unsigned int);
#define FNVNX_MSVC_RAND_MAX 0x7FFF



/* std::bad_cast::bad_cast(const char *) */
typedef struct {                                                       /* _Ctypevec, 16 bytes on ILP32 */
    uint32_t _Hand;
    uint32_t _Page;
    const short *_Table;
    int _Delfl;
} fnvnx_ctypevec;

#ifdef __cplusplus
}
#endif
#endif
