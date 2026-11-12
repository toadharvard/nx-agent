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

/* ---- S4b: CRT functions identified from the decomp's calls (crt_symbols.tsv) */
unsigned long fnvnx_byteswap_ulong(unsigned long);         /* _byteswap_ulong (0x00ec3bad) */
unsigned short fnvnx_byteswap_ushort(unsigned short);      /* _byteswap_ushort (0x00ec3bda) */
unsigned long long fnvnx_byteswap_uint64(unsigned long long); /* _byteswap_uint64 (0x00ec3bf0) */
char *fnvnx_mbstok(char *, const char *);                  /* _mbstok: its own per-thread position */
unsigned char *_mbsnbcpy(unsigned char *, const unsigned char *, size_t); /* single-byte code page */
unsigned char *_mbsncpy(unsigned char *, const unsigned char *, size_t);
int _mbsnbcmp(const unsigned char *, const unsigned char *, size_t);
int _mbsnbicmp(const unsigned char *, const unsigned char *, size_t);
int fnvnx_wcsicmp16(const unsigned short *, const unsigned short *); /* _wcsicmp on 16-bit wchar_t */
int fnvnx_wcscmp16(const unsigned short *, const unsigned short *);

/* ---- S4b: MSVC 2008 C++ library (libcpmt) internals, object as first argument, MSVC object layouts
 * (crt/cpplib.cpp, layouts in crt/fnvnx_msvc_cpp.h). Constructors return the object, as MSVC's do. */
void *fnvnx_exception_ctor(void *self);                                /* std::exception::exception() */
void *fnvnx_exception_ctor_str(void *self, const char *const *what);  /* exception(const char *const &) */
void *fnvnx_exception_ctor_noalloc(void *self, const char *const *what, int);
void *fnvnx_exception_copy_ctor(void *self, const void *other);
void *fnvnx_exception_assign(void *self, const void *other);
void fnvnx_exception_dtor(void *self);
const char *fnvnx_exception_what(const void *self);
void *fnvnx_bad_cast_ctor(void *self, const char *msg);
void *fnvnx_bad_alloc_ctor(void *self, const char *msg);              /* std::bad_alloc::bad_alloc(const char *) */               /* std::bad_cast::bad_cast(const char *) */
void *fnvnx_mutex_ctor(void *self);                                    /* std::_Mutex */
void fnvnx_mutex_dtor(void *self);
void fnvnx_mutex_lock(void *self);
void fnvnx_mutex_unlock(void *self);
void *fnvnx_init_locks_ctor(void *self);                               /* std::_Init_locks */
void fnvnx_init_locks_dtor(void *self);
void *fnvnx_locale_init(void);                                         /* locale::_Init -> _Locimp* */
void *fnvnx_locale_getgloballocale(void);                              /* locale::_Getgloballocale */
void fnvnx_facet_register(void *facet);                                /* locale::facet::_Facet_Register */
void fnvnx_facet_incref(void *facet);                                  /* locale::facet::_Incref */
size_t fnvnx_cpplib_facet_count(void);
void fnvnx_cpplib_facet_tidy(void);
void fnvnx_locinfo_ctor(void *self, const void *locname_str);         /* _Locinfo::_Locinfo_ctor(this, const string&) */
void fnvnx_locinfo_dtor(void *self);                                   /* _Locinfo::_Locinfo_dtor */
typedef struct {                                                       /* _Ctypevec, 16 bytes on ILP32 */
    uint32_t _Hand;
    uint32_t _Page;
    const short *_Table;
    int _Delfl;
} fnvnx_ctypevec;
fnvnx_ctypevec fnvnx_getctype(void);                                   /* _Getctype (returned by value) */
int fnvnx_tolower_ctype(int c, const fnvnx_ctypevec *);                /* _Tolower */
int fnvnx_toupper_ctype(int c, const fnvnx_ctypevec *);                /* _Toupper */
const short *fnvnx_msvc_ctype_table(void);                             /* MSVC "C" locale _pctype[0..255] */
void fnvnx_ios_base_addstd(void *ios);                                 /* ios_base::_Addstd */
void fnvnx_ios_base_dtor(void *ios);                                   /* ios_base::_Ios_base_dtor */
void *fnvnx_ostream_ctor(void *self, void *strbuf, int isstd, int construct_vbase); /* basic_ostream<char> */
void fnvnx_basic_ios_init(void *self, void *strbuf, int isstd);        /* basic_ios<char>::init */
void *fnvnx_fiopen(const char *name, int mode, int prot);              /* std::_Fiopen -> struct _iobuf * */

/* ---- S4b: _CxxThrowException data (MSVC x86 EH tables in the image: absolute 32-bit addresses) */
typedef struct {
    uint32_t attributes;
    uint32_t pmfnUnwind;          /* destructor of the thrown object (thiscall) */
    uint32_t pForwardCompat;
    uint32_t pCatchableTypeArray;
} fnvnx_msvc_throw_info;
typedef struct {
    uint32_t properties;          /* 1 simple type, 2 by-reference only, 4 has virtual base */
    uint32_t pType;               /* TypeDescriptor */
    int32_t mdisp, pdisp, vdisp;  /* PMD: displacement of this base in the thrown object */
    int32_t sizeOrOffset;         /* size of the object */
    uint32_t copyFunction;        /* copy constructor (thiscall), 0 = bitwise */
} fnvnx_msvc_catchable_type;
typedef struct {
    int32_t nCatchableTypes;      /* the most-derived type first */
    uint32_t arrayOfCatchableTypes[1];
} fnvnx_msvc_catchable_type_array;
typedef struct {
    uint32_t pVFTable;            /* type_info's vftable */
    uint32_t spare;
    char name[1];                 /* ".?AVbad_alloc@std@@" */
} fnvnx_msvc_type_descriptor;
typedef void (*fnvnx_eh_copy_fn)(void *dst, const void *src);
typedef void (*fnvnx_eh_dtor_fn)(void *obj);
/* "this ThrowInfo throws MSVC's std::X" (TypeDescriptor name ".?AVbad_alloc@std@@", ...): no image needed */
int fnvnx_eh_register_msvc_std(const void *throw_info, const char *msvc_type_name);
/* registers the decomp's two standard-exception ThrowInfos (0x01140ef8 bad_alloc, 0x01140cb0 length_error) */
void fnvnx_eh_register_fnv_known(void);
/* code addresses in the image's EH tables (x86 .text) -> native functions (S2 address map / S3 loader) */
void fnvnx_eh_set_code_resolver(void *(*resolve)(uint32_t va));
/* where ThrowInfo / CatchableType / TypeDescriptor pointers may point (the data image); default 0x10000.. */
void fnvnx_eh_set_image_range(uint32_t lo, uint32_t hi);
const char *fnvnx_eh_describe(const void *throw_info);

#ifdef __cplusplus
}
#include <new>
#include <typeinfo>
/* RTTI bridge: the MSVC TypeDescriptor address (data image) of a class -> its GCC type_info (S2 generates the
 * table from the class names: ??_R0?AVTESForm@@@8 <-> _ZTI7TESForm). _CxxThrowException uses it too. */
extern "C++" void fnvnx_rtti_register(const void *msvc_type_descriptor, const std::type_info *ti);
extern "C++" const std::type_info *fnvnx_rtti_lookup(const void *msvc_type_descriptor);
/* native-type path of _CxxThrowException: objects thrown with this ThrowInfo are GCC objects of type ti */
extern "C" void fnvnx_eh_register_throw(const void *throw_info, const std::type_info *ti, size_t size,
                                        fnvnx_eh_copy_fn copy, fnvnx_eh_dtor_fn dtor);
template <class T> inline void fnvnx_eh_register_type(const void *throw_info)
{
    fnvnx_eh_register_throw(
        throw_info, &typeid(T), sizeof(T), [](void *d, const void *s) { new (d) T(*(const T *)s); },
        [](void *p) { ((T *)p)->~T(); });
}
/* what fnvnx_cxx_throw_exception throws when the ThrowInfo is unknown: catch (...) sees it, typed handlers do not.
 * object points into the thrower's (unwound) frame: valid only for its address. */
struct fnvnx_msvc_exception {
    void *object;
    void *throw_info;
};
#endif
#endif
