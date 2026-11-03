/* fnvnx_msvcrt.h - MSVC 2008 CRT functions the game expects and newlib/glibc lack or name differently.
 *
 * Standard C functions (memcpy, strlen, sin, qsort, ...) come from the target libc. Functions whose MSVC
 * behaviour differs from the libc function of the same name, or that would collide with it, have an fnvnx_
 * name here; the link step (S2) maps the original CRT address to that name (platform/crt/crt_symbols.tsv).
 * Types follow the MSVC x86 ABI where they reach game data (errno_t = int, struct _stat64i32 layout). */
#ifndef FNVNX_MSVCRT_H
#define FNVNX_MSVCRT_H

#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

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
int _strcmpi(const char *, const char *);
int _stricoll(const char *, const char *);
int _mbscmp(const unsigned char *, const unsigned char *);
int _mbsicmp(const unsigned char *, const unsigned char *);
int _mbsnicmp(const unsigned char *, const unsigned char *, size_t);
char *_strlwr(char *);
char *_strupr(char *);
errno_t _strlwr_s(char *, size_t);
errno_t _strupr_s(char *, size_t);
char *_strdup(const char *);
errno_t strcpy_s(char *, size_t, const char *);
errno_t strcat_s(char *, size_t, const char *);
errno_t strncpy_s(char *, size_t, const char *, size_t);
errno_t strncat_s(char *, size_t, const char *, size_t);
char *strtok_s(char *, const char *, char **);
errno_t memcpy_s(void *, size_t, const void *, size_t);
errno_t memmove_s(void *, size_t, const void *, size_t);
errno_t wcstombs_s(size_t *, char *, size_t, const unsigned short *, size_t); /* wchar_t is 16-bit (-fshort-wchar) */
size_t fnvnx_wcslen16(const unsigned short *);
char *_itoa(int, char *, int);
char *_ltoa(long, char *, int);
char *_ultoa(unsigned long, char *, int);
char *_i64toa(long long, char *, int);
char *_ui64toa(unsigned long long, char *, int);
errno_t _itoa_s(int, char *, size_t, int);
errno_t _ltoa_s(long, char *, size_t, int);
errno_t _ultoa_s(unsigned long, char *, size_t, int);
long long _atoi64(const char *);
long long _strtoi64(const char *, char **, int);
unsigned long long _strtoui64(const char *, char **, int);
char *_gcvt(double, int, char *);
void _splitpath(const char *, char *, char *, char *, char *);
errno_t _splitpath_s(const char *, char *, size_t, char *, size_t, char *, size_t, char *, size_t);
void _makepath(char *, const char *, const char *, const char *, const char *);
errno_t _makepath_s(char *, size_t, const char *, const char *, const char *, const char *);

/* ---- formatted output: MSVC semantics */
int _snprintf(char *, size_t, const char *, ...);          /* no terminator when full; -1 on truncation */
int _vsnprintf(char *, size_t, const char *, va_list);
int sprintf_s(char *, size_t, const char *, ...);          /* "" and -1 (invalid parameter) on overflow */
int vsprintf_s(char *, size_t, const char *, va_list);
int _snprintf_s(char *, size_t, size_t, const char *, ...);
int _vsnprintf_s(char *, size_t, size_t, const char *, va_list);
int _scprintf(const char *, ...);
int _vscprintf(const char *, va_list);

/* ---- files: Windows paths through the runtime's path mapper. _access/_mkdir/_rmdir/_unlink/_getcwd/_chdir
 * carry an fnvnx_ name: newlib uses some underscore names (_unlink, _stat) for its system-call layer. */
FILE *fnvnx_fopen(const char *path, const char *mode);
errno_t fnvnx_fopen_s(FILE **, const char *, const char *);
FILE *fnvnx_fsopen(const char *, const char *, int);
int fnvnx_remove(const char *);
int fnvnx_rename(const char *, const char *);
int fnvnx_access(const char *, int);
errno_t fnvnx_access_s(const char *, int);
int fnvnx_mkdir(const char *);
int fnvnx_rmdir(const char *);
int fnvnx_unlink(const char *);
char *fnvnx_getcwd(char *, int);
int fnvnx_chdir(const char *);
int _fseeki64(FILE *, long long, int);
long long _ftelli64(FILE *);
int fnvnx_fileno(FILE *);
/* struct _stat as compiled by MSVC 2008 (64-bit time_t, 32-bit size: _stat64i32), 48 bytes */
struct fnvnx_msvc_stat {
    uint32_t st_dev;
    uint16_t st_ino, st_mode;
    int16_t st_nlink, st_uid, st_gid;
    uint16_t pad0_;
    uint32_t st_rdev;
    int32_t st_size;
    int64_t st_atime_, st_mtime_, st_ctime_;
} __attribute__((aligned(8)));
int fnvnx_stat(const char *, struct fnvnx_msvc_stat *);

/* ---- math / floating point */
int _finite(double);
int _isnan(double);
double _chgsign(double);
double _copysign(double, double);
double _hypot(double, double);
float _hypotf(float, float);
double _logb(double);
double _nextafter(double, double);
double _scalb(double, long);
unsigned int _controlfp(unsigned int newval, unsigned int mask);
unsigned int _control87(unsigned int newval, unsigned int mask);
errno_t _controlfp_s(unsigned int *cur, unsigned int newval, unsigned int mask);
unsigned int _clearfp(void);
unsigned int _statusfp(void);
void _fpreset(void);
long long fnvnx_ftol2(double);                              /* _ftol2/_ftol2_sse with the operand passed */
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

/* ---- heap */
size_t _msize(void *);
void *_aligned_malloc(size_t, size_t);
void *_aligned_realloc(void *, size_t, size_t);
void _aligned_free(void *);
size_t _aligned_msize(void *, size_t, size_t);
void *_aligned_offset_malloc(size_t, size_t, size_t);
void *_expand(void *, size_t);
int _heapchk(void);
#define _HEAPOK (-2)
void *_recalloc(void *, size_t, size_t);

/* ---- process / runtime support */
int *_errno(void);
void _invalid_parameter_noinfo(void);
void _purecall(void);
int fnvnx_atexit(void (*)(void));
void fnvnx_doexit(int code, int quick, int retcaller);
unsigned int fnvnx_clock(void);             /* clock(): CLOCKS_PER_SEC 1000, wall time since start */
int fnvnx_rand(void);                        /* MSVC LCG, RAND_MAX 0x7FFF, per thread */
void fnvnx_srand(unsigned int);
long long _time64(long long *);
#define FNVNX_MSVC_RAND_MAX 0x7FFF

/* ---- compiler helpers referenced explicitly by decomp source (crt/helpers.cpp) */
void fnvnx_security_check_cookie(uintptr_t cookie);         /* __security_check_cookie: no /GS here */
void fnvnx_eh_vector_ctor(void *ptr, size_t size, int count, void (*ctor)(void *), void (*dtor)(void *));
void fnvnx_eh_vector_dtor(void *ptr, size_t size, int count, void (*dtor)(void *));
void fnvnx_eh_vector_copy_ctor(void *dst, void *src, size_t size, int count, void (*cctor)(void *, void *),
                               void (*dtor)(void *));
void *fnvnx_rt_dynamic_cast(void *inptr, long vfdelta, void *src_type, void *target_type, int is_reference);
void fnvnx_cxx_throw_exception(void *obj, void *throw_info) __attribute__((noreturn));
/* MSVC std::_Lockit(int kind) / ~_Lockit as thiscall-shaped functions (self = 4-byte _Lockit) */
void fnvnx_lockit_ctor(void *self, int kind);
void fnvnx_lockit_dtor(void *self);
void fnvnx_xlength_error(void) __attribute__((noreturn));  /* std::_Xlen: length_error("string too long") */
void fnvnx_xout_of_range(void) __attribute__((noreturn));  /* std::_Xran: out_of_range("invalid string position") */

#ifdef __cplusplus
}
#include <typeinfo>
/* RTTI bridge: the MSVC TypeDescriptor address (data image) of a class -> its GCC type_info (S2 generates the
 * table from the class names: ??_R0?AVTESForm@@@8 <-> _ZTI7TESForm). */
extern "C++" void fnvnx_rtti_register(const void *msvc_type_descriptor, const std::type_info *ti);
/* what fnvnx_cxx_throw_exception throws for a non-null object: catch(...) sees it, typed handlers do not */
struct fnvnx_msvc_exception {
    void *object;
    void *throw_info;
};
#endif
#endif
