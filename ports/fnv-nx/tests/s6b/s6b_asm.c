/*
 * s6b_asm - assemble a D3D9 shader-assembly test file to bytecode with mojoshader_assembler.
 *   s6b_asm IN.asm OUT.bin
 * Lines "// ctab: NAME SET REG COUNT CLASS TYPE ROWS COLS" (SET float|int|bool|sampler,
 * CLASS scalar|vector|matrix_rows|matrix_columns|object, TYPE float|int|bool|sampler2d|
 * samplercube|sampler3d) add a CTAB symbol, so constant-table preservation can be tested.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "mojoshader.h"
#include "mojoshader_assembler.h"

static int lookup(const char *s, const char *const *names, int n)
{
    for (int i = 0; i < n; i++) if (!strcmp(s, names[i])) return i;
    return -1;
}

int main(int argc, char **argv)
{
    if (argc != 3) { fprintf(stderr, "usage: s6b_asm IN.asm OUT.bin\n"); return 2; }
    FILE *f = fopen(argv[1], "rb");
    if (!f) { perror(argv[1]); return 1; }
    fseek(f, 0, SEEK_END); long n = ftell(f); rewind(f);
    char *src = (char *)calloc(1, (size_t)n + 1);
    if (fread(src, 1, (size_t)n, f) != (size_t)n) return 1;
    fclose(f);

    static const char *const sets[] = { "bool", "int", "float", "sampler" };
    static const char *const classes[] = { "scalar", "vector", "matrix_rows", "matrix_columns", "object" };
    static const char *const types[] = { "void", "bool", "int", "float", "string", "texture", "texture1d", "texture2d",
                                         "texture3d", "texturecube", "sampler", "sampler1d", "sampler2d", "sampler3d", "samplercube" };
    MOJOSHADER_symbol syms[64];
    char names[64][64];
    unsigned nsym = 0;
    for (const char *p = src; (p = strstr(p, "// ctab:")) && nsym < 64; p++) {
        char set[32], cls[32], typ[32];
        unsigned reg, cnt, rows, cols;
        if (sscanf(p + 8, "%63s %31s %u %u %31s %31s %u %u", names[nsym], set, &reg, &cnt, cls, typ, &rows, &cols) != 8) {
            fprintf(stderr, "bad ctab line\n"); return 1;
        }
        MOJOSHADER_symbol *y = &syms[nsym];
        memset(y, 0, sizeof *y);
        y->name = names[nsym];
        y->register_set = (MOJOSHADER_symbolRegisterSet)lookup(set, sets, 4);
        y->register_index = reg; y->register_count = cnt;
        y->info.parameter_class = (MOJOSHADER_symbolClass)lookup(cls, classes, 5);
        y->info.parameter_type = (MOJOSHADER_symbolType)lookup(typ, types, 15);
        y->info.rows = rows; y->info.columns = cols; y->info.elements = 1;
        if ((int)y->register_set < 0 || (int)y->info.parameter_class < 0 || (int)y->info.parameter_type < 0) {
            fprintf(stderr, "bad ctab field in %s\n", names[nsym]); return 1;
        }
        nsym++;
    }

    const MOJOSHADER_parseData *pd = MOJOSHADER_assemble(argv[1], src, (unsigned)n, NULL, 0,
        nsym ? syms : NULL, nsym, NULL, 0, NULL, NULL, NULL, NULL, NULL);
    if (pd->error_count) {
        for (int i = 0; i < pd->error_count; i++)
            fprintf(stderr, "%s:%d: %s\n", pd->errors[i].filename ? pd->errors[i].filename : argv[1],
                    pd->errors[i].error_position, pd->errors[i].error);
        return 1;
    }
    FILE *o = fopen(argv[2], "wb");
    if (!o || fwrite(pd->output, 1, (size_t)pd->output_len, o) != (size_t)pd->output_len) { perror(argv[2]); return 1; }
    fclose(o);
    MOJOSHADER_freeParseData(pd);
    free(src);
    return 0;
}
