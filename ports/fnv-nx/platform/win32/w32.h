/* w32.h - internals shared by the Win32 implementation files (not seen by game code). */
#ifndef FNVNX_W32_H
#define FNVNX_W32_H

#include "fnvnx_win32_ext.h"
#include "../os/os.h"

#include <stdarg.h>
#include <string.h>

#define W32_TLS_SLOTS 1088 /* 64 + 1024, as on Windows */

/* per-thread state, created lazily for any thread that touches the runtime */
struct w32_waiter {
    os_cond_t cond;
};
struct w32_obj;
struct w32_thread {
    DWORD tid;
    DWORD last_error;
    void *tls[64];
    void **tls_ext;                /* 1024 more, allocated on first use */
    void *implicit_tls[1];         /* fs:[0x2c] points here; [0] = image TLS block of this thread */
    int implicit_tls_borrowed;     /* [0] came from fnvnx_image_tls_hook: not ours to free */
    struct w32_obj *self_obj;      /* thread object when created by CreateThread */
    struct w32_waiter waiter;
    char name[32];
};
struct w32_thread *w32_self(void);

/* ---- kernel objects: HANDLE is a pointer to one of these */
enum { W32_EVENT = 1, W32_MUTEX, W32_SEMAPHORE, W32_THREAD, W32_FILE, W32_FIND, W32_HEAP };
#define W32_MAGIC 0x46564E58u /* "FVNX" */
struct w32_link {                  /* one waiter linked to one object */
    struct w32_link *next, *prev;
    struct w32_thread *t;
};
struct w32_obj {
    uint32_t magic;
    int type;
    int refs;                      /* atomic */
    struct w32_link waiters;       /* list head, guarded by w32_wait_lock */
    void (*destroy)(struct w32_obj *);
};
struct w32_obj *w32_obj_new(int type, size_t size, void (*destroy)(struct w32_obj *));
/* Handles are indices into a table with a generation count (stale or double-closed handles fail with
 * ERROR_INVALID_HANDLE instead of touching freed memory); values are multiples of 4 below 2^32 and never
 * NULL, INVALID_HANDLE_VALUE or a pseudo handle. */
HANDLE w32_handle_open(struct w32_obj *o);         /* the handle takes over one reference; NULL if full */
struct w32_obj *w32_obj_get(HANDLE h, int type);   /* type 0: any; NULL + ERROR_INVALID_HANDLE if bad */
BOOL w32_handle_close(HANDLE h, int type);         /* type 0: any */
void w32_obj_ref(struct w32_obj *o);
void w32_obj_unref(struct w32_obj *o);
/* object wait support (sync.cpp): global lock around all waitable state */
extern os_lock_t w32_wait_lock;
void w32_obj_signal_locked(struct w32_obj *o);     /* wake waiters of o; caller holds w32_wait_lock */

/* ---- threads (thread.cpp) */
#define W32_PSEUDO_THREAD ((HANDLE)(LONG_PTR)-2)
#define W32_PSEUDO_PROCESS ((HANDLE)(LONG_PTR)-1)
struct w32_thread_obj;
int w32_thread_signaled(struct w32_obj *o);

/* ---- paths (path.cpp) */
#define W32_PATH_MAX 1024
/* Windows -> host. create_last: the last component may not exist (it keeps the given spelling). */
DWORD w32_map_path(const char *win, char *host, size_t n);
void w32_virtual_cwd(char *out, size_t n);
DWORD w32_full_path(const char *win, char *out, size_t n); /* normalised "x:\\a\\b" */
const char *w32_exe_path(void);              /* virtual Windows path of FalloutNV.exe */
const char *w32_user_dir(void);              /* virtual Windows path of the documents folder */
/* errno-style os error -> Win32 error code */
DWORD w32_os_error(int neg);
int w32_wildcard_match(const char *pat, const char *name); /* Windows *, ? matching, case-insensitive */

/* ---- misc */
void w32_log(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
#define W32_UNUSED(x) ((void)(x))
int w32_stricmp(const char *a, const char *b);
int w32_strnicmp(const char *a, const char *b, size_t n);
/* FILETIME helpers: 100 ns since 1601-01-01 */
ULONGLONG w32_unix_us_to_filetime(int64_t us);
static inline ULONGLONG w32_ft64(const FILETIME *f) { return ((ULONGLONG)f->dwHighDateTime << 32) | f->dwLowDateTime; }
static inline void w32_set_ft(FILETIME *f, ULONGLONG v) { f->dwLowDateTime = (DWORD)(v & 0xffffffffu); f->dwHighDateTime = (DWORD)(v >> 32); }
/* DWORD is 64-bit on LP64 hosts; keep values in the 32-bit Win32 range */
#define W32_DW(x) ((DWORD)((x) & 0xffffffffu))

#endif
