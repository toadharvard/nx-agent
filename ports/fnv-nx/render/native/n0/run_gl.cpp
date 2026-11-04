// fnv-nx native renderer: runs the N0 scenes on the GL reference backend (host). Writes <out>/<scene>.rgba
// ("NXRGBA w h\n" + RGBA8 rows, top first) and prints the conformance-check results.
//   run_gl <out dir> [scene ...]
#include "scenes.h"
#include "../gl/nxgl.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char** argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s <out dir> [scene ...]\n", argv[0]); return 2; }
    char err[512];
    const NxGpuApi* api = nxgl_create(0, 0, err, sizeof err);
    if (!api) { fprintf(stderr, "nxgl: %s\n", err); return 2; }
    nx::Backend* be = nx::GpuBackend(api);
    if (!be) { fprintf(stderr, "GpuBackend failed\n"); return 2; }
    static n0::Ctx c;
    if (!n0::Init(c, be, err, sizeof err)) { fprintf(stderr, "init: %s (%s)\n", err, nxgl_last_error()); return 2; }
    static uint8_t rgba[n0::W * n0::H * 4];
    static n0::Checks k;
    static char log[16384];
    int total = 0, checks = 0;
    for (int i = 0; i < n0::kSceneCount; i++) {
        const n0::Scene& s = n0::kScenes[i];
        if (argc > 2) {
            bool want = false;
            for (int a = 2; a < argc; a++) if (!strcmp(argv[a], s.name)) want = true;
            if (!want) continue;
        }
        memset(rgba, 0, sizeof rgba);
        if (!n0::Run(c, s, rgba, k)) { printf("%-10s readback FAILED (%s)\n", s.name, nxgl_last_error()); total++; continue; }
        int f = n0::Evaluate(k, rgba, log, sizeof log);
        printf("%-10s %3d checks, %d failed, %u packets, %u state binds\n%s", s.name, k.n + (k.expectStateBinds >= 0),
               f, c.packets, c.stateBinds, log);
        total += f; checks += k.n + (k.expectStateBinds >= 0);
        char path[1024];
        snprintf(path, sizeof path, "%s/%s.rgba", argv[1], s.name);
        FILE* o = fopen(path, "wb");
        if (!o) { perror(path); return 2; }
        fprintf(o, "NXRGBA %d %d\n", n0::W, n0::H);
        fwrite(rgba, 1, sizeof rgba, o);
        fclose(o);
    }
    if (nxgl_last_error()[0]) printf("nxgl: %s\n", nxgl_last_error());
    printf("scenes: %d checks, %d failed\n", checks, total);
    nxgl_destroy();
    return total ? 1 : 0;
}
