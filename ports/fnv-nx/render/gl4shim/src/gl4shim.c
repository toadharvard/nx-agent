/* fnv-nx L6 GL 4.5 backend: the GLSL shim, the one home of the rewrite that gl4 hands to the GL driver (a root the shader-link checker links without
 * the GL half). It is nxgl v3's (same rewriting rules, see render/native/gl/nxgl.h): "#version 460..." -> "#version 450 core", "binding = n" ->
 * "binding = stage * 16 + n", uniform block names get a per-stage suffix, and the vertex stage's main is wrapped to negate gl_Position.y. */
#include "nx_gl4shim.h"
#include <stdio.h>
#include <string.h>

size_t nxgl4_shim_glsl(const char* src, size_t n, uint32_t stage, char* out, size_t cap) {
    size_t o = 0, i = 0;
#define PUT(c) do { if (o + 1 >= cap) return 0; out[o++] = (c); } while (0)
    while (i < n) {
        if ((i == 0 || src[i - 1] == '\n') && n - i >= 8 && !memcmp(src + i, "#version", 8)) {
            const char* v = "#version 450 core";
            for (const char* p = v; *p; p++) PUT(*p);
            while (i < n && src[i] != '\n') i++;
            continue;
        }
        if (n - i >= 7 && !memcmp(src + i, "binding", 7) && (i == 0 || !(src[i - 1] == '_' ||
            (src[i - 1] >= 'a' && src[i - 1] <= 'z') || (src[i - 1] >= 'A' && src[i - 1] <= 'Z') ||
            (src[i - 1] >= '0' && src[i - 1] <= '9')))) {
            size_t j = i + 7;
            while (j < n && (src[j] == ' ' || src[j] == '\t')) j++;
            if (j < n && src[j] == '=') {
                j++;
                while (j < n && (src[j] == ' ' || src[j] == '\t')) j++;
                if (j < n && src[j] >= '0' && src[j] <= '9') {
                    unsigned v = 0;
                    while (j < n && src[j] >= '0' && src[j] <= '9') v = v * 10 + (unsigned)(src[j++] - '0');
                    char num[32];
                    int k = snprintf(num, sizeof num, "binding = %u", v + stage * 16);
                    for (int q = 0; q < k; q++) PUT(num[q]);
                    i = j;
                    continue;
                }
            }
        }
        /* "uniform Name {": a block of that name in two stages is one GL block and must have one binding; the
         * stages bind their own buffers (binding = stage * 16 + n), so each stage gets its own block name */
        if (n - i >= 8 && !memcmp(src + i, "uniform", 7) && (src[i + 7] == ' ' || src[i + 7] == '\t') &&
            (i == 0 || src[i - 1] == ' ' || src[i - 1] == '\t' || src[i - 1] == ')' || src[i - 1] == '\n')) {
            size_t j = i + 7;
            while (j < n && (src[j] == ' ' || src[j] == '\t')) j++;
            size_t id0 = j;
            while (j < n && (src[j] == '_' || (src[j] >= 'a' && src[j] <= 'z') || (src[j] >= 'A' && src[j] <= 'Z') ||
                             (src[j] >= '0' && src[j] <= '9'))) j++;
            size_t id1 = j;
            while (j < n && (src[j] == ' ' || src[j] == '\t' || src[j] == '\n')) j++;
            if (id1 > id0 && j < n && src[j] == '{') {
                for (size_t q = i; q < id1; q++) PUT(src[q]);
                const char* sfx = stage ? "_nxfs" : "_nxvs";
                for (const char* q = sfx; *q; q++) PUT(*q);
                i = id1;
                continue;
            }
        }
        /* vertex stage: "void main(" -> "void nx_main_(", wrapper appended below */
        if (stage == 0 && n - i >= 9 && !memcmp(src + i, "void main", 9) && (i == 0 || src[i - 1] == '\n' ||
            src[i - 1] == ' ') && (i + 9 >= n || src[i + 9] == '(' || src[i + 9] == ' ')) {
            const char* r = "void nx_main_";
            for (const char* q = r; *q; q++) PUT(*q);
            i += 9;
            continue;
        }
        PUT(src[i]);
        i++;
    }
    if (stage == 0) {
        const char* w = "\nvoid main() { nx_main_(); gl_Position.y = -gl_Position.y; }\n";
        for (const char* q = w; *q; q++) PUT(*q);
    }
    out[o] = 0;
    return o;
#undef PUT
}
