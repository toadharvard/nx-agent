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
/* implicit TLS of the image (.tls section of FalloutNV.exe, from the loader, S3) */
void fnvnx_tls_set_template(const void *init_data, size_t init_size, size_t total_size);
void *fnvnx_tls_block(void);                     /* this thread's block; what fs:[0x2c][0] points at */
void **fnvnx_tls_array(void);                    /* what fs:[0x2c] returns */
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
