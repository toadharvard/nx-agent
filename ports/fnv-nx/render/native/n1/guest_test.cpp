// fnv-nx native renderer, N1: the renderer self-test as an ILP32 guest (console, or Unicorn: tests/s6n/emu_n0.py).
// Results: sdmc:/fnvnx/n1/results.txt
#include "selftest.h"
#include <stdio.h>
#include <sys/stat.h>
int main() {
    mkdir("sdmc:/fnvnx", 0777);
    mkdir("sdmc:/fnvnx/n1", 0777);
    FILE* o = fopen("sdmc:/fnvnx/n1/results.txt", "w");
    nx::Backend* be = nx::GpuBackend(nullptr);
    if (!be) { if (o) { fputs("no nxgpu host module\n", o); fclose(o); } return 3; }
    static char log[8192];
    int checks = 0, f = n1_selftest(be, log, sizeof log, &checks);
    if (o) { fprintf(o, "%sn1 renderer: %d checks, %d failed\n", log, checks, f); fclose(o); }
    be->Shutdown();
    return f ? 1 : 0;
}
