/* main.cpp - the -m32 host tests (tests/s4/m32): platform/ + S7's audio/input + render/nine/d3dx built for i386,
 * whose ILP32 data model and struct layouts equal the game's (the shim's D3D9 static_asserts hold there).
 *
 *   s4_m32                     run the TESTs of tests/s4/m32/*.cpp
 *   s4_m32 --iat imports.txt   resolve every import slot of the exe (tests/s4/iat/pe_imports.py output) as the
 *                              guest boot does (toolchain/game/boot.cpp resolve_import) and list the slots with
 *                              no export; prints "iat: <missing> of <slots> slots have no export" (S4's metric) */
#include "../s4_test.h"

#include <stdint.h>
#include <stdlib.h>

int s4_failures, s4_checks;
jmp_buf s4_case_jmp;   /* a failed CHECK on the case's thread ends the case (s4_test.h) */
pthread_t s4_case_thread;
int s4_case_armed;
s4_case *s4_cases;

static char g_root[1024];
const char *s4_root(void) { return g_root; }

void s4_write_file(const char *win_path, const char *data)
{
    HANDLE h = CreateFileA(win_path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    if (h == INVALID_HANDLE_VALUE)
        return;
    DWORD put = 0;
    CHECK(WriteFile(h, data, (DWORD)strlen(data), &put, NULL));
    CHECK_EQ(put, strlen(data));
    CloseHandle(h);
}

extern "C" void fnvnx_d3d9_register(void) __attribute__((weak));   /* the null device's d3d9.dll module */
extern "C" void fnvnx_audio_input_register(void);

static int iat(const char *path)
{
    if (getenv("FNVNX_IAT_LIST"))   /* addresses below are relative to iat() itself (PIE) */
        printf("base\t%p\n", (void *)&iat);
    static struct { unsigned slot; char dll[128], what[256]; } seen[1024];
    int nseen = 0;
    FILE *f = fopen(path, "r");
    if (!f) {
        perror(path);
        return 2;
    }
    char line[512];
    int slots = 0, missing = 0;
    while (fgets(line, sizeof line, f)) {
        char slot[32], dll[128], what[256];
        if (sscanf(line, "%31s %127s %255s", slot, dll, what) != 3)
            continue;
        slots++;
        if (nseen < 1024) {
            seen[nseen].slot = (unsigned)strtoul(slot, NULL, 16);
            snprintf(seen[nseen].dll, sizeof seen[0].dll, "%s", dll);
            snprintf(seen[nseen].what, sizeof seen[0].what, "%s", what);
            nseen++;
        }
        void *p = what[0] == '#' ? fnvnx_win32_lookup_import(dll, NULL, (unsigned)strtoul(what + 1, NULL, 10))
                                 : fnvnx_win32_lookup_import(dll, what, FNVNX_IMPORT_BY_NAME);
        if (p && getenv("FNVNX_IAT_LIST"))
            printf("resolved\t%s\t%s\t%s\t%p\n", slot, dll, what, p);
        if (!p) {
            missing++;
            printf("missing\t%s\t%s\t%s\n", slot, dll, what);
        }
    }
    fclose(f);
    printf("iat: %d of %d slots have no export\n", missing, slots);
    return missing ? 1 : 0;
}

int main(int argc, char **argv)
{
    const char *r = getenv("FNVNX_ROOT");
    snprintf(g_root, sizeof g_root, "%s", r && *r ? r : "./fnvnx-root");
    char cmd[1200];
    snprintf(cmd, sizeof cmd, "mkdir -p '%s/game'", g_root);
    if (system(cmd) != 0)
        return 2;
    fnvnx_path_reset(g_root);
    /* the modules boot.cpp registers before the image's imports are bound */
    fnvnx_audio_input_register();
    if (fnvnx_d3d9_register)
        fnvnx_d3d9_register();
    if (argc == 3 && !strcmp(argv[1], "--iat"))
        return iat(argv[2]);
    fnvnx_set_command_line(argc, argv);
    int n = 0;
    for (s4_case *c = s4_cases; c; c = c->next)
        n++;
    s4_case **v = (s4_case **)malloc(sizeof(s4_case *) * (size_t)(n ? n : 1));
    int i = 0;
    for (s4_case *c = s4_cases; c; c = c->next)
        v[i++] = c;
    qsort(v, (size_t)n, sizeof *v, [](const void *a, const void *b) {
        return strcmp((*(s4_case *const *)a)->name, (*(s4_case *const *)b)->name);
    });
    int failed_cases = 0;
    for (i = 0; i < n; i++) {
        int before = s4_failures;
        if (setjmp(s4_case_jmp) == 0) {
            s4_case_thread = pthread_self();
            s4_case_armed = 1;
            v[i]->fn();
        }
        s4_case_armed = 0;
        int bad = s4_failures - before;
        failed_cases += bad != 0;
        printf("%-34s %s\n", v[i]->name, bad ? "FAIL" : "ok");
    }
    printf("\nm32: %d tests, %d checks, %d failed checks, %d failed tests\n", n, s4_checks, s4_failures, failed_cases);
    free(v);
    return s4_failures ? 1 : 0;
}
