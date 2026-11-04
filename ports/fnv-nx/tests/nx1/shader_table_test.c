/* NX1 check 9: the console's generated shader table (toolchain/nro/shaderpack.py --table) run natively.
 *   shader_table_test <dir with uam's *.dksh>
 * Built with -DFNVNX_SHADER_PACK="<pack>". Every table entry must resolve to exactly uam's DKSH bytes, with no GLSL
 * text (glsl_size 0). Built against another pack (-DWRONG), every lookup must fail: never a fallback. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "nx_shaders.h"

int main(int argc, char **argv) {
    int bad = 0;
#ifdef WRONG
    (void)argc; (void)argv;
    for (uint32_t i = 0; i < nx_shader_blob_count; i++)
        if (nx_shader_find(nx_shader_blobs[i].name, nx_shader_blobs[i].stage) >= 0) bad++;
    printf("shader_table (foreign or missing pack): %d lookups succeeded, want 0\n", bad);
    return bad != 0;
#else
    if (argc < 2) return 2;
    for (uint32_t i = 0; i < nx_shader_blob_count; i++) {
        const NxShaderBlob *b = &nx_shader_blobs[i];
        int k = nx_shader_find(b->name, b->stage);
        char p[512];
        snprintf(p, sizeof p, "%s/%s.%s.dksh", argv[1], b->name, b->stage ? "frag" : "vert");
        FILE *f = fopen(p, "rb");
        static unsigned char want[1 << 20];
        size_t n = f ? fread(want, 1, sizeof want, f) : 0;
        if (f) fclose(f);
        if (k != (int)i || !b->dksh || b->dksh_size != n || memcmp(b->dksh, want, n) || b->glsl_size != 0) {
            printf("FAIL %s.%u: find %d, size %u vs %zu\n", b->name, b->stage, k, b->dksh_size, n);
            bad++;
        }
    }
    printf("shader_table: %u shaders, %d bad\n", nx_shader_blob_count, bad);
    return bad != 0;
#endif
}
