/*
 * fnvnx_shaders.h - libfnvnx_shaders: D3D9 shader bytecode (SM1.1-3.0) -> deko3d DKSH.
 *
 * Portable C API (C99 callers; implementation C/C++). Pipeline per shader:
 *   front end  : parse/validate D3D bytecode, reflection (constants, samplers, I/O, CTAB)
 *   backend    : GLSL 4.50 following fnvnx_dk_layout.h  (today: MojoShader "glsldk")
 *   compiler   : uam (GLSL -> Maxwell, DKSH)
 * The backend is replaceable (see README.md, "Seam"); front end, reflection and the layout
 * contract do not change when it is.
 */
#ifndef FNVNX_SHADERS_H
#define FNVNX_SHADERS_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

#define FNVNX_SHADERS_ABI 1

typedef enum fnvnx_shader_stage {
    FNVNX_STAGE_UNKNOWN = -1,
    FNVNX_STAGE_VERTEX = 0,
    FNVNX_STAGE_PIXEL = 1
} fnvnx_shader_stage;

enum {
    FNVNX_SHADER_KEEP_GLSL = 1u << 0,  /* return the intermediate GLSL in result.glsl */
    FNVNX_SHADER_NO_DKSH   = 1u << 1,  /* stop after GLSL (no uam) */
    /* Static D3D9 epilogue variant of a pixel shader (G1). Without EPI_STATIC the shader carries the runtime
     * epilogue: alpha test and (ps < 3.0) fog, each behind a uniform branch on the driver UBO. With EPI_STATIC
     * only the blocks named by EPI_ALPHA / EPI_FOG are compiled in; the runtime switches inside them stay, so a
     * variant bound for a state it does not cover is still correct for that block. __e0 = neither, __ea = alpha
     * test, __ef = fog; the runtime shader covers both. */
    FNVNX_SHADER_EPI_STATIC = 1u << 2,
    FNVNX_SHADER_EPI_ALPHA  = 1u << 3,
    FNVNX_SHADER_EPI_FOG    = 1u << 4,
    /* D3D9's implementation-defined numerics (rcp/rsq/log of 0, nrm of a zero vector): WineD3D's IEEE results by
     * default (the original under Wine, the reference of the S16 sweeps); with this flag Gallium Nine's clamped
     * ones (V6n's oracle, softpipe). The GLSL carries both behind DK_NUMERICS_NINE; the flag defines it. */
    FNVNX_SHADER_NUMERICS_NINE = 1u << 5,
    /* Vertex shaders: the "complete" variant (README "Varying completion"): besides the base completion (the components a declared output
     * leaves out, the link set L, FOG0) every varying slot 0..30 that is still unwritten gets the default of its canonical pair, so any
     * pixel stage whose inputs are canonical links. The device picks this variant of the VS iff fnvnx_dk_vs_needs_complete(). Ignored for
     * pixel shaders. Part of the variant key. */
    FNVNX_SHADER_VS_COMPLETE = 1u << 6
};

typedef struct fnvnx_shader_opts {
    uint32_t flags;
    const char *backend;               /* NULL = default ("mojoshader-glsldk") */
    /* Varying link set L of the draw (fnvnx_dk_layout.h, "Location rule"): FNVNX_DK_PAIR(usage, index) of every VS output
     * (except POSITION0/PSIZE0) and every PS input of the VS/PS pair; both stages are translated with the same L. NULL = none:
     * the stage's own declarations (identical result when all its pairs are canonical). A PS-read POSITION0/PSIZE0 must be in L
     * for the VS to write its varying copy. Part of the variant key. */
    const unsigned char *link_pairs;
    uint32_t n_link_pairs;
} fnvnx_shader_opts;

typedef struct fnvnx_shader_stats {
    uint32_t d3d_instructions;         /* D3D instruction slots (front end) */
    uint32_t glsl_lines;               /* lines of GLSL */
    uint32_t glsl_statements;          /* ';'-terminated statements inside main() */
    uint32_t dk_instructions;          /* uam/nv50_ir instruction count */
    uint32_t dk_gprs;                  /* registers per thread */
    uint32_t dk_code_size;             /* bytes of Maxwell code */
    uint32_t dk_constbuf1_size;        /* compiler-generated immediates */
} fnvnx_shader_stats;

typedef struct fnvnx_shader_result {
    int ok;                            /* 1 = dksh valid (or glsl valid with NO_DKSH) */
    char error[1024];
    uint64_t hash;                     /* fnvnx_shader_hash of the input */
    fnvnx_shader_stage stage;
    int major, minor;                  /* shader model */
    uint8_t *dksh; size_t dksh_size;   /* malloc'd */
    char *glsl;                        /* malloc'd, with KEEP_GLSL */
    char *reflection_json;             /* malloc'd, always when the front end succeeded */
    fnvnx_shader_stats stats;
} fnvnx_shader_result;

/* Cache key: FNV-1a 64 of the bytecode. */
uint64_t fnvnx_shader_hash(const void *bytecode, size_t n);

/* GLSL half (the GL path; no uam, no DKSH): front end -> GLSL backend. Returns result->ok; the GLSL is always in result->glsl. */
int fnvnx_shader_translate_glsl(const void *d3d_bytecode, size_t n, const fnvnx_shader_opts *opts,
                                fnvnx_shader_result *result);

/* GLSL + DKSH (adds uam; src/translate_dksh.c). Returns result->ok. Never throws; result must be freed with fnvnx_shader_result_free. */
int fnvnx_shader_translate(const void *d3d_bytecode, size_t n, const fnvnx_shader_opts *opts,
                           fnvnx_shader_result *result);
void fnvnx_shader_result_free(fnvnx_shader_result *result);

/* Structural DKSH check (header sizes, alignment, single program of the given stage). 0 = ok. */
int fnvnx_dksh_validate(const void *dksh, size_t n, fnvnx_shader_stage stage, char *err, size_t errlen);

/* Constant table (CTAB comment) of a blob, front end only (no backend, no uam). */
typedef struct fnvnx_ctab_entry {
    char name[128];
    const char *set;                   /* "float" | "int" | "bool" | "sampler" */
    uint32_t reg, count;               /* first register, registers used */
    const char *cls;                   /* scalar | vector | matrix_rows | matrix_columns | object | struct */
    const char *type;                  /* float | int | bool | sampler2d | samplercube | sampler3d | ... */
    uint32_t rows, columns, elements;
} fnvnx_ctab_entry;
/* Returns the number of entries (>= 0) and a malloc'd array in *out, or -1 with err. */
int fnvnx_shader_ctab(const void *d3d_bytecode, size_t n, fnvnx_ctab_entry **out, char *err, size_t errlen);

/* Names of compiled-in GLSL backends, NULL-terminated. */
const char *const *fnvnx_shader_backends(void);

#ifdef __cplusplus
}
#endif
#endif
