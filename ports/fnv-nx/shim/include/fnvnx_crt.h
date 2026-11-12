/* fnvnx_crt.h - the MSVC CRT functions the decomp uses that the target C library (newlib / glibc) lacks, as
 * declarations with their MSVC prototypes (size_t is the target's). Implementations live in the platform layer
 * (stream S4). Included by prepare.py rule DECL1 where it drops the decomp's own prototypes of these functions.
 */
#ifndef FNVNX_CRT_H
#define FNVNX_CRT_H

#include <stddef.h>
#include <stdarg.h>

#ifdef __cplusplus
extern "C" {
#endif

int _stricmp(const char *, const char *);
int _strnicmp(const char *, const char *, size_t);
int stricmp(const char *, const char *);
int strnicmp(const char *, const char *, size_t);
char *_strdup(const char *);
int _snprintf(char *, size_t, const char *, ...);
int _vsnprintf(char *, size_t, const char *, va_list);
size_t _msize(void *);

/* secure CRT (errno_t is int); the decomp spells the sizes unsigned int (MSVC x86 size_t) */
int strcpy_s(char *, size_t, const char *);
int strcat_s(char *, size_t, const char *);
int strncpy_s(char *, size_t, const char *, size_t);
int sprintf_s(char *, size_t, const char *, ...);
int vsprintf_s(char *, size_t, const char *, va_list);
int memcpy_s(void *, size_t, const void *, size_t);
int memmove_s(void *, size_t, const void *, size_t);
char *strtok_s(char *, const char *, char **);
int _splitpath_s(const char *, char *, size_t, char *, size_t, char *, size_t, char *, size_t);
int _strlwr_s(char *, size_t);

/* <float.h> / <stdlib.h> MSVC-only functions (platform/crt/fnvnx_msvcrt.h) */
int _finite(double);
int _isnan(double);
char *_gcvt(double, int, char *);

/* compiler/CRT runtime entry points that only MSVC-built code references by name */
typedef struct _ThrowInfo _ThrowInfo;
void _CxxThrowException(void *, _ThrowInfo *);
void _invalid_parameter_noinfo(void);

#ifdef __cplusplus
}
#endif
#endif
