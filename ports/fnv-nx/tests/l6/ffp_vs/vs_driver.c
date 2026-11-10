/* vs_driver [--dump|<mutation>] < records > floats : CPU reference (nx_ffp_vs_eval) over a stream of VsRec; prints
 * sizeof(VsRec)/4 on stderr. Writes OUT_WORDS floats per record on stdout. `--words` prints the record word count. */
#include "vs_common.h"
int main(int argc, char **argv)
{
    if (argc > 1 && !strcmp(argv[1], "--words")) { printf("%zu\n", sizeof(VsRec) / 4); return 0; }
    VsRec r; NxFfpVsState st; NxFfpVsIn in; NxFfpVsOut o; float f[OUT_WORDS];
    while (fread(&r, sizeof r, 1, stdin) == 1) {
        rec_expand(&r, &st, &in);
        nx_ffp_vs_eval(&st, (const float (*)[16])g_palette, r.posfix, &in, &o);
        out_flat(&o, f); fwrite(f, 4, OUT_WORDS, stdout);
    }
    return 0;
}
