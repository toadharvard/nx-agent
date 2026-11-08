/* fnvtrace.c - function-boundary tracer for the original FalloutNV.exe under Wine (the reference side of the
 * lockstep trace, tools/lockstep/README.md). Our own code; it names no game code, only addresses from a list
 * (and `dll!Export [name]` lines: a system API entry, resolved with GetProcAddress, e.g. kernel32!CreateFileA for
 * S4's platform baseline, tools/perf/plat_wine.py).
 *
 * Built twice with i686-w64-mingw32-gcc (build.sh):
 *   fnvtrace.dll   -DFNVTRACE_DLL: loaded into the game before its first instruction; puts int3 on every listed
 *                  function entry and logs each call and its return from a vectored exception handler.
 *   fnvtrace.exe   the launcher: fnvtrace.exe <game exe> [args]: CreateProcess suspended, the main thread parked at
 *                  the entry point (jmp $) until the loader has run, LoadLibraryA(fnvtrace.dll) in the child through
 *                  CreateRemoteThread, the entry's bytes restored, resume.
 * Environment (read by the DLL):
 *   FNVTRACE_LIST  functions, one per line: <hex address> [name]   ('#' comments)
 *   FNVTRACE_OUT   the TSV log (default fnvtrace.tsv beside the exe)
 *   FNVTRACE_MAX   stop logging after this many calls (default 1000000)
 *   FNVTRACE_SNAP  bytes snapshotted at ecx and at each pointer-looking stack argument at entry and compared at
 *                  return (default 64; 0 = off): the callee's writes to the objects it was given
 *   FNVTRACE_DEREF bytes of each pointer-looking argument dumped at entry, hex (default 32; 0 = off)
 * Log lines (tab-separated; the same shapes as emu_guest.py --trace-fn, so boot_trace.py reads both):
 *   call <seq> <tid> <depth> <name> <orig> <ecx> <edx> <s0..s7> <esp> <ret>
 *   arg  <seq> <slot> <hex bytes>                  (FNVTRACE_DEREF; slot ecx, edx, s0..s7)
 *   ret  <seq> <tid> <depth> <name> <eax> <edx>
 *   wr   <seq> <slot> <offset> <old hex> <new hex>  (FNVTRACE_SNAP: bytes changed between entry and return)
 * Returns are caught by swapping the return address on the stack for a trap address, with a per-thread shadow
 * stack; a frame skipped by a longjmp or an exception unwind is dropped (logged as 'lost') when a shallower one
 * returns. The int3 is re-armed after the original instruction by a single step. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef FNVTRACE_DLL

#define MAXFN 65536
#define MAXDEPTH 512
#define NSLOT 10   /* ecx, edx, s0..s7 */

typedef struct { DWORD addr; BYTE orig; char name[64]; } Fn;
typedef struct {
    DWORD ret, esp, seq, fn;
    DWORD ptr[NSLOT];
    BYTE *snap;   /* NSLOT * g_snap bytes */
} Frame;
typedef struct { int depth; DWORD rearm; Frame f[MAXDEPTH]; } Tls;

static Fn *g_fn;
static int g_nfn;
static FILE *g_out;
static LONG g_seq, g_calls;
static long g_max = 1000000;
static int g_snap = 64, g_deref = 32;
static DWORD g_tls = TLS_OUT_OF_INDEXES;
static BYTE *g_trap;   /* return trap: one int3 */
static CRITICAL_SECTION g_lock;

static int fn_cmp(const void *a, const void *b) {
    DWORD x = ((const Fn *)a)->addr, y = ((const Fn *)b)->addr;
    return x < y ? -1 : x > y;
}
static Fn *fn_find(DWORD a) {
    int lo = 0, hi = g_nfn - 1;
    while (lo <= hi) {
        int m = (lo + hi) / 2;
        if (g_fn[m].addr == a) return &g_fn[m];
        if (g_fn[m].addr < a) lo = m + 1; else hi = m - 1;
    }
    return NULL;
}
static Tls *tls(void) {
    Tls *t = TlsGetValue(g_tls);
    if (!t) {
        t = calloc(1, sizeof *t);
        TlsSetValue(g_tls, t);
    }
    return t;
}
static int readable(DWORD p, int n) {
    /* pointer-looking: above 64 KiB and readable */
    if (p < 0x10000 || n <= 0) return 0;
    MEMORY_BASIC_INFORMATION mi;
    if (!VirtualQuery((void *)p, &mi, sizeof mi) || mi.State != MEM_COMMIT) return 0;
    if (mi.Protect & (PAGE_NOACCESS | PAGE_GUARD)) return 0;
    return (DWORD)mi.BaseAddress + mi.RegionSize >= p + (DWORD)n;
}
static void hexdump(char *dst, const BYTE *p, int n) {
    static const char hx[] = "0123456789abcdef";
    for (int i = 0; i < n; i++) { dst[2 * i] = hx[p[i] >> 4]; dst[2 * i + 1] = hx[p[i] & 15]; }
    dst[2 * n] = 0;
}
static void patch(DWORD a, BYTE b) {
    DWORD old;
    VirtualProtect((void *)a, 1, PAGE_EXECUTE_READWRITE, &old);
    *(volatile BYTE *)a = b;
    VirtualProtect((void *)a, 1, old, &old);
    FlushInstructionCache(GetCurrentProcess(), (void *)a, 1);
}
static const char *slot_name(int k) {
    static const char *n[NSLOT] = {"ecx", "edx", "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7"};
    return n[k];
}

static void on_call(CONTEXT *c, Fn *f) {
    Tls *t = tls();
    DWORD *sp = (DWORD *)c->Esp;
    DWORD v[NSLOT] = {c->Ecx, c->Edx};
    for (int k = 0; k < 8; k++) v[2 + k] = readable((DWORD)(sp + 1 + k), 4) ? sp[1 + k] : 0;
    if (g_calls >= g_max || t->depth >= MAXDEPTH) return;
    InterlockedIncrement(&g_calls);
    DWORD seq = (DWORD)InterlockedIncrement(&g_seq);
    char dump[2 * 256 + 1];
    EnterCriticalSection(&g_lock);
    fprintf(g_out, "call\t%lu\t%lu\t%d\t%s\t%08lx", seq, GetCurrentThreadId(), t->depth, f->name, f->addr);
    for (int k = 0; k < NSLOT; k++) fprintf(g_out, "\t%#lx", v[k]);
    fprintf(g_out, "\t%#lx\t%#lx\n", c->Esp, sp[0]);
    if (g_deref)
        for (int k = 0; k < NSLOT; k++)
            if (readable(v[k], g_deref)) {
                hexdump(dump, (const BYTE *)v[k], g_deref);
                fprintf(g_out, "arg\t%lu\t%s\t%s\n", seq, slot_name(k), dump);
            }
    LeaveCriticalSection(&g_lock);
    Frame *fr = &t->f[t->depth++];
    fr->ret = sp[0];
    fr->esp = c->Esp;
    fr->seq = seq;
    fr->fn = (DWORD)(f - g_fn);
    if (g_snap) {
        if (!fr->snap) fr->snap = malloc(NSLOT * g_snap);
        for (int k = 0; k < NSLOT; k++) {
            fr->ptr[k] = readable(v[k], g_snap) ? v[k] : 0;
            if (fr->ptr[k]) memcpy(fr->snap + k * g_snap, (void *)v[k], g_snap);
        }
    }
    sp[0] = (DWORD)g_trap;
}

static void on_ret(CONTEXT *c) {
    Tls *t = tls();
    /* The returning frame is the top one, unless the top one was unwound without returning (longjmp, an
     * exception): then the current esp is already past the parent's entry esp (the parent returned too, through
     * the trap). Normal return: entry esp + 4 <= esp (+ the bytes a stdcall ret N popped) < the parent's entry esp. */
    while (t->depth > 0) {
        Frame *fr = &t->f[--t->depth];
        Fn *f = &g_fn[fr->fn];
        if (t->depth > 0 && c->Esp > t->f[t->depth - 1].esp) {
            EnterCriticalSection(&g_lock);
            fprintf(g_out, "lost\t%lu\t%lu\t%d\t%s\n", fr->seq, GetCurrentThreadId(), t->depth, f->name);
            LeaveCriticalSection(&g_lock);
            continue;
        }
        char a[2 * 256 + 1], b[2 * 256 + 1];
        EnterCriticalSection(&g_lock);
        fprintf(g_out, "ret\t%lu\t%lu\t%d\t%s\t%#lx\t%#lx\n", fr->seq, GetCurrentThreadId(), t->depth, f->name,
                c->Eax, c->Edx);
        if (g_snap)
            for (int k = 0; k < NSLOT; k++) {
                if (!fr->ptr[k] || !readable(fr->ptr[k], g_snap)) continue;
                const BYTE *old = fr->snap + k * g_snap, *now = (const BYTE *)fr->ptr[k];
                for (int i = 0; i < g_snap; i += 4) {
                    if (!memcmp(old + i, now + i, 4)) continue;
                    hexdump(a, old + i, 4);
                    hexdump(b, now + i, 4);
                    fprintf(g_out, "wr\t%lu\t%s\t%d\t%s\t%s\n", fr->seq, slot_name(k), i, a, b);
                }
            }
        LeaveCriticalSection(&g_lock);
        c->Eip = fr->ret;
        return;
    }
    /* no frame: should not happen; stop the process rather than run at the trap */
    fprintf(g_out, "error\ttrap without a frame (thread %lu)\n", GetCurrentThreadId());
    fflush(g_out);
    ExitProcess(3);
}

static LONG CALLBACK handler(EXCEPTION_POINTERS *e) {
    CONTEXT *c = e->ContextRecord;
    DWORD code = e->ExceptionRecord->ExceptionCode;
    DWORD at = (DWORD)e->ExceptionRecord->ExceptionAddress;
    if (code == EXCEPTION_BREAKPOINT) {
        if (at == (DWORD)g_trap) {
            on_ret(c);
            return EXCEPTION_CONTINUE_EXECUTION;
        }
        Fn *f = fn_find(at);
        if (!f) return EXCEPTION_CONTINUE_SEARCH;
        on_call(c, f);
        /* run the original first instruction: restore it, single-step, re-arm in the step handler */
        patch(f->addr, f->orig);
        tls()->rearm = f->addr;
        c->Eip = f->addr;
        c->EFlags |= 0x100;
        return EXCEPTION_CONTINUE_EXECUTION;
    }
    if (code == EXCEPTION_SINGLE_STEP) {
        Tls *t = tls();
        if (!t->rearm) return EXCEPTION_CONTINUE_SEARCH;
        patch(t->rearm, 0xCC);
        t->rearm = 0;
        return EXCEPTION_CONTINUE_EXECUTION;
    }
    return EXCEPTION_CONTINUE_SEARCH;
}

static void load_list(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) return;
    g_fn = calloc(MAXFN, sizeof *g_fn);
    char line[512];
    while (g_nfn < MAXFN && fgets(line, sizeof line, f)) {
        char *p = line;
        while (*p == ' ' || *p == '\t') p++;
        if (*p == '#' || *p == '\n' || !*p) continue;
        char *end;
        unsigned long a;
        size_t tok = strcspn(p, "\r\n\t ");
        char *bang = memchr(p, '!', tok);
        if (bang) {   /* dll!Export: an API entry the game calls (S4's plat baseline: kernel32!CreateFileA) */
            char dll[64], sym[128];
            size_t dn = (size_t)(bang - p), sn = tok - dn - 1;
            if (!dn || dn >= sizeof dll || !sn || sn >= sizeof sym) continue;
            memcpy(dll, p, dn); dll[dn] = 0;
            memcpy(sym, bang + 1, sn); sym[sn] = 0;
            HMODULE m = GetModuleHandleA(dll);
            if (!m) m = LoadLibraryA(dll);
            FARPROC fp = m ? GetProcAddress(m, sym) : NULL;
            if (!fp) continue;
            a = (unsigned long)(DWORD_PTR)fp;
            end = p + tok;
            if (!g_fn) continue;
            Fn *fn = &g_fn[g_nfn++];
            fn->addr = a;
            while (*end == ' ' || *end == '\t') end++;
            size_t n2 = strcspn(end, "\r\n\t ");
            if (!n2) { end = bang + 1; n2 = sn; }   /* named after the export unless a name follows */
            if (n2 >= sizeof fn->name) n2 = sizeof fn->name - 1;
            memcpy(fn->name, end, n2); fn->name[n2] = 0;
            continue;
        }
        a = strtoul(p, &end, 16);
        if (end == p) continue;
        Fn *fn = &g_fn[g_nfn++];
        fn->addr = a;
        while (*end == ' ' || *end == '\t') end++;
        size_t n = strcspn(end, "\r\n\t ");
        if (!n) snprintf(fn->name, sizeof fn->name, "0x%08lx", a);
        else { if (n >= sizeof fn->name) n = sizeof fn->name - 1; memcpy(fn->name, end, n); fn->name[n] = 0; }
    }
    fclose(f);
    qsort(g_fn, g_nfn, sizeof *g_fn, fn_cmp);
}

static void start(void) {
    char buf[MAX_PATH];
    InitializeCriticalSection(&g_lock);
    g_tls = TlsAlloc();
    if (GetEnvironmentVariableA("FNVTRACE_MAX", buf, sizeof buf)) g_max = strtol(buf, 0, 0);
    if (GetEnvironmentVariableA("FNVTRACE_SNAP", buf, sizeof buf)) g_snap = (int)strtol(buf, 0, 0) & ~3;
    if (GetEnvironmentVariableA("FNVTRACE_DEREF", buf, sizeof buf)) g_deref = (int)strtol(buf, 0, 0);
    if (g_snap > 256) g_snap = 256;
    if (g_deref > 256) g_deref = 256;
    if (!GetEnvironmentVariableA("FNVTRACE_OUT", buf, sizeof buf)) strcpy(buf, "fnvtrace.tsv");
    g_out = fopen(buf, "w");
    if (!g_out) return;
    setvbuf(g_out, NULL, _IOFBF, 1 << 16);
    if (GetEnvironmentVariableA("FNVTRACE_LIST", buf, sizeof buf)) load_list(buf);
    g_trap = VirtualAlloc(NULL, 4096, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    g_trap[0] = 0xCC;
    AddVectoredExceptionHandler(1, handler);
    int armed = 0;
    for (int i = 0; i < g_nfn; i++) {
        if (!readable(g_fn[i].addr, 1)) continue;
        g_fn[i].orig = *(BYTE *)g_fn[i].addr;
        patch(g_fn[i].addr, 0xCC);
        armed++;
    }
    fprintf(g_out, "# fnvtrace: %d functions listed, %d armed\n", g_nfn, armed);
    fflush(g_out);
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD why, LPVOID r) {
    (void)h; (void)r;
    if (why == DLL_PROCESS_ATTACH) start();
    if (why == DLL_PROCESS_DETACH && g_out) fflush(g_out);
    return TRUE;
}
/* the game may end with ExitProcess before DLL_PROCESS_DETACH reaches us under some loaders */
__declspec(dllexport) void fnvtrace_flush(void) { if (g_out) fflush(g_out); }

#else   /* the launcher */

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: fnvtrace.exe <game exe> [args...]\n");
        return 2;
    }
    char cmd[4096] = "", dll[MAX_PATH], dir[MAX_PATH];
    for (int i = 1; i < argc; i++) {
        strncat(cmd, "\"", sizeof cmd - strlen(cmd) - 1);
        strncat(cmd, argv[i], sizeof cmd - strlen(cmd) - 1);
        strncat(cmd, i + 1 < argc ? "\" " : "\"", sizeof cmd - strlen(cmd) - 1);
    }
    GetModuleFileNameA(NULL, dll, sizeof dll);
    char *s = strrchr(dll, '\\');
    strcpy(s ? s + 1 : dll, "fnvtrace.dll");
    strncpy(dir, argv[1], sizeof dir - 1);
    dir[sizeof dir - 1] = 0;
    s = strrchr(dir, '\\');
    if (!s) s = strrchr(dir, '/');
    if (s) *s = 0; else strcpy(dir, ".");
    STARTUPINFOA si = {sizeof si};
    PROCESS_INFORMATION pi;
    if (!CreateProcessA(argv[1], cmd, NULL, NULL, FALSE, CREATE_SUSPENDED, NULL, dir, &si, &pi)) {
        fprintf(stderr, "fnvtrace: CreateProcess %s: error %lu\n", argv[1], GetLastError());
        return 1;
    }
    /* The loader of a process created suspended has not run yet, so LoadLibraryA in a remote thread fails
     * (STATUS_DLL_NOT_FOUND). Park the main thread at the entry point instead (jmp $), let the loader finish, inject
     * while it spins there, then put the entry's bytes back: the DLL's hooks are in place before the first
     * instruction of the game's own code. */
    DWORD base = 0x00400000, e_lfanew = 0, entry_rva = 0;
    ReadProcessMemory(pi.hProcess, (void *)(base + 0x3c), &e_lfanew, 4, NULL);
    ReadProcessMemory(pi.hProcess, (void *)(base + e_lfanew + 0x28), &entry_rva, 4, NULL);
    BYTE *entry = (BYTE *)(base + entry_rva), saved[2], spin[2] = {0xEB, 0xFE};
    DWORD old;
    if (!entry_rva || !ReadProcessMemory(pi.hProcess, entry, saved, 2, NULL)) {
        fprintf(stderr, "fnvtrace: cannot read the entry point\n");
        TerminateProcess(pi.hProcess, 1);
        return 1;
    }
    VirtualProtectEx(pi.hProcess, entry, 2, PAGE_EXECUTE_READWRITE, &old);
    WriteProcessMemory(pi.hProcess, entry, spin, 2, NULL);
    FlushInstructionCache(pi.hProcess, entry, 2);
    ResumeThread(pi.hThread);
    CONTEXT ctx;
    for (int i = 0; i < 9000; i++) {   /* up to 90 s (a fresh prefix initialises first) */
        Sleep(10);
        SuspendThread(pi.hThread);
        ctx.ContextFlags = CONTEXT_CONTROL;
        GetThreadContext(pi.hThread, &ctx);
        if (ctx.Eip == (DWORD)entry) break;
        ResumeThread(pi.hThread);
    }
    if (ctx.Eip != (DWORD)entry) {
        DWORD code = 0;
        GetExitCodeProcess(pi.hProcess, &code);
        fprintf(stderr, "fnvtrace: the game never reached its entry point %p (last eip %#lx, process %s %#lx)\n",
                entry, ctx.Eip, code == STILL_ACTIVE ? "running" : "ended", code);
        TerminateProcess(pi.hProcess, 1);
        return 1;
    }
    void *mem = VirtualAllocEx(pi.hProcess, NULL, sizeof dll, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    WriteProcessMemory(pi.hProcess, mem, dll, strlen(dll) + 1, NULL);
    HANDLE th = CreateRemoteThread(pi.hProcess, NULL, 0,
                                   (LPTHREAD_START_ROUTINE)GetProcAddress(GetModuleHandleA("kernel32"), "LoadLibraryA"),
                                   mem, 0, NULL);
    if (!th) {
        fprintf(stderr, "fnvtrace: CreateRemoteThread: error %lu\n", GetLastError());
        TerminateProcess(pi.hProcess, 1);
        return 1;
    }
    WaitForSingleObject(th, INFINITE);
    DWORD loaded = 0;
    GetExitCodeThread(th, &loaded);
    if (!loaded) {
        fprintf(stderr, "fnvtrace: LoadLibraryA(%s) failed in the game\n", dll);
        TerminateProcess(pi.hProcess, 1);
        return 1;
    }
    WriteProcessMemory(pi.hProcess, entry, saved, 2, NULL);
    VirtualProtectEx(pi.hProcess, entry, 2, old, &old);
    FlushInstructionCache(pi.hProcess, entry, 2);
    fprintf(stderr, "fnvtrace: %s loaded at %#lx, resuming at the entry point %p\n", dll, loaded, entry);
    ResumeThread(pi.hThread);
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD rc = 0;
    GetExitCodeProcess(pi.hProcess, &rc);
    return (int)rc;
}
#endif
