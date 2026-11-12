/* probe_dynsites - host probe of the module/name lookups: what do the platform's own GetModuleHandleA, LoadLibraryA and
 * GetProcAddress answer? Input lines on stdin (TAB-separated), one answer line each, in order:
 *
 *   MODS                    -> `MODS<TAB>name` per system module of the table (fnvnx_win32_system_module), then `END`
 *   HANDLE<TAB>name         -> `HANDLE<TAB>name<TAB>present|absent<TAB>lasterror-if-absent`   (GetModuleHandleA(name))
 *   LOAD<TAB>name           -> `LOAD<TAB>name<TAB>present|absent<TAB>lasterror-if-absent`     (LoadLibraryA(name))
 *   GPA<TAB>module<TAB>name -> `GPA<TAB>module<TAB>name<TAB>yes|no|nomodule`  (GetProcAddress(GetModuleHandleA(module), name))
 *
 * Built by `make -C tests/s4 probe` from the platform objects of the host build; manifest/check_dynsites.py
 * drives it, so the answers are those of module.cpp / exports.cpp themselves, not a re-implementation of their rules. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef void *HMODULE_;
extern "C" {
const char *fnvnx_win32_system_module(unsigned i);
HMODULE_ GetModuleHandleA(const char *);
HMODULE_ LoadLibraryA(const char *);
void *GetProcAddress(HMODULE_, const char *);
unsigned long GetLastError(void);
void SetLastError(unsigned long);
}

int main()
{
    char line[2048];
    while (fgets(line, sizeof line, stdin)) {
        line[strcspn(line, "\r\n")] = 0;
        if (!line[0])
            continue;
        char *f[4] = { line, NULL, NULL, NULL };
        int n = 1;
        for (char *p = line; *p && n < 4; p++)
            if (*p == '\t') {
                *p = 0;
                f[n++] = p + 1;
            }
        if (!strcmp(f[0], "MODS")) {
            for (unsigned i = 0; fnvnx_win32_system_module(i); i++)
                printf("MODS\t%s\n", fnvnx_win32_system_module(i));
            printf("END\n");
        } else if ((!strcmp(f[0], "HANDLE") || !strcmp(f[0], "LOAD")) && f[1]) {
            SetLastError(0);
            HMODULE_ h = f[0][0] == 'H' ? GetModuleHandleA(f[1]) : LoadLibraryA(f[1]);
            if (h)
                printf("%s\t%s\tpresent\n", f[0], f[1]);
            else
                printf("%s\t%s\tabsent\t%lu\n", f[0], f[1], GetLastError());
        } else if (!strcmp(f[0], "GPA") && f[1] && f[2]) {
            HMODULE_ h = GetModuleHandleA(f[1]);
            printf("GPA\t%s\t%s\t%s\n", f[1], f[2], !h ? "nomodule" : GetProcAddress(h, f[2]) ? "yes" : "no");
        } else {
            fprintf(stderr, "probe_dynsites: bad line: %s\n", line);
            return 2;
        }
        fflush(stdout);
    }
    return 0;
}
