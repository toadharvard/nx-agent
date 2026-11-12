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
#define ERROR_DLL_INIT_FAILED 1114L
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
/* the registry's first boot (platform/win32/registry_seed.cpp): when the hive file does not exist, makes it from the declared
 * machine's rows (platform/manifest/declared_registry.tsv, generated into the build); when it exists, touches nothing */
void fnvnx_registry_first_run(void);
/* implicit TLS of the image (.tls section of FalloutNV.exe, from the loader, S3) */
BOOL fnvnx_tls_set_template(const void *init_data, size_t init_size, size_t total_size);
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
/* prints what the watchdog has so far (its summary, when on); a race checker prints its own pairs (hb_dump). ExitProcess calls it; the scanner every FNVNX_THREADS_DUMP_S seconds (default 10). */
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
/* Platform lane (platform/win32/platstat.cpp, D25 #3 #4 #6; tools/perf/PLAT.md): calls to unimplemented
 * entries, failed file opens, audio decode failures and unmapped inputs, counted per (class, name, caller, detail),
 * "plat: ..." lines to the sink (default os_debug_print). Off (one load per report site) until started. */
enum { FNVNX_NOTE_UNIMPL = 0, FNVNX_NOTE_OPEN, FNVNX_NOTE_DECODE, FNVNX_NOTE_INPUT, FNVNX_NOTE_NCLASS };
typedef void (*fnvnx_plat_sink)(const char *line);
extern volatile int fnvnx_plat_on;
void fnvnx_plat_start(fnvnx_plat_sink sink);
void fnvnx_plat_autostart(void);                  /* from a static constructor (tools/perf/plat_autostart.c) */
void fnvnx_plat_stop(void);
void fnvnx_plat_flush(void);                      /* counts and summary (ExitProcess calls it) */
void fnvnx_plat_counts(unsigned long long distinct[FNVNX_NOTE_NCLASS], unsigned long long calls[FNVNX_NOTE_NCLASS]);
void fnvnx_plat_note(int cls, const char *name, unsigned caller, const char *detail);
/* win: the Windows path as the game gave it; host: what the mapper made of it (NULL if it failed); api: the entry */
void fnvnx_plat_open_failed(const char *win, const char *host, unsigned err, unsigned caller, const char *api);
#define FNVNX_NOTE_RA ((unsigned)(unsigned long)__builtin_return_address(0))
/* builtin modules for LoadLibraryA/GetProcAddress (xinput1_3.dll, ... registered by S6/S7) */
typedef FARPROC (*fnvnx_module_resolver)(const char *proc);
void fnvnx_register_module(const char *dll_name, fnvnx_module_resolver resolve);
/* Win32 export table: implementation of a DLL import by name (for IAT slots resolved from the exe) */
void *fnvnx_win32_lookup_export(const char *dll, const char *name);
/* An import slot of the exe, as the loader hands it over: by name (ordinal == FNVNX_IMPORT_BY_NAME, the loader's
 * FNVNX_BY_NAME) or by ordinal (name NULL). NULL: no export (the slot stays 0). */
#define FNVNX_IMPORT_BY_NAME 0xffffffffu
void *fnvnx_win32_lookup_import(const char *dll, const char *name, unsigned ordinal);
/* Exceptions raised by platform code (a failed HEAP_GENERATE_EXCEPTIONS allocation, RaiseException, DebugBreak when
 * the caller is the platform): the lifter runtime installs its SEH dispatcher over the guest's fs:[0] chain here at
 * start. Returns when the exception was continued. Without a backend no handler exists: the top-level filter of
 * SetUnhandledExceptionFilter decides, then the process ends with the exception code as its exit status. */
typedef void (*fnvnx_raise_fn)(uint32_t code, uint32_t flags, uint32_t nargs, const uintptr_t *args);
void fnvnx_win32_set_raise_backend(fnvnx_raise_fn fn);
/* Test hooks. Module table: -1 for a system DLL (pinned), the reference count of a registered module, 0 for a
 * handle that is no module. Steam: the number of (object, id) registrations of SteamAPI_RegisterCallback. */
int fnvnx_module_load_count(HMODULE);
/* The loader reports a mapped image (the exe, or a translated game module after mapping it): the module table gets it
 * (HMODULE = base; GetModuleHandle/GetModuleFileName/LoadLibrary/GetProcAddress by the PE export directory in memory,
 * forwarders followed; FindResource* on its resource directory) and the page map records it as MEM_IMAGE, the whole
 * image and then each of its sections with the protection of its R/W/X bits. `name` is the file name or path. */
void fnvnx_win32_register_image(const char *name, uint32_t base, uint32_t size);
int fnvnx_steam_callback_count(void);
/* The i-th system module (the distinct DLL names of the export table, as the exe imports them), NULL past the last */
const char *fnvnx_win32_system_module(unsigned i);
/* window/input plumbing for S6/S7 */
HWND fnvnx_main_window(void);
void fnvnx_set_display_size(int w, int h);   /* the output size (display.cpp): 1920x1080 is docked, any smaller one handheld */
/* the console was docked or undocked (the lifecycle code calls it): re-reads os_display_docked, sets the display
 * mode, the desktop window and GetSystemMetrics, and sends WM_DISPLAYCHANGE to the top-level windows */
void fnvnx_display_mode_changed(void);
void fnvnx_post_message(HWND, UINT, WPARAM, LPARAM);
void fnvnx_set_key_state(int vk, int down);
void fnvnx_set_cursor_pos(int x, int y);
BOOL GetClipCursor(RECT *);                  /* user32; ClipCursor is in the shim */
HWND GetCapture(void);
HWND fnwin_capture(void);                    /* the window with the mouse capture, NULL if none */
/* COM classes (e.g. XAudio2 from S7) for CoCreateInstance */
typedef HRESULT (*fnvnx_com_factory)(const GUID *iid, void **out);
/* A statically linked server (resident for the life of the process); 0 when the registry (16 classes) is full. */
int fnvnx_register_com_class(const GUID *clsid, fnvnx_com_factory f);
/* A server with its own unload query (DllCanUnloadNow: S_OK when idle); CoFreeUnusedLibraries drops the idle ones. */
typedef HRESULT (*fnvnx_com_can_unload)(void);
int fnvnx_register_com_dll(const GUID *clsid, fnvnx_com_factory f, fnvnx_com_can_unload can_unload);
#ifndef CLSCTX_INPROC_SERVER
#define CLSCTX_INPROC_SERVER 0x1
#define CLSCTX_INPROC_HANDLER 0x2
#endif
#ifndef CO_E_NOTINITIALIZED
#define CO_E_NOTINITIALIZED ((HRESULT)0x800401F0L)
#endif
#ifndef RPC_E_CHANGED_MODE
#define RPC_E_CHANGED_MODE ((HRESULT)0x80010106L)
#endif
#ifndef CLASS_E_NOAGGREGATION
#define CLASS_E_NOAGGREGATION ((HRESULT)0x80040110L)
#endif
/* ---- virtual memory (platform/win32/memory.cpp, filemap.cpp): the process has ONE page map of its user address space
 * [0x00010000, 0x7FFEFFFF]; every page has a state, type, allocation base and protections. */
#ifndef ERROR_NOACCESS
#define ERROR_NOACCESS 998L
#endif
#ifndef ERROR_BAD_LENGTH
#define ERROR_BAD_LENGTH 24L
#endif
#ifndef ERROR_FILE_INVALID
#define ERROR_FILE_INVALID 1006L
#endif
#ifndef ERROR_MAPPED_ALIGNMENT
#define ERROR_MAPPED_ALIGNMENT 1132L
#endif
#ifndef ERROR_PRIVILEGE_NOT_HELD
#define ERROR_PRIVILEGE_NOT_HELD 1314L
#endif
#ifndef ERROR_BAD_EXE_FORMAT
#define ERROR_BAD_EXE_FORMAT 193L
#endif
#ifndef MEM_MAPPED
#define MEM_MAPPED 0x40000
#define MEM_IMAGE 0x1000000
#endif
#ifndef MEM_RESET
#define MEM_RESET 0x80000
#define MEM_RESET_UNDO 0x1000000
#define MEM_WRITE_WATCH 0x200000
#define WRITE_WATCH_FLAG_RESET 1
UINT GetWriteWatch(DWORD flags, PVOID base, SIZE_T size, PVOID *addresses, PULONG_PTR count, PULONG granularity);
UINT ResetWriteWatch(PVOID base, SIZE_T size);
#define MEM_PHYSICAL 0x400000
#define MEM_LARGE_PAGES 0x20000000
#endif
#ifndef PAGE_WRITECOMBINE
#define PAGE_WRITECOMBINE 0x400
#endif
#ifndef FILE_MAP_READ
#define FILE_MAP_COPY 0x0001
#define FILE_MAP_WRITE 0x0002
#define FILE_MAP_READ 0x0004
#define FILE_MAP_ALL_ACCESS 0x000F001F
#define FILE_MAP_EXECUTE 0x0020
#define SEC_FILE 0x00800000
#define SEC_IMAGE 0x01000000
#define SEC_RESERVE 0x04000000
#define SEC_COMMIT 0x08000000
#define SEC_NOCACHE 0x10000000
#define SEC_WRITECOMBINE 0x40000000
#define SEC_LARGE_PAGES 0x80000000
#endif
#ifndef STATUS_GUARD_PAGE_VIOLATION
#define STATUS_GUARD_PAGE_VIOLATION 0x80000001u
#endif
#ifndef STATUS_ACCESS_VIOLATION
#define STATUS_ACCESS_VIOLATION 0xC0000005u
#endif
BOOL VirtualProtect(LPVOID, SIZE_T, DWORD, PDWORD);
HANDLE CreateFileMappingA(HANDLE, void *, DWORD, DWORD, DWORD, LPCSTR);
LPVOID MapViewOfFile(HANDLE, DWORD, DWORD, DWORD, SIZE_T);
BOOL UnmapViewOfFile(LPCVOID);
BOOL FlushViewOfFile(LPCVOID, SIZE_T);
/* The loader records memory it did not get from VirtualAlloc: base/size (page granular), type MEM_IMAGE / MEM_MAPPED /
 * MEM_PRIVATE, the pages' protection. The pages become committed in the map (the platform does not touch the OS
 * protection of noted memory except through VirtualProtect). A note over pages that are free starts a new allocation
 * at base; a note over pages of an existing allocation of the same type only sets their protection: the loader notes
 * the whole image first (headers, PAGE_READONLY) and then each section with its own protection, so one image is one
 * allocation. The platform records heap-less regions it obtains itself the same way. Ranges outside the 32-bit user
 * space [0x10000, 0x7FFEFFFF] are not part of the map and are ignored. */
void fnvnx_win32_vm_note(uintptr_t base, size_t size, DWORD type, DWORD protect);
/* The memory noted above is gone (a thread's stack ended, a module unloaded): its pages are free in the map again. */
void fnvnx_win32_vm_unnote(uintptr_t base, size_t size);
/* The host fault handler asks what a memory fault at addr (access 0 read, 1 write, 8 exec) is, by the map:
 * returns 1: a guard-page hit (the guard bit is cleared, *code_out = STATUS_GUARD_PAGE_VIOLATION: raise it, the
 * access is retried after the handler), 0: an access violation (*code_out = STATUS_ACCESS_VIOLATION), 2: the map
 * permits the access (another thread cleared the guard first): just retry. */
int fnvnx_win32_vm_fault(uintptr_t addr, int access, uint32_t *code_out);

/* message boxes: default answer policy is the default button; tests can capture the text */
typedef int (*fnvnx_msgbox_hook)(const char *text, const char *caption, UINT type);
void fnvnx_set_msgbox_hook(fnvnx_msgbox_hook);

#ifdef __cplusplus
}
/* the display mode for the renderer (display.cpp), C++ linkage as render/d3d9/dev/d3d9_adapter.h declares
 * `NxEnvProfile nx_env_profile();`: 1 docked (1080p), 0 handheld (720p); NxEnvProfile is an enum of that int */
int nx_env_profile(void);
#endif
#endif
