// fnv-nx native renderer: replay a capture on the GL reference backend (host).
//   replay_gl <capture> [out dir]     writes <out dir>/rb<N>.rgba ("NXRGBA w h") for every readback record
#include "replay.h"
#include "../gl/nxgl.h"
#include <stdio.h>
#include <stdlib.h>

static const char* g_out;
static void on_rb(void*, uint32_t i, const NxReadback* r, const uint8_t* rgba) {
    if (!g_out) return;
    char path[1024];
    snprintf(path, sizeof path, "%s/rb%03u.rgba", g_out, i);
    FILE* o = fopen(path, "wb");
    if (!o) return;
    fprintf(o, "NXRGBA %u %u\n", r->w, r->h);
    fwrite(rgba, 1, (size_t)r->w * r->h * 4, o);
    fclose(o);
}

int main(int argc, char** argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s <capture> [out dir]\n", argv[0]); return 2; }
    g_out = argc > 2 ? argv[2] : nullptr;
    FILE* f = fopen(argv[1], "rb");
    if (!f) { perror(argv[1]); return 2; }
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t* d = (uint8_t*)malloc(n);
    if (fread(d, 1, n, f) != (size_t)n) return 2;
    fclose(f);
    char err[512];
    const NxGpuApi* api = nxgl_create(0, 0, err, sizeof err);
    if (!api) { fprintf(stderr, "nxgl: %s\n", err); return 2; }
    NxReplayStats st;
    if (nx_replay(api, d, n, &st, on_rb, nullptr, err, sizeof err)) { fprintf(stderr, "replay: %s\n", err); return 1; }
    printf("replay: %u frames, %u images, %u shaders, %u samplers, %u readbacks, %u differ from the capture\n",
           st.frames, st.images, st.shaders, st.samplers, st.readbacks, st.readbackMismatches);
    nxgl_destroy();
    return st.readbackMismatches ? 1 : 0;
}
