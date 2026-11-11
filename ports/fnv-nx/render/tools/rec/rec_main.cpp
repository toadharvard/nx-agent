/* rec_main.cpp: the recorder d3d9.dll (RD1, Windows/Wine side). Every export of the spec is a thunk into the generated d9wrap_<Export>
 * (wrapped exports) or a signature-independent `jmp` to the real function (exports the spec has no signature for). The D9S1 stream goes to
 * the file D9REC_OUT (default d9rec.d9s), buffered, written through at every Present/PresentEx and at process exit.
 * env: D9REC_OUT (stream path, Windows path), D9REC_REAL (path of the real d3d9.dll; default <system dir>\d3d9.dll). */
#include "d9wrap.gen.h"
#include "d9rec_ids.h"
#include <stdio.h>
#include <new>

void *operator new(size_t n) { return malloc(n ? n : 1); }
void operator delete(void *p) noexcept { free(p); }
void operator delete(void *p, size_t) noexcept { free(p); }

static HMODULE g_self, g_real;
static HANDLE g_file = INVALID_HANDLE_VALUE;
static unsigned char g_buf[1 << 20];
static size_t g_len;
static volatile LONG g_init;   /* 0 new, 1 running, 2 done */
static bool g_werr, g_sync;   /* g_sync: D9REC_FLUSH=1, write through after every record (a crash then loses nothing) */

static void out_flush() {
    size_t o = 0;
    while (o < g_len && g_file != INVALID_HANDLE_VALUE) {
        DWORD k = 0;
        if (!WriteFile(g_file, g_buf + o, (DWORD)(g_len - o), &k, NULL) || !k) {
            if (!g_werr) { g_werr = true; fprintf(stderr, "d9rec: write error %lu: stream is incomplete\n", (unsigned long)GetLastError()); }
            break;
        }
        o += k;
    }
    g_len = 0;
}
static void sink(void *, const void *p, size_t n) {   /* called with the runtime's lock held: records arrive whole and in order */
    if (n > sizeof g_buf - g_len) out_flush();
    if (n > sizeof g_buf) { size_t s = g_len; (void)s; const unsigned char *q = (const unsigned char *)p; while (n) { size_t c = n < sizeof g_buf ? n : sizeof g_buf; memcpy(g_buf, q, c); g_len = c; out_flush(); q += c; n -= c; } return; }
    memcpy(g_buf + g_len, p, n); g_len += n;
    if (g_sync) { out_flush(); return; }
    if (n >= 20 && memcmp(p, "D9S1", 4)) {   /* a record (the 44-byte file header starts with the magic): flush after a presenting method */
        uint32_t w3; memcpy(&w3, (const char *)p + 12, 4);
        for (const auto &e : d9rec_present) if ((w3 & 0xFFFF) == e.iface && (w3 >> 16) == e.slot) { out_flush(); break; }
    }
}

static HMODULE load_real() {
    char p[MAX_PATH + 16];
    DWORD n = GetEnvironmentVariableA("D9REC_REAL", p, MAX_PATH);
    if (!n || n >= MAX_PATH) {
        UINT k = GetSystemDirectoryA(p, MAX_PATH);
        if (!k || k >= MAX_PATH) { fprintf(stderr, "d9rec: no system directory\n"); return NULL; }
        strcat(p, "\\d3d9.dll");
    }
    HMODULE h = LoadLibraryA(p);
    if (h && h == g_self) { FreeLibrary(h); h = NULL; fprintf(stderr, "d9rec: %s is this proxy; set D9REC_REAL to the real d3d9.dll\n", p); }
    else if (!h) fprintf(stderr, "d9rec: cannot load %s (error %lu)\n", p, (unsigned long)GetLastError());
    return h;
}
static void ensure() {   /* once: real DLL, output file, runtime */
    if (g_init == 2) return;
    if (InterlockedCompareExchange(&g_init, 1, 0) != 0) { while (g_init != 2) SwitchToThread(); return; }
    g_real = load_real();
    char p[MAX_PATH];
    DWORD n = GetEnvironmentVariableA("D9REC_OUT", p, MAX_PATH);
    if (!n || n >= MAX_PATH) strcpy(p, "d9rec.d9s");
    g_file = CreateFileA(p, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (g_file == INVALID_HANDLE_VALUE) fprintf(stderr, "d9rec: cannot create %s (error %lu): nothing is recorded\n", p, (unsigned long)GetLastError());
    { char t[8]; DWORD k = GetEnvironmentVariableA("D9REC_FLUSH", t, sizeof t); g_sync = k > 0 && k < sizeof t && t[0] != '0'; }
    { char t[8]; DWORD k = GetEnvironmentVariableA("D9REC_TRACE", t, sizeof t); d9s_trace = k > 0 && k < sizeof t && t[0] != '0'; }
    d9s_open(sink, NULL, d9wrap_spec_sha256);
    out_flush();
    InterlockedExchange(&g_init, 2);
}
template <class F> static F real(const char *name) {
    ensure();
    F f = g_real ? (F)GetProcAddress(g_real, name) : NULL;
    if (!f) { fprintf(stderr, "d9rec: the real d3d9 has no %s\n", name); ExitProcess(3); }
    return f;
}

/* ---- wrapped exports (signatures: d9wrap.gen.h, from the spec) ---- */
extern "C" {
IDirect3D9 *WINAPI Direct3DCreate9(UINT v) { return d9wrap_Direct3DCreate9(real<IDirect3D9 *(WINAPI *)(UINT)>("Direct3DCreate9"), v); }
HRESULT WINAPI Direct3DCreate9Ex(UINT v, IDirect3D9Ex **pp) { return d9wrap_Direct3DCreate9Ex(real<HRESULT(WINAPI *)(UINT, IDirect3D9Ex **)>("Direct3DCreate9Ex"), v, pp); }
int WINAPI D3DPERF_BeginEvent(D3DCOLOR c, LPCWSTR w) { return d9wrap_D3DPERF_BeginEvent(real<int(WINAPI *)(D3DCOLOR, LPCWSTR)>("D3DPERF_BeginEvent"), c, w); }
int WINAPI D3DPERF_EndEvent(void) { return d9wrap_D3DPERF_EndEvent(real<int(WINAPI *)(void)>("D3DPERF_EndEvent")); }
DWORD WINAPI D3DPERF_GetStatus(void) { return d9wrap_D3DPERF_GetStatus(real<DWORD(WINAPI *)(void)>("D3DPERF_GetStatus")); }
BOOL WINAPI D3DPERF_QueryRepeatFrame(void) { return d9wrap_D3DPERF_QueryRepeatFrame(real<BOOL(WINAPI *)(void)>("D3DPERF_QueryRepeatFrame")); }
void WINAPI D3DPERF_SetMarker(D3DCOLOR c, LPCWSTR w) { d9wrap_D3DPERF_SetMarker(real<void(WINAPI *)(D3DCOLOR, LPCWSTR)>("D3DPERF_SetMarker"), c, w); }
void WINAPI D3DPERF_SetOptions(DWORD o) { d9wrap_D3DPERF_SetOptions(real<void(WINAPI *)(DWORD)>("D3DPERF_SetOptions"), o); }
void WINAPI D3DPERF_SetRegion(D3DCOLOR c, LPCWSTR w) { d9wrap_D3DPERF_SetRegion(real<void(WINAPI *)(D3DCOLOR, LPCWSTR)>("D3DPERF_SetRegion"), c, w); }

/* ---- exports without a signature in the spec: forwarded 1:1 (jmp keeps the caller's stack frame, whatever its size); not recorded ---- */
void *d9rec_fwd[5];
static const char *const fwd_names[5] = { "DebugSetLevel", "DebugSetMute", "Direct3DShaderValidatorCreate9", "PSGPError", "PSGPSampleTexture" };
void *__cdecl d9rec_resolve(int i) { void *f = real<void *>(fwd_names[i]); d9rec_fwd[i] = f; return f; }
}
#define FWD(i, name) asm(".text\n.globl _" #name "\n_" #name ":\n movl _d9rec_fwd+" #i "*4,%eax\n testl %eax,%eax\n jnz 1f\n pushl $" #i "\n call _d9rec_resolve\n addl $4,%esp\n1: jmp *%eax\n");
FWD(0, DebugSetLevel) FWD(1, DebugSetMute) FWD(2, Direct3DShaderValidatorCreate9) FWD(3, PSGPError) FWD(4, PSGPSampleTexture)

BOOL WINAPI DllMain(HINSTANCE h, DWORD why, LPVOID) {
    if (why == DLL_PROCESS_ATTACH) { g_self = h; DisableThreadLibraryCalls(h); }
    else if (why == DLL_PROCESS_DETACH && g_init == 2) { out_flush(); if (g_file != INVALID_HANDLE_VALUE) { CloseHandle(g_file); g_file = INVALID_HANDLE_VALUE; } }
    return TRUE;
}
