/* fnv-nx L6 GL 4.5 backend v4: GL function loading and the error log shared by all files. */
#define _GNU_SOURCE
#include "gl4.h"
#include <stdio.h>

#define X(type, name) type p_##name;
NXGL4_FUNCS(X)
#undef X
#define X(type, name) {#name, (void (**)(void))&p_##name},
static const struct { const char* name; void (**fn)(void); } k_funcs[] = { NXGL4_FUNCS(X) };
#undef X

Gl4 g4;

int gl4_load_gl(void* (*getproc)(const char*), char* err, size_t errlen) {
    for (size_t i = 0; i < sizeof k_funcs / sizeof k_funcs[0]; i++) {
        *k_funcs[i].fn = (void (*)(void))getproc(k_funcs[i].name);
        if (!*k_funcs[i].fn) {
            if (err && errlen) snprintf(err, errlen, "GL function %s missing", k_funcs[i].name);
            return 0;
        }
    }
    return 1;
}

/* A contract violation (a value the device never sends, a GL error): recorded, counted, and reported by the
 * call that met it (submit / image_read return NXGPU_BAD_CALL); never ignored. */
void nxchk_err(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g4.err, sizeof g4.err, fmt, ap);
    va_end(ap);
    g4.errCount++;
}

const char* nxgl4_last_error(void) { return g4.err; }
