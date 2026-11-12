/* sysinfo.cpp - kernel32 system information: the Windows version (ENV_TRUTH P4), the system and Windows
 * directories (P5), the processor feature table, the error mode, DBCS lead bytes (L1: code page 1252 has none) and
 * environment expansion over the process environment block (crt_kernel32.cpp). */
#include "w32.h"

#include <stdlib.h>

/* ---- directories (P5): the one source; the environment block derives its paths from here */

/* GetSystemDirectory / GetWindowsDirectory: the length without the NUL when the string fits with its NUL, else the
 * size needed with it */
static UINT dir_out(const char *d, LPSTR buf, UINT n)
{
    UINT len = (UINT)strlen(d);
    n = W32_DW(n);
    if (!buf || n < len + 1)
        return len + 1;
    memcpy(buf, d, len + 1);
    return len;
}

extern "C" UINT GetSystemDirectoryA(LPSTR buf, UINT n) { return dir_out(W32_SYS_DIR, buf, n); }
extern "C" UINT GetWindowsDirectoryA(LPSTR buf, UINT n) { return dir_out(W32_WIN_DIR, buf, n); }

/* ---- version: Windows 7 SP1 */
struct w32_osvi { DWORD dwOSVersionInfoSize, dwMajorVersion, dwMinorVersion, dwBuildNumber, dwPlatformId; char szCSDVersion[128]; };
struct w32_osviex { w32_osvi b; WORD wServicePackMajor, wServicePackMinor, wSuiteMask; BYTE wProductType, wReserved; };

enum { VER_MAJOR = 6, VER_MINOR = 1, VER_BUILD = 7601, VER_PLATFORM_NT = 2, VER_SUITE_SINGLEUSERTS = 0x100, VER_NT_WORKSTATION = 1 };

extern "C" BOOL GetVersionExA(void *p)
{
    if (!p) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    w32_osvi *v = (w32_osvi *)p;
    DWORD size = W32_DW(v->dwOSVersionInfoSize);
    if (size != sizeof(w32_osvi) && size != sizeof(w32_osviex)) {
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return FALSE;
    }
    v->dwMajorVersion = VER_MAJOR;
    v->dwMinorVersion = VER_MINOR;
    v->dwBuildNumber = VER_BUILD;
    v->dwPlatformId = VER_PLATFORM_NT;
    memset(v->szCSDVersion, 0, sizeof v->szCSDVersion);
    strcpy(v->szCSDVersion, "Service Pack 1");
    if (size == sizeof(w32_osviex)) {
        w32_osviex *x = (w32_osviex *)p;
        x->wServicePackMajor = 1;
        x->wServicePackMinor = 0;
        x->wSuiteMask = VER_SUITE_SINGLEUSERTS;
        x->wProductType = VER_NT_WORKSTATION;
        x->wReserved = 0;
    }
    return TRUE;
}

/* low byte major, next byte minor, high word the build (the high bit clear: NT platform) */
extern "C" DWORD GetVersion(void) { return (DWORD)(VER_MAJOR | (VER_MINOR << 8) | ((DWORD)VER_BUILD << 16)); }

/* ---- processor features: the reference CPU's table (indices of the PF_* constants); everything else is absent.
 * Present: PF_COMPARE_EXCHANGE_DOUBLE 2, PF_MMX_INSTRUCTIONS_AVAILABLE 3, PF_XMMI_INSTRUCTIONS_AVAILABLE 6,
 * PF_RDTSC_INSTRUCTION_AVAILABLE 8, PF_PAE_ENABLED 9, PF_XMMI64_INSTRUCTIONS_AVAILABLE 10, PF_NX_ENABLED 12 */
extern "C" const unsigned char fnvnx_pf_table[64] = {
    0, 0, 1, 1, 0, 0, 1, 0, 1, 1, 1, 0, 1,
};

extern "C" BOOL IsProcessorFeaturePresent(DWORD f)
{
    f = W32_DW(f);
    return f < 64 && fnvnx_pf_table[f] ? TRUE : FALSE;
}

/* ---- error mode: process-wide */
static UINT g_error_mode;
extern "C" UINT SetErrorMode(UINT mode)
{
    return (UINT)__atomic_exchange_n(&g_error_mode, (UINT)W32_DW(mode), __ATOMIC_SEQ_CST);
}

/* ---- DBCS: a byte is a lead byte when it lies in a LeadByte range of the ANSI code page's CPINFO */
extern "C" BOOL IsDBCSLeadByte(BYTE c)
{
    CPINFO ci;
    if (!GetCPInfo(0 /* CP_ACP */, &ci))
        return FALSE;
    c = (BYTE)c;
    for (int i = 0; i + 1 < (int)sizeof ci.LeadByte && ci.LeadByte[i]; i += 2)
        if (c >= ci.LeadByte[i] && c <= ci.LeadByte[i + 1])
            return TRUE;
    return FALSE;
}

/* ---- ExpandEnvironmentStringsA: each %NAME% naming a variable of the block becomes its value; a pair whose name is
 * empty or not defined, and a '%' with no closing one, stay as written. The result is the size in characters,
 * NUL included; a destination too small receives nothing */
struct xbuf { char *p; size_t n, cap; bool ok; };
static void xput(xbuf *b, const char *s, size_t n)
{
    if (!b->ok || !n)
        return;
    if (b->n + n + 1 > b->cap) {
        size_t c = (b->n + n + 1) * 2;
        char *q = (char *)realloc(b->p, c);
        if (!q) {
            b->ok = false;
            return;
        }
        b->p = q;
        b->cap = c;
    }
    memcpy(b->p + b->n, s, n);
    b->n += n;
}

extern "C" DWORD GetEnvironmentVariableA(LPCSTR, LPSTR, DWORD);
extern "C" DWORD ExpandEnvironmentStringsA(LPCSTR src, LPSTR dst, DWORD n)
{
    n = W32_DW(n);
    if (!src) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0;
    }
    xbuf b = { NULL, 0, 0, true };
    for (const char *p = src; *p;) {
        const char *close = *p == '%' ? strchr(p + 1, '%') : NULL;
        if (!close) {
            const char *e = *p == '%' ? p + 1 : p;
            while (*e && *e != '%')
                e++;
            xput(&b, p, (size_t)(e - p));   /* literal text, or a lone '%' and what follows it up to the next one */
            p = e;
            continue;
        }
        size_t ln = (size_t)(close - p - 1);
        bool done = false;
        if (ln) {
            char *name = (char *)malloc(ln + 1);
            if (!name) {
                b.ok = false;
                break;
            }
            memcpy(name, p + 1, ln);
            name[ln] = 0;
            SetLastError(ERROR_SUCCESS);
            DWORD need = GetEnvironmentVariableA(name, NULL, 0);
            if (need || GetLastError() != ERROR_ENVVAR_NOT_FOUND) {   /* defined (an empty value has need 1) */
                char *val = (char *)malloc(need + 1);
                if (!val) {
                    free(name);
                    b.ok = false;
                    break;
                }
                DWORD got = GetEnvironmentVariableA(name, val, need + 1);   /* a value that grew meanwhile is cut */
                if (got <= need) {
                    xput(&b, val, got);
                    done = true;
                }
                free(val);
            }
            free(name);
        }
        if (!done)
            xput(&b, p, (size_t)(close - p + 1));
        p = close + 1;
    }
    if (!b.ok) {
        free(b.p);
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        return 0;
    }
    DWORD need = (DWORD)(b.n + 1);
    if (dst && n >= need) {
        if (b.n)
            memcpy(dst, b.p, b.n);
        dst[b.n] = 0;
    }
    free(b.p);
    return need;
}
