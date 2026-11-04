// fnv-nx native renderer: the N0 scenes as an ILP32 guest on the Switch (stream S6n). Renders every scene through
// the deko3d host module (ext_call NXGPU_EXT_MODULE), writes the readbacks to sdmc:/fnvnx/n0/out/<scene>.rgba and
// the check results to sdmc:/fnvnx/n0/out/results.txt; then shows the last scene for a few seconds.
// Compare on a PC: tests/s6n/scenes.py --compare <copied out dir> --tol N
#include "scenes.h"
#include "../capture/capture.h"
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>

int main() {
    mkdir("sdmc:/fnvnx", 0777);
    mkdir("sdmc:/fnvnx/n0", 0777);
    mkdir("sdmc:/fnvnx/n0/out", 0777);
    FILE* res = fopen("sdmc:/fnvnx/n0/out/results.txt", "w");
    nx::Backend* be = nx::GpuBackend(nullptr);
    if (!be) { if (res) { fputs("GpuBackend: no answer from the nxgpu host module\n", res); fclose(res); } return 3; }
    static nx::CaptureBackend cap;   // the command streams, for host replay (capture/replay_gl)
    if (cap.Open(be, fopen("sdmc:/fnvnx/n0/out/n0.nxcap", "wb"))) be = &cap;
    static n0::Ctx c;
    char err[256];
    if (!n0::Init(c, be, err, sizeof err)) { if (res) { fprintf(res, "init: %s\n", err); fclose(res); } return 4; }
    static uint8_t rgba[n0::W * n0::H * 4];
    static n0::Checks k;
    static char log[16384];
    int total = 0, checks = 0;
    for (int i = 0; i < n0::kSceneCount; i++) {
        const n0::Scene& s = n0::kScenes[i];
        memset(rgba, 0, sizeof rgba);
        if (!n0::Run(c, s, rgba, k)) { if (res) fprintf(res, "%s: readback FAILED\n", s.name); total++; continue; }
        int f = n0::Evaluate(k, rgba, log, sizeof log);
        int nk = k.n + (k.expectStateBinds >= 0);
        if (k.hasImage) for (int p = 0; p < n0::W * n0::H; p++) nk += k.imageMask[p] != 0;
        total += f; checks += nk;
        if (res) { fprintf(res, "%-10s %3d checks, %d failed, %u state binds\n%s", s.name, k.n, f, (unsigned)c.stateBinds, log); fflush(res); }
        char path[128];
        snprintf(path, sizeof path, "sdmc:/fnvnx/n0/out/%s.rgba", s.name);
        FILE* o = fopen(path, "wb");
        if (o) { fprintf(o, "NXRGBA %d %d\n", n0::W, n0::H); fwrite(rgba, 1, sizeof rgba, o); fclose(o); }
    }
    if (res) { fprintf(res, "scenes: %d checks, %d failed\n", checks, total); fclose(res); }
    be->Shutdown();
    return total ? 1 : 0;
}
