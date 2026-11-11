/* handles.cpp - kernel object handles, CloseHandle, shared helpers. */
#include "w32.h"

#include <stdio.h>
#include <stdlib.h>

struct w32_obj *w32_obj_new(int type, size_t size, void (*destroy)(struct w32_obj *))
{
    struct w32_obj *o = (struct w32_obj *)calloc(1, size);
    if (!o) {
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        return NULL;
    }
    o->magic = W32_MAGIC;
    o->type = type;
    o->refs = 1;
    o->waiters.next = o->waiters.prev = &o->waiters;
    o->destroy = destroy;
    o->clk_t = clk_stamp();
    return o;
}

#define MAX_HANDLES 65535
struct slot { struct w32_obj *o; uint32_t gen; uint32_t next_free; };
static struct slot g_slots[MAX_HANDLES + 1]; /* slot 0 unused */
static uint32_t g_free_head, g_used;
static os_lock_t g_handle_lock;

static inline uint32_t enc(uint32_t idx, uint32_t gen) { return ((gen & 0x3FFFu) << 18) | (idx << 2); }

HANDLE w32_handle_open(struct w32_obj *o)
{
    os_lock(&g_handle_lock);
    uint32_t i = g_free_head;
    if (i)
        g_free_head = g_slots[i].next_free;
    else if (g_used < MAX_HANDLES)
        i = ++g_used;
    if (i) {
        g_slots[i].o = o;
        g_slots[i].gen++;
    }
    uint32_t v = i ? enc(i, g_slots[i].gen) : 0;
    os_unlock(&g_handle_lock);
    if (!i) {
        w32_obj_unref(o);
        SetLastError(ERROR_NOT_ENOUGH_MEMORY); /* ERROR_NO_SYSTEM_RESOURCES on Windows */
        return NULL;
    }
    return (HANDLE)(uintptr_t)v;
}

static uint32_t slot_of(HANDLE h)
{
    uintptr_t v = (uintptr_t)h;
    if (v & 3 || v > 0xFFFFFFFFu)
        return 0;
    uint32_t i = (uint32_t)(v >> 2) & 0xFFFFu;
    if (!i || i > MAX_HANDLES || enc(i, g_slots[i].gen) != (uint32_t)v || !g_slots[i].o)
        return 0;
    return i;
}

struct w32_obj *w32_obj_get(HANDLE h, int type)
{
    os_lock(&g_handle_lock);
    uint32_t i = slot_of(h);
    struct w32_obj *o = i ? g_slots[i].o : NULL;
    os_unlock(&g_handle_lock);
    if (!o || o->magic != W32_MAGIC || (type && o->type != type)) {
        SetLastError(ERROR_INVALID_HANDLE);
        return NULL;
    }
    return o;
}

/* w32_obj_get that takes a reference while the table is locked (a concurrent CloseHandle cannot free the object) */
struct w32_obj *w32_obj_get_ref(HANDLE h, int type)
{
    os_lock(&g_handle_lock);
    uint32_t i = slot_of(h);
    struct w32_obj *o = i ? g_slots[i].o : NULL;
    if (o && o->magic == W32_MAGIC && (!type || o->type == type))
        w32_obj_ref(o);
    else
        o = NULL;
    os_unlock(&g_handle_lock);
    if (!o)
        SetLastError(ERROR_INVALID_HANDLE);
    return o;
}

BOOL w32_handle_close(HANDLE h, int type)
{
    os_lock(&g_handle_lock);
    uint32_t i = slot_of(h);
    struct w32_obj *o = i ? g_slots[i].o : NULL;
    if (o && (!type || o->type == type)) {
        g_slots[i].o = NULL;
        g_slots[i].next_free = g_free_head;
        g_free_head = i;
    } else {
        o = NULL;
    }
    os_unlock(&g_handle_lock);
    if (!o) {
        SetLastError(ERROR_INVALID_HANDLE);
        return FALSE;
    }
    w32_obj_unref(o);
    return TRUE;
}

void w32_obj_ref(struct w32_obj *o) { __atomic_add_fetch(&o->refs, 1, __ATOMIC_ACQ_REL); }

void w32_obj_unref(struct w32_obj *o)
{
    if (__atomic_sub_fetch(&o->refs, 1, __ATOMIC_ACQ_REL) == 0) {
        if (o->destroy)
            o->destroy(o);
        o->magic = 0;
        free(o);
    }
}

extern "C" BOOL CloseHandle(HANDLE h)
{
    if (h == W32_PSEUDO_THREAD || h == W32_PSEUDO_PROCESS)
        return TRUE;
    return w32_handle_close(h, 0);
}

W32Log::W32Log() : n(0)
{
    buf[0] = 0;
    s("[fnvnx] ");
}

W32Log &W32Log::s(const char *str)
{
    while (*str && n < sizeof buf - 1)
        buf[n++] = *str++;
    buf[n] = 0;
    return *this;
}

W32Log &W32Log::u(unsigned long long v)
{
    char t[24];
    size_t k = 0;
    do {
        t[k++] = (char)('0' + v % 10);
        v /= 10;
    } while (v);
    while (k && n < sizeof buf - 1)
        buf[n++] = t[--k];
    buf[n] = 0;
    return *this;
}

W32Log &W32Log::h(unsigned long long v)
{
    char t[24];
    size_t k = 0;
    do {
        t[k++] = "0123456789abcdef"[v & 15];
        v >>= 4;
    } while (v);
    s("0x");
    while (k && n < sizeof buf - 1)
        buf[n++] = t[--k];
    buf[n] = 0;
    return *this;
}

void W32Log::emit()
{
    os_debug_print(buf);   /* the local buffer only */
}

static inline int lower(int c) { return c >= 'A' && c <= 'Z' ? c + 32 : c; }

int w32_stricmp(const char *a, const char *b)
{
    for (;; a++, b++) {
        int x = lower((unsigned char)*a), y = lower((unsigned char)*b);
        if (x != y || !x)
            return x - y;
    }
}

int w32_strnicmp(const char *a, const char *b, size_t n)
{
    for (; n; n--, a++, b++) {
        int x = lower((unsigned char)*a), y = lower((unsigned char)*b);
        if (x != y || !x)
            return x - y;
    }
    return 0;
}

DWORD w32_os_error(int neg)
{
    switch (-neg) {
    case 0: return ERROR_SUCCESS;
    case OS_ENOENT: return ERROR_FILE_NOT_FOUND;
    case OS_ENOTDIR: return ERROR_PATH_NOT_FOUND;
    case OS_EACCES: case OS_EISDIR: return ERROR_ACCESS_DENIED;
    case OS_EEXIST: return ERROR_FILE_EXISTS;
    case OS_ENOSPC: return 112; /* ERROR_DISK_FULL */
    case OS_ENOTEMPTY: return ERROR_DIR_NOT_EMPTY;
    case OS_ENOMEM: return ERROR_NOT_ENOUGH_MEMORY;
    case OS_EINVAL: return ERROR_INVALID_PARAMETER;
    default: return 1117; /* ERROR_IO_DEVICE */
    }
}

/* Windows wildcard rules for FindFirstFile: '*' any run, '?' one char, case-insensitive; "*.*" matches
 * names without a dot too, and a trailing ".*" or "." matches the empty extension. */
static int wild(const char *p, const char *n)
{
    for (; *p; p++, n++) {
        if (*p == '*') {
            while (*p == '*')
                p++;
            if (!*p)
                return 1;
            for (; *n; n++)
                if (wild(p, n))
                    return 1;
            return wild(p, n);
        }
        if (!*n) {
            /* "name.*" / "name." also match "name" */
            if (p[0] == '.' && (p[1] == 0 || (p[1] == '*' && p[2] == 0)))
                return 1;
            return 0;
        }
        if (*p != '?' && lower((unsigned char)*p) != lower((unsigned char)*n))
            return 0;
    }
    return *n == 0;
}

int w32_wildcard_match(const char *pat, const char *name)
{
    if (!strcmp(pat, "*.*") || !strcmp(pat, "*"))
        return 1;
    return wild(pat, name);
}

ULONGLONG w32_unix_us_to_filetime(int64_t us)
{
    return (ULONGLONG)(us * 10) + 116444736000000000ull;
}
