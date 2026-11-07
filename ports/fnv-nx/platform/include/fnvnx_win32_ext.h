/* fnvnx_win32_ext.h - Win32 declarations the runtime implements beyond shim/include/fnvnx_win32.h (ERROR_* codes)
 * and the runtime's own hooks.
 *
 * Everything here is imported by FalloutNV.exe (docs/RUNTIME.md, imports table) but not yet declared by the
 * shim (stream S1 owns it). S1 can move these into the shim; the #ifndef guards keep both orders working.
 * Also: the runtime's own hooks (fnvnx_*) used by the loader (S3), input (S7) and render (S6) streams.
 */
#ifndef FNVNX_WIN32_EXT_H
#define FNVNX_WIN32_EXT_H

#include "fnvnx_win32.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ---- constants missing from the shim */
#ifndef ERROR_SUCCESS
#define ERROR_SUCCESS 0L
#define ERROR_INVALID_FUNCTION 1L
#define ERROR_FILE_NOT_FOUND 2L
#define ERROR_PATH_NOT_FOUND 3L
#define ERROR_ACCESS_DENIED 5L
#define ERROR_INVALID_HANDLE 6L
#define ERROR_NOT_ENOUGH_MEMORY 8L
#define ERROR_OUTOFMEMORY 14L
#define ERROR_NO_MORE_FILES 18L
#define ERROR_SHARING_VIOLATION 32L
#define ERROR_HANDLE_EOF 38L
#define ERROR_NOT_SUPPORTED 50L
#define ERROR_FILE_EXISTS 80L
#define ERROR_INVALID_PARAMETER 87L
#define ERROR_CALL_NOT_IMPLEMENTED 120L
#define ERROR_INSUFFICIENT_BUFFER 122L
#define ERROR_INVALID_NAME 123L
#define ERROR_MOD_NOT_FOUND 126L
#define ERROR_PROC_NOT_FOUND 127L
#define ERROR_DIR_NOT_EMPTY 145L
#define ERROR_ALREADY_EXISTS 183L
#define ERROR_MORE_DATA 234L
#define ERROR_NOT_OWNER 288L
#define ERROR_TOO_MANY_POSTS 298L
#define ERROR_INVALID_ADDRESS 487L
#endif
/* (the other Win32 constants and declarations that were here moved to shim/include/fnvnx_win32.h; the
 * ERROR_* codes stay here: some decomp units define ERROR_SUCCESS... as enumerators of their own) */

/* ---- runtime hooks (not Win32) */
/* Paths (docs/RUNTIME.md "Paths"). Windows prefix (case-insensitive, '\' or '/') -> host directory. */
int fnvnx_path_mount(const char *win_prefix, const char *host_dir);
void fnvnx_path_reset(const char *root);        /* default mounts under root (NULL: os_default_root) */
/* Maps a Windows path to a host path, resolving existing components case-insensitively. Returns 0 or a
 * Win32 error code (ERROR_PATH_NOT_FOUND when a directory component is missing and !for_create...). */
DWORD fnvnx_path_map(const char *win_path, char *host, size_t host_size);
void fnvnx_set_command_line(int argc, char **argv);
/* the first run's profile files, as the PC launcher writes them (platform/win32/profile.cpp); a mask of what it made */
#define FNVNX_PROFILE_INI 1u      /* My Games\FalloutNV\Fallout.ini: a copy of Fallout_default.ini */
#define FNVNX_PROFILE_PREFS 2u    /* My Games\FalloutNV\FalloutPrefs.ini: Fallout_default.ini + [Display] sD3DDevice */
#define FNVNX_PROFILE_PLUGINS 4u  /* AppData\Local\FalloutNV\plugins.txt: empty, as the launcher leaves it */
unsigned fnvnx_first_run_profile(void);
/* implicit TLS of the image (.tls section of FalloutNV.exe, from the loader, S3) */
void fnvnx_tls_set_template(const void *init_data, size_t init_size, size_t total_size);
void *fnvnx_tls_block(void);                     /* this thread's block; what fs:[0x2c][0] points at */
void **fnvnx_tls_array(void);                    /* what fs:[0x2c] returns */
/* Wait-graph watchdog (platform/win32/waitgraph.cpp, D25 #7): every blocking wait on a critical section, event,
 * mutex, semaphore or thread is recorded; a scan builds the wait-for graph (waiter -> owner: a section's and a mutex's
 * owner, a thread handle's thread) and reports deadlock cycles and waits longer than limit_ms, by thread id, object
 * address and caller address only. start: a scanner thread every period_ms (0: no thread, call scan yourself). Off
 * (a single load on the slow paths) until started. Lines go to the sink (default os_debug_print), all "wg: ..." */
typedef void (*fnvnx_waitgraph_sink)(const char *line);
void fnvnx_waitgraph_start(unsigned limit_ms, unsigned period_ms, fnvnx_waitgraph_sink sink);
void fnvnx_waitgraph_scan(void);
void fnvnx_waitgraph_stop(void);
/* from a static constructor (tools/threads/autostart.c): recording on now, the scanner (1 s, the default sink) at the
 * first blocking wait */
void fnvnx_waitgraph_autostart(unsigned limit_ms);                 /* stops the scanner thread; recording stays on */
/* counts so far: cycles (each distinct cycle once), long waits (each wait once), scans */
void fnvnx_waitgraph_counts(unsigned *cycles, unsigned *long_waits, unsigned *scans);
void fnvnx_waitgraph_summary(void);
/* an interposer around EnterCriticalSection / WaitFor* (the race checker's --wrap) names the real call site: set it
 * before the call, 0 after; the watchdog records it instead of the interposer's own address */
void fnvnx_waitgraph_set_caller(unsigned pc);
/* prints what the thread checkers have so far: the race checker's pairs (hb_dump, when linked) and the watchdog's
 * summary (when on). ExitProcess calls it; the scanner every FNVNX_THREADS_DUMP_S seconds (default 10). */
void fnvnx_threads_flush(void);              /* "wg: summary cycles N long-waits N scans N threads N" */
/* Memory lane (platform/win32/memtrack.cpp, D25 #8; tools/perf/MEM.md): live bytes by kind (0 heap, 1 virtual,
 * 2 gpu), the peak, failed allocations and allocation sites by caller address, "mem: ..." lines to the sink (default
 * os_debug_print). Off (one load on the allocation paths) until started. */
typedef void (*fnvnx_mem_sink)(const char *line);
void fnvnx_mem_start(unsigned period_ms, fnvnx_mem_sink sink);
void fnvnx_mem_autostart(unsigned period_ms);     /* from a static constructor (tools/perf/mem_autostart.c) */
void fnvnx_mem_stop(void);                        /* recording off, the sampler stopped; counts stay */
void fnvnx_mem_flush(void);                       /* peak, sites, summary (ExitProcess calls it) */
void fnvnx_mem_counts(unsigned long long live[3], unsigned long long *peak, unsigned long long *fails);
/* for the allocator stand-ins and tools/perf/mem_wrap.c */
void fnvnx_mem_note(int kind, const void *p, size_t n, unsigned caller);
void fnvnx_mem_forget(int kind, const void *p);   /* kind -1: whatever kind p was recorded as */
void fnvnx_mem_delta(int kind, long long bytes, unsigned caller);
void fnvnx_mem_fail(int kind, size_t n, unsigned caller);
int fnvnx_mem_enter(unsigned caller);             /* a stand-in records its own allocation: the wrapped malloc skips */
void fnvnx_mem_leave(int entered);
int fnvnx_mem_inside(void);
unsigned fnvnx_mem_caller(unsigned fallback);
/* builtin modules for LoadLibraryA/GetProcAddress (xinput1_3.dll, ... registered by S6/S7) */
typedef FARPROC (*fnvnx_module_resolver)(const char *proc);
void fnvnx_register_module(const char *dll_name, fnvnx_module_resolver resolve);
/* Win32 export table: implementation of a DLL import by name (for IAT slots resolved from the exe) */
void *fnvnx_win32_lookup_export(const char *dll, const char *name);
/* An import slot of the exe, as the loader hands it over: by name (ordinal == FNVNX_IMPORT_BY_NAME, the loader's
 * FNVNX_BY_NAME) or by ordinal (name NULL). NULL: no export (the slot stays 0). */
#define FNVNX_IMPORT_BY_NAME 0xffffffffu
void *fnvnx_win32_lookup_import(const char *dll, const char *name, unsigned ordinal);
/* window/input plumbing for S6/S7 */
HWND fnvnx_main_window(void);
void fnvnx_set_display_size(int w, int h);
void fnvnx_post_message(HWND, UINT, WPARAM, LPARAM);
void fnvnx_set_key_state(int vk, int down);
void fnvnx_set_cursor_pos(int x, int y);
/* COM classes (e.g. XAudio2 from S7) for CoCreateInstance */
typedef HRESULT (*fnvnx_com_factory)(const GUID *iid, void **out);
void fnvnx_register_com_class(const GUID *clsid, fnvnx_com_factory f);
/* message boxes: default answer policy is the default button; tests can capture the text */
typedef int (*fnvnx_msgbox_hook)(const char *text, const char *caption, UINT type);
void fnvnx_set_msgbox_hook(fnvnx_msgbox_hook);

#ifdef __cplusplus
}
#endif
#endif
