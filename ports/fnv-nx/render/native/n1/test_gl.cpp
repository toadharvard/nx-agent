// fnv-nx native renderer, N1: the renderer self-test on the GL reference backend (host).
#include "selftest.h"
#include "../gl/nxgl.h"
#include <stdio.h>
int main() {
    char err[256];
    const NxGpuApi* api = nxgl_create(0, 0, err, sizeof err);
    if (!api) { fprintf(stderr, "nxgl: %s\n", err); return 2; }
    nx::Backend* be = nx::GpuBackend(api);
    static char log[8192];
    int checks = 0, f = n1_selftest(be, log, sizeof log, &checks);
    printf("%sn1 renderer: %d checks, %d failed\n", log, checks, f);
    if (nxgl_last_error()[0]) printf("nxgl: %s\n", nxgl_last_error());
    return f ? 1 : 0;
}
