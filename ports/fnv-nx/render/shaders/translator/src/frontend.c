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
#include "../include/fnvnx_dk_layout.h"

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

/* vs < 3.0 oFog / oPts are registers 1 / 2 of the RASTOUT file, which the bytecode profile reports as index 1 / 2 of their usage; their
 * pair is FOG0 / PSIZE0 (D3D9: one fog and one point size per vertex), as the glsldk profile treats them. */
static int out_index(const MOJOSHADER_parseData *pd, const MOJOSHADER_attribute *a)
{
    return (pd->major_ver < 3 && (a->usage == MOJOSHADER_USAGE_FOG || a->usage == MOJOSHADER_USAGE_POINTSIZE)) ? 0 : a->index;
}

/* The varyings a stage writes (VS) or reads (PS), as FNVNX_DK_PAIR codes, for the static variant rule fnvnx_dk_vs_needs_complete() (fnvnx_dk_layout.h).
 * VS: its outputs except the built-ins POSITION0 / PSIZE0, and FOG0 always (every VS writes it, README "Varying completion"). PS: its inputs
 * (not vPos / vFace), and FOG0 for a ps < 3.0 (its epilogue reads the fog factor). Returns the count. */
static int stage_pairs(const MOJOSHADER_parseData *pd, int is_vs, unsigned char *out)
{
    int n = 0, i, q;
    #define ADDP(pr) do { int d_ = 0; for (int k_ = 0; k_ < n; k_++) if (out[k_] == (pr)) d_ = 1; if (!d_ && n < 224) out[n++] = (unsigned char)(pr); } while (0)
    if (is_vs) {
        for (i = 0; i < pd->output_count; i++) {
            const MOJOSHADER_attribute *a = &pd->outputs[i]; const int idx = out_index(pd, a);
            if (a->usage < 0 || a->usage > 13 || idx < 0 || idx > 15) continue;
            if (idx == 0 && (a->usage == MOJOSHADER_USAGE_POSITION || a->usage == MOJOSHADER_USAGE_POINTSIZE)) continue;
            ADDP(FNVNX_DK_PAIR(a->usage, idx));
        }
        ADDP(FNVNX_DK_PAIR(MOJOSHADER_USAGE_FOG, 0));
    } else {
        for (i = 0; i < pd->attribute_count; i++) {
            const MOJOSHADER_attribute *a = &pd->attributes[i];
            if (a->usage < 0 || a->usage > 13 || a->index < 0 || a->index > 15) continue;
            if (!(a->usage == MOJOSHADER_USAGE_COLOR && strstr(a->name ? a->name : "", "oC"))) ADDP(FNVNX_DK_PAIR(a->usage, a->index));
        }
        if (pd->major_ver < 3) ADDP(FNVNX_DK_PAIR(MOJOSHADER_USAGE_FOG, 0));
    }
    #undef ADDP
    for (i = 1; i < n; i++) { unsigned char v = out[i]; for (q = i; q > 0 && out[q - 1] > v; q--) out[q] = out[q - 1]; out[q] = v; }   /* ascending */
    return n;
}

static void emit_io(sb *s, const MOJOSHADER_parseData *pd, const char *key, const MOJOSHADER_attribute *a, int count, int is_vs_input, int is_vs, const fnvnx_reflection *refl)
{
    sb_printf(s, ",\n  \"%s\": [", key);
    for (int i = 0; i < count; i++) {
        int loc;
        const int idx = (is_vs && !is_vs_input) ? out_index(pd, &a[i]) : a[i].index;
        const int k = a[i].usage * 16 + idx;
        const int ok = a[i].usage >= 0 && a[i].usage < 14 && idx >= 0 && idx < 16;
        if (is_vs_input) loc = ok ? refl->attrib_loc[k] : -1;
        else if (idx == 0 && is_vs && (a[i].usage == MOJOSHADER_USAGE_POSITION || a[i].usage == MOJOSHADER_USAGE_POINTSIZE)) loc = -2; /* gl_Position / gl_PointSize (+ copy at the varying location when linked) */
        else if (a[i].usage == MOJOSHADER_USAGE_COLOR && !is_vs_input && strstr(a[i].name ? a[i].name : "", "oC")) loc = a[i].index;
        else loc = ok ? refl->varying_loc[k] : -1;
        sb_printf(s, "%s\n    {\"usage\": \"%s\", \"index\": %d, \"name\": ", i ? "," : "", usage_name(a[i].usage), idx);
        sb_str(s, a[i].name);
        sb_printf(s, ", \"location\": %d}", loc);
    }
    sb_printf(s, "%s]", count ? "\n  " : "");
}

/* The location rule of fnvnx_dk_layout.h applied to this program's declarations (+ the draw's link set). */
static int compute_locations(const MOJOSHADER_parseData *pd, int is_vs, const unsigned char *link, uint32_t nlink, fnvnx_reflection *refl, char *err, size_t errlen)
{
    unsigned char set[224]; signed char loc[224]; int n = 0, i;
    memset(refl->attrib_loc, -1, sizeof refl->attrib_loc); memset(refl->varying_loc, -1, sizeof refl->varying_loc); memset(refl->copy, 0, sizeof refl->copy);
    #define ADD(pr) do { int dup_ = 0; for (int q_ = 0; q_ < n; q_++) if (set[q_] == (pr)) dup_ = 1; if (!dup_) set[n++] = (unsigned char)(pr); } while (0)
    for (i = 0; i < pd->attribute_count; i++) {                       /* inputs: a VS's are attributes, a PS's are varyings */
        const MOJOSHADER_attribute *a = &pd->attributes[i];
        if (!is_vs && a->usage < 0) continue;                         /* vPos / vFace */
        if (a->usage < 0 || a->usage > 13 || a->index < 0 || a->index > 15) { snprintf(err, errlen, "front end: input usage %d index %d outside the D3D9 range", a->usage, a->index); return -1; }
        if (is_vs) { if (n < 224) ADD(FNVNX_DK_PAIR(a->usage, a->index)); }
    }
    if (is_vs) {
        if (fnvnx_dk_assign(FNVNX_DK_KIND_ATTRIB, set, n, loc)) { snprintf(err, errlen, "front end: the vertex inputs are not a legal declaration set (duplicate or more than 16)"); return -1; }
        for (i = 0; i < n; i++) refl->attrib_loc[set[i]] = loc[i];
        n = 0;
    }
    if (!is_vs) for (i = 0; i < pd->attribute_count; i++) {
        const MOJOSHADER_attribute *a = &pd->attributes[i];
        if (a->usage < 0) continue;                                   /* vPos / vFace: not interpolants */
        if (!(a->usage == MOJOSHADER_USAGE_COLOR && strstr(a->name ? a->name : "", "oC"))) ADD(FNVNX_DK_PAIR(a->usage, a->index));
    }
    if (is_vs) for (i = 0; i < pd->output_count; i++) {
        const MOJOSHADER_attribute *a = &pd->outputs[i];
        const int idx = out_index(pd, a);
        if (a->usage < 0 || a->usage > 13 || idx < 0 || idx > 15) { snprintf(err, errlen, "front end: output usage %d index %d outside the D3D9 range", a->usage, a->index); return -1; }
        if (idx == 0 && (a->usage == MOJOSHADER_USAGE_POSITION || a->usage == MOJOSHADER_USAGE_POINTSIZE)) continue;  /* built-ins */
        ADD(FNVNX_DK_PAIR(a->usage, idx));
    }
    for (uint32_t k = 0; k < nlink; k++) { if (link[k] / 16 > 13) { snprintf(err, errlen, "link pair %u outside the D3D9 range", link[k]); return -1; } if (n < 224) ADD(link[k]); }
    #undef ADD
    if (fnvnx_dk_assign(FNVNX_DK_KIND_VARYING, set, n, loc)) { snprintf(err, errlen, "front end: the varyings are not a legal declaration set (duplicate or more than 31)"); return -1; }
    for (i = 0; i < n; i++) refl->varying_loc[set[i]] = loc[i];
    for (uint32_t k = 0; k < nlink; k++)
        if (link[k] == FNVNX_DK_PAIR(MOJOSHADER_USAGE_POSITION, 0) || link[k] == FNVNX_DK_PAIR(MOJOSHADER_USAGE_POINTSIZE, 0)) refl->copy[link[k]] = 1;
    return 0;
}

int fnvnx_frontend_parse(const void *bc, size_t n, const unsigned char *link, uint32_t nlink, fnvnx_reflection *refl, char **json, char *err, size_t errlen)
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
    if (compute_locations(pd, is_vs, link, nlink, refl, err, errlen)) { fnvnx_frontend_release(refl); return -1; }
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
        if (!is_vs && pd->major_ver < 2)    /* ps_1_x: no dcl, the type is data (fnvnx_dk_layout.h): all three samplers + the stage's type word */
            sb_printf(&s, "%s\n    {\"reg\": %d, \"type\": \"bound\", \"binding\": %d, \"binding_cube\": %d, \"binding_volume\": %d, \"type_word\": %d, \"texbem\": %s}",
                      i ? "," : "", m->index, m->index, FNVNX_DK_SAMP_CUBE_BASE + m->index, FNVNX_DK_SAMP_VOLUME_BASE + m->index, m->index, m->texbem ? "true" : "false");
        else
        sb_printf(&s, "%s\n    {\"reg\": %d, \"type\": \"%s\", \"binding\": %d, \"texbem\": %s}", i ? "," : "", m->index, t, m->index, m->texbem ? "true" : "false");
    }
    sb_printf(&s, "%s]", pd->sampler_count ? "\n  " : "");
    emit_io(&s, pd, "inputs", pd->attributes, pd->attribute_count, is_vs, is_vs, refl);
    emit_io(&s, pd, "outputs", pd->outputs, pd->output_count, 0, is_vs, refl);
    { unsigned char pr[224]; int np = stage_pairs(pd, is_vs, pr);
      sb_printf(&s, ",\n  \"%s\": [", is_vs ? "varyings_written" : "varyings_read");
      for (int i = 0; i < np; i++) sb_printf(&s, "%s%d", i ? ", " : "", pr[i]);
      sb_printf(&s, "]"); }
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
    if (fnvnx_frontend_parse(bc, n, NULL, 0, &refl, &json, err, errlen)) return -1;
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
