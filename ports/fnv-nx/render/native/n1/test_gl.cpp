// fnv-nx native renderer, N1: the renderer self-test on the GL reference backend (host).
// FNVNX_TRANSLATED_PACK=<translated.nxsp> (the player's pack, render/native/tools/nxsp_pack.py) also loads that pack
// and checks every program in it has a DKSH (no runtime compile).
#include "selftest.h"
#include "../gl/nxgl.h"
#include <stdio.h>
#include <stdlib.h>
int main() {
    char err[256];
    const NxGpuApi* api = nxgl_create(0, 0, err, sizeof err);
    if (!api) { fprintf(stderr, "nxgl: %s\n", err); return 2; }
    nx::Backend* be = nx::GpuBackend(api);
    static char log[8192];
    int checks = 0, f = n1_selftest(be, log, sizeof log, &checks);
    printf("%sn1 renderer: %d checks, %d failed\n", log, checks, f);
    if (nxgl_last_error()[0]) printf("nxgl: %s\n", nxgl_last_error());
    if (const char* pk = getenv("FNVNX_TRANSLATED_PACK")) {
        FILE* fp = fopen(pk, "rb");
        if (!fp) { printf("translated pack %s: cannot open\n", pk); return 1; }
        fseek(fp, 0, SEEK_END); long n = ftell(fp); rewind(fp);
        unsigned char* d = (unsigned char*)malloc((size_t)n);
        if (!d || fread(d, 1, (size_t)n, fp) != (size_t)n) { fclose(fp); return 1; }
        fclose(fp);
        static nx::TranslatedStore st;
        const bool ok = st.Load(d, (uint32_t)n);
        uint32_t noDksh = 0, glsl = 0;
        for (uint32_t i = 0; ok && i < st.count(); i++) { noDksh += !st.program((int)i).dksh; glsl += st.program((int)i).glsl != nullptr; }
        printf("translated pack: %s, %u programs, %u without DKSH, %u with GLSL\n", ok ? "loaded" : "INVALID",
               st.count(), noDksh, glsl);
        if (!ok || noDksh) return 1;
    }
    return f ? 1 : 0;
}
