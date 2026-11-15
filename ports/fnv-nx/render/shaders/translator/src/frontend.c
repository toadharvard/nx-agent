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
#include "../include/fnvnx_shaders.h"
#include "fnvnx_sha256.h"
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

/* THE declared pairs of a stage, as FNVNX_DK_PAIR codes ascending (the one function: the reflection field "declared_pairs", the draw path's pairing
 * through fnvnx_shader_declared_pairs, and the sets below all come from here). VS: its declared outputs (oFog / oPts below sm3 normalised by
 * out_index; POSITION0 / PSIZE0 included). PS: its declared inputs (not vPos / vFace). Returns the count (<= 224). */
static int declared_pairs(const MOJOSHADER_parseData *pd, int is_vs, unsigned char *out)
{
    int n = 0, i, q;
    #define ADDP(pr) do { int d_ = 0; for (int k_ = 0; k_ < n; k_++) if (out[k_] == (pr)) d_ = 1; if (!d_ && n < 224) out[n++] = (unsigned char)(pr); } while (0)
    if (is_vs) {
        for (i = 0; i < pd->output_count; i++) {
            const MOJOSHADER_attribute *a = &pd->outputs[i]; const int idx = out_index(pd, a);
            if (a->usage < 0 || a->usage > 13 || idx < 0 || idx > 15) continue;
            ADDP(FNVNX_DK_PAIR(a->usage, idx));
        }
    } else {
        for (i = 0; i < pd->attribute_count; i++) {
            const MOJOSHADER_attribute *a = &pd->attributes[i];
            if (a->usage < 0 || a->usage > 13 || a->index < 0 || a->index > 15) continue;
            ADDP(FNVNX_DK_PAIR(a->usage, a->index));
        }
    }
    #undef ADDP
    for (i = 1; i < n; i++) { unsigned char v = out[i]; for (q = i; q > 0 && out[q - 1] > v; q--) out[q] = out[q - 1]; out[q] = v; }   /* ascending */
    return n;
}

/* The varyings a stage writes (VS) or reads (PS), as FNVNX_DK_PAIR codes, for the static variant rule fnvnx_dk_vs_needs_complete() (fnvnx_dk_layout.h):
 * the declared pairs, VS without the built-ins POSITION0 / PSIZE0 and with FOG0 always (every VS writes it, README "Varying completion"), PS with FOG0 for a
 * ps < 3.0 (its epilogue reads the fog factor). Returns the count. */
static int stage_pairs(const MOJOSHADER_parseData *pd, int is_vs, unsigned char *out)
{
    unsigned char d[224]; int nd = declared_pairs(pd, is_vs, d), n = 0, i, q;
    const int fog = FNVNX_DK_PAIR(MOJOSHADER_USAGE_FOG, 0);
    for (i = 0; i < nd; i++) if (!(is_vs && (d[i] == FNVNX_DK_PAIR(MOJOSHADER_USAGE_POSITION, 0) || d[i] == FNVNX_DK_PAIR(MOJOSHADER_USAGE_POINTSIZE, 0)))) out[n++] = d[i];
    if (is_vs || pd->major_ver < 3) { int have = 0; for (i = 0; i < n; i++) have |= out[i] == fog; if (!have && n < 224) out[n++] = (unsigned char)fog; }
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
        else if (!is_vs && !is_vs_input && a[i].usage == MOJOSHADER_USAGE_COLOR) loc = a[i].index;   /* a PS output list holds only oC# (usage COLOR) and oDepth: the colour target */
        else loc = ok ? refl->varying_loc[k] : -1;
        sb_printf(s, "%s\n    {\"usage\": \"%s\", \"index\": %d, \"name\": ", i ? "," : "", usage_name(a[i].usage), idx);
        sb_str(s, a[i].name);
        sb_printf(s, ", \"location\": %d}", loc);
    }
    sb_printf(s, "%s]", count ? "\n  " : "");
}

int fnvnx_frontend_parse(const void *bc, size_t n, const unsigned char *link, uint32_t nlink, int vs_complete, int flat_color, fnvnx_reflection *refl, char **json, char *err, size_t errlen)
{
    memset(refl, 0, sizeof(*refl));
    *json = NULL;
    if (n < 4 || (n & 3)) { snprintf(err, errlen, "bytecode size %zu is not a positive multiple of 4", n); return -1; }
    /* ONE parse: the glsldk profile validates, computes the locations (the declared set, once, when every register is known; refl->*_loc / copy are
     * its outputs) and emits the GLSL (pd->output); the reflection below is read from the same parse data. */
    const MOJOSHADER_glsldkConfig dk = { link, nlink, vs_complete, flat_color, refl->attrib_loc, refl->varying_loc, refl->copy };
    const MOJOSHADER_parseData *pd = MOJOSHADER_parseGlsldk(&dk, "main",
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
    uint8_t key[32]; char keyhex[FNVNX_SHADER_KEY_HEX]; fnvnx_sha256(bc, n, key); fnvnx_shader_key_hex(key, keyhex);
    sb s = { 0 };
    sb_printf(&s, "{\n  \"hash\": \"%s\",\n  \"stage\": \"%s\",\n  \"model\": \"%s_%d_%d\",\n  \"d3d_instructions\": %d",
              keyhex, is_vs ? "vertex" : "pixel", is_vs ? "vs" : "ps",
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
    { unsigned char dp[224]; int nd = declared_pairs(pd, is_vs, dp);
      sb_printf(&s, ",\n  \"major\": %d,\n  \"declared_pairs\": [", pd->major_ver);
      for (int i = 0; i < nd; i++) sb_printf(&s, "%s%d", i ? ", " : "", dp[i]);
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

int fnvnx_shader_declared_pairs(const void *bc, size_t n, unsigned char pairs[224], uint32_t *major, char *err, size_t errlen)
{
    fnvnx_reflection refl;
    char *json;
    if (fnvnx_frontend_parse(bc, n, NULL, 0, 0, 0, &refl, &json, err, errlen)) return -1;
    free(json);
    const MOJOSHADER_parseData *pd = (const MOJOSHADER_parseData *)refl.frontend_parse;
    const int k = declared_pairs(pd, refl.stage == FNVNX_STAGE_VERTEX, pairs);
    *major = (uint32_t)pd->major_ver;
    fnvnx_frontend_release(&refl);
    return k;
}

int fnvnx_shader_ctab(const void *bc, size_t n, fnvnx_ctab_entry **out, char *err, size_t errlen)
{
    fnvnx_reflection refl;
    char *json;
    *out = NULL;
    if (fnvnx_frontend_parse(bc, n, NULL, 0, 0, 0, &refl, &json, err, errlen)) return -1;
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
