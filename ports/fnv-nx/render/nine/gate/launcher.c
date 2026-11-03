/* fnv-nx host gate launcher (stream S6c-nine): a PIE executable that embeds the Python interpreter, so nothing of
 * the process is mapped low. The system python3 here is a non-PIE executable mapped at 0x00200000-0x01dxxxxx,
 * which covers FalloutNV.exe's image window 0x00400000-0x02100000; test/gate.py --game needs that window (S3's data
 * image) at the same address in the host process as in the guest, like every other guest region.
 *
 *   gate_launcher [python options] script.py [args]      (exactly like python3; e.g. -I test/gate.py --game ...)
 *
 * Before starting the interpreter it checks that the window and the other gate ranges are free. */
#define _GNU_SOURCE
#include <Python.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const struct { unsigned long lo, hi; const char* what; } k_ranges[] = {
    {0x00400000ul, 0x02100000ul, "FalloutNV.exe image window (S3 data image)"},
    /* tests/s5/emu_guest.py: a fixed guest at its link base (0x10000000 by default), a relocatable one at
     * 0x05000000; heap 0x30000000, stacks below 0x3E000000, host page 0x3F000000 */
    {0x02100000ul, 0x40000000ul, "guest image, heap, stacks, host page"},
    {0x40000000ul, 0xC0000000ul, "gate low-VA arena (gate/gate_alloc.c)"},
};

static int check_free(void) {
    FILE* f = fopen("/proc/self/maps", "r");
    if (!f) return 0;
    char line[512];
    int bad = 0;
    while (fgets(line, sizeof line, f)) {
        unsigned long lo, hi;
        if (sscanf(line, "%lx-%lx", &lo, &hi) != 2) continue;
        for (size_t i = 0; i < sizeof k_ranges / sizeof k_ranges[0]; i++)
            if (lo < k_ranges[i].hi && hi > k_ranges[i].lo) {
                fprintf(stderr, "gate_launcher: %s overlaps %#lx-%#lx: %s", k_ranges[i].what, k_ranges[i].lo,
                        k_ranges[i].hi, line);
                bad = 1;
            }
    }
    fclose(f);
    return bad;
}

#ifndef GATE_PYTHON_HOME
#define GATE_PYTHON_HOME "/usr/local"     /* the installation that has unicorn and numpy (build.sh passes it) */
#endif

int main(int argc, char** argv) {
    if (check_free()) return 70;
    setenv("FNVNX_GATE_LAUNCHER", "1", 1);   /* gate.py: the image window is shareable */
    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    /* an embedded interpreter derives its prefix from the executable's location: pin it to the installation */
    PyStatus st = PyConfig_SetBytesString(&config, &config.home, GATE_PYTHON_HOME);
    if (!PyStatus_Exception(st)) st = PyConfig_SetBytesArgv(&config, argc, argv);
    if (!PyStatus_Exception(st)) st = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(st)) Py_ExitStatusException(st);
    return Py_RunMain();
}
