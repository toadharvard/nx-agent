/*
 * frontend.c - D3D9 bytecode front end: validation and reflection.
 *
 * Uses MojoShader's parser with the "bytecode" profile (no code generation) so reflection is
 * independent of the GLSL backend. Reflection JSON (one object per shader):
 *   hash, stage, model, d3d_instructions,
 *   constants: [{set: float|int|bool, reg}]   registers read directly (relatively addressed
 *              ranges come from the CTAB; the front end mirrors the whole constant file anyway)
 *   literals:  [{set, reg, value}]                              def/defi/defb
 *   samplers:  [{reg, type: 2d|cube|volume, texbem}]
 *   inputs / outputs: [{usage, index, name, location}]          location from fnvnx_dk_layout.h
 *   ctab: [{name, set, reg, count, class, type, rows, columns, elements}]   from the CTAB comment
 */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "mojoshader.h"
#include "backend.h"
#include "fnvnx_dk_layout.h"

typedef struct sb { char *p; size_t n, cap; } sb;
static void sb_printf(sb *s, const char *fmt, ...)
{
    va_list ap;
    for (;;) {
        size_t room = s->cap - s->n;
        va_start(ap, fmt);
        int k = vsnprintf(s->p ? s->p + s->n : NULL, s->p ? room : 0, fmt, ap);
        va_end(ap);
        if (k < 0) return;
        if (s->p && (size_t)k < room) { s->n += (size_t)k; return; }
        size_t nc = s->cap ? s->cap * 2 : 1024;
        while (nc < s->n + (size_t)k + 1) nc *= 2;
        char *np = (char *)realloc(s->p, nc);
        if (!np) return;
        s->p = np; s->cap = nc;
    }
}
static void sb_str(sb *s, const char *str)
{
    sb_printf(s, "\"");
    for (const char *c = str ? str : ""; *c; c++) {
        if (*c == '"' || *c == '\\') sb_printf(s, "\\%c", *c);
        else if ((unsigned char)*c < 0x20) sb_printf(s, "\\u%04x", (unsigned char)*c);
        else sb_printf(s, "%c", *c);
    }
    sb_printf(s, "\"");
}

static const char *usage_name(int u)
{
    static const char *n[] = { "POSITION", "BLENDWEIGHT", "BLENDINDICES", "NORMAL", "PSIZE", "TEXCOORD",
                               "TANGENT", "BINORMAL", "TESSFACTOR", "POSITIONT", "COLOR", "FOG", "DEPTH", "SAMPLE" };
    return (u >= 0 && u < (int)(sizeof n / sizeof n[0])) ? n[u] : "UNKNOWN";
}
static const char *uset(int t) { return t == MOJOSHADER_UNIFORM_FLOAT ? "float" : t == MOJOSHADER_UNIFORM_INT ? "int" : "bool"; }
static const char *symset(int t) { static const char *n[] = { "bool", "int", "float", "sampler" }; return (t >= 0 && t < 4) ? n[t] : "?"; }
static const char *symclass(int t) { static const char *n[] = { "scalar", "vector", "matrix_rows", "matrix_columns", "object", "struct" }; return (t >= 0 && t < 6) ? n[t] : "?"; }
static const char *symtype(int t)
{
    static const char *n[] = { "void", "bool", "int", "float", "string", "texture", "texture1d", "texture2d", "texture3d",
                               "texturecube", "sampler", "sampler1d", "sampler2d", "sampler3d", "samplercube",
                               "pixelshader", "vertexshader", "pixelfragment", "vertexfragment", "unsupported" };
    return (t >= 0 && t < (int)(sizeof n / sizeof n[0])) ? n[t] : "?";
}

static void emit_io(sb *s, const char *key, const MOJOSHADER_attribute *a, int count, int is_vs_input, int is_vs)
{
    sb_printf(s, ",\n  \"%s\": [", key);
    for (int i = 0; i < count; i++) {
        int loc;
        if (is_vs_input) loc = fnvnx_dk_attrib_location(a[i].usage, a[i].index);
        else if (a[i].usage == MOJOSHADER_USAGE_POSITION && a[i].index == 0 && is_vs) loc = -2; /* gl_Position */
        else if (a[i].usage == MOJOSHADER_USAGE_COLOR && !is_vs_input && strstr(a[i].name ? a[i].name : "", "oC")) loc = a[i].index;
        else loc = fnvnx_dk_varying_location(a[i].usage, a[i].index);
        sb_printf(s, "%s\n    {\"usage\": \"%s\", \"index\": %d, \"name\": ", i ? "," : "", usage_name(a[i].usage), a[i].index);
        sb_str(s, a[i].name);
        sb_printf(s, ", \"location\": %d}", loc);
    }
    sb_printf(s, "%s]", count ? "\n  " : "");
}

int fnvnx_frontend_parse(const void *bc, size_t n, fnvnx_reflection *refl, char **json, char *err, size_t errlen)
{
    memset(refl, 0, sizeof(*refl));
    *json = NULL;
    if (n < 4 || (n & 3)) { snprintf(err, errlen, "bytecode size %zu is not a positive multiple of 4", n); return -1; }
    const MOJOSHADER_parseData *pd = MOJOSHADER_parse(MOJOSHADER_PROFILE_BYTECODE, "main",
        (const unsigned char *)bc, (unsigned int)n, NULL, 0, NULL, 0, NULL, NULL, NULL);
    if (!pd) { snprintf(err, errlen, "out of memory"); return -1; }
    if (pd->error_count > 0) {
        snprintf(err, errlen, "front end: %s (pos %d)", pd->errors[0].error, pd->errors[0].error_position);
        MOJOSHADER_freeParseData(pd);
        return -1;
    }
    refl->stage = pd->shader_type == MOJOSHADER_TYPE_VERTEX ? FNVNX_STAGE_VERTEX
                : pd->shader_type == MOJOSHADER_TYPE_PIXEL ? FNVNX_STAGE_PIXEL : FNVNX_STAGE_UNKNOWN;
    refl->major = pd->major_ver; refl->minor = pd->minor_ver;
    refl->d3d_instructions = (uint32_t)pd->instruction_count;
    refl->frontend_parse = pd;
    if (refl->stage == FNVNX_STAGE_UNKNOWN) { snprintf(err, errlen, "not a vertex or pixel shader"); fnvnx_frontend_release(refl); return -1; }

    const int is_vs = refl->stage == FNVNX_STAGE_VERTEX;
    sb s = { 0 };
    sb_printf(&s, "{\n  \"hash\": \"%016llx\",\n  \"stage\": \"%s\",\n  \"model\": \"%s_%d_%d\",\n  \"d3d_instructions\": %d",
              (unsigned long long)fnvnx_shader_hash(bc, n), is_vs ? "vertex" : "pixel", is_vs ? "vs" : "ps",
              pd->major_ver, pd->minor_ver, pd->instruction_count);
    sb_printf(&s, ",\n  \"layout\": {\"ubo_float\": %d, \"ubo_intbool\": %d, \"ubo_driver\": %d}",
              FNVNX_DK_UBO_FLOAT, FNVNX_DK_UBO_INTBOOL, FNVNX_DK_UBO_DRIVER);
    sb_printf(&s, ",\n  \"constants\": [");
    for (int i = 0; i < pd->uniform_count; i++) {
        const MOJOSHADER_uniform *u = &pd->uniforms[i];
        sb_printf(&s, "%s\n    {\"set\": \"%s\", \"reg\": %d}", i ? "," : "", uset(u->type), u->index);
    }
    sb_printf(&s, "%s]", pd->uniform_count ? "\n  " : "");
    sb_printf(&s, ",\n  \"literals\": [");
    for (int i = 0; i < pd->constant_count; i++) {
        const MOJOSHADER_constant *c = &pd->constants[i];
        sb_printf(&s, "%s\n    {\"set\": \"%s\", \"reg\": %d, \"value\": ", i ? "," : "", uset(c->type), c->index);
        if (c->type == MOJOSHADER_UNIFORM_FLOAT) sb_printf(&s, "[%.9g, %.9g, %.9g, %.9g]}", c->value.f[0], c->value.f[1], c->value.f[2], c->value.f[3]);
        else if (c->type == MOJOSHADER_UNIFORM_INT) sb_printf(&s, "[%d, %d, %d, %d]}", c->value.i[0], c->value.i[1], c->value.i[2], c->value.i[3]);
        else sb_printf(&s, "%s}", c->value.b ? "true" : "false");
    }
    sb_printf(&s, "%s]", pd->constant_count ? "\n  " : "");
    sb_printf(&s, ",\n  \"samplers\": [");
    for (int i = 0; i < pd->sampler_count; i++) {
        const MOJOSHADER_sampler *m = &pd->samplers[i];
        const char *t = m->type == MOJOSHADER_SAMPLER_CUBE ? "cube" : m->type == MOJOSHADER_SAMPLER_VOLUME ? "volume" : "2d";
        sb_printf(&s, "%s\n    {\"reg\": %d, \"type\": \"%s\", \"binding\": %d, \"texbem\": %s}", i ? "," : "", m->index, t, m->index, m->texbem ? "true" : "false");
    }
    sb_printf(&s, "%s]", pd->sampler_count ? "\n  " : "");
    emit_io(&s, "inputs", pd->attributes, pd->attribute_count, is_vs, is_vs);
    emit_io(&s, "outputs", pd->outputs, pd->output_count, 0, is_vs);
    sb_printf(&s, ",\n  \"ctab\": [");
    for (int i = 0; i < pd->symbol_count; i++) {
        const MOJOSHADER_symbol *y = &pd->symbols[i];
        sb_printf(&s, "%s\n    {\"name\": ", i ? "," : "");
        sb_str(&s, y->name);
        sb_printf(&s, ", \"set\": \"%s\", \"reg\": %u, \"count\": %u, \"class\": \"%s\", \"type\": \"%s\", \"rows\": %u, \"columns\": %u, \"elements\": %u}",
                  symset(y->register_set), y->register_index, y->register_count, symclass(y->info.parameter_class),
                  symtype(y->info.parameter_type), y->info.rows, y->info.columns, y->info.elements);
    }
    sb_printf(&s, "%s]\n}\n", pd->symbol_count ? "\n  " : "");
    if (!s.p) { snprintf(err, errlen, "out of memory"); fnvnx_frontend_release(refl); return -1; }
    *json = s.p;
    return 0;
}

int fnvnx_shader_ctab(const void *bc, size_t n, fnvnx_ctab_entry **out, char *err, size_t errlen)
{
    fnvnx_reflection refl;
    char *json;
    *out = NULL;
    if (fnvnx_frontend_parse(bc, n, &refl, &json, err, errlen)) return -1;
    free(json);
    const MOJOSHADER_parseData *pd = (const MOJOSHADER_parseData *)refl.frontend_parse;
    int k = pd->symbol_count;
    fnvnx_ctab_entry *e = (fnvnx_ctab_entry *)calloc(k ? (size_t)k : 1, sizeof *e);
    if (!e) { fnvnx_frontend_release(&refl); snprintf(err, errlen, "out of memory"); return -1; }
    for (int i = 0; i < k; i++) {
        const MOJOSHADER_symbol *y = &pd->symbols[i];
        snprintf(e[i].name, sizeof e[i].name, "%s", y->name ? y->name : "");
        e[i].set = symset(y->register_set); e[i].reg = y->register_index; e[i].count = y->register_count;
        e[i].cls = symclass(y->info.parameter_class); e[i].type = symtype(y->info.parameter_type);
        e[i].rows = y->info.rows; e[i].columns = y->info.columns; e[i].elements = y->info.elements;
    }
    fnvnx_frontend_release(&refl);
    *out = e;
    return k;
}

void fnvnx_frontend_release(fnvnx_reflection *refl)
{
    if (refl->frontend_parse) MOJOSHADER_freeParseData((const MOJOSHADER_parseData *)refl->frontend_parse);
    refl->frontend_parse = NULL;
}
