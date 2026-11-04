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
    FNVNX_SHADER_NO_VARIANTS = 1u << 2 /* pixel programs: only the runtime-epilogue DKSH, no __e0/__ea/__ef */
};

/* Compile-time epilogue variants of a pixel program with a D3D9 epilogue (alpha test + ps < 3.0 fog from the driver
 * UBO): the runtime binds the one matching the draw's state, all precompiled (no runtime compile). The main DKSH
 * (result.dksh) is the runtime one (both, driver-selected); the GLSL is shared, a variant defines DK_NO_ALPHA and/or
 * DK_NO_FOG after #version. */
enum { FNVNX_EPI_E0 = 0,   /* "__e0": no alpha test, no fog */
       FNVNX_EPI_EA,       /* "__ea": alpha test only */
       FNVNX_EPI_EF,       /* "__ef": fog only (the same code as __e0 for ps_3_0: no fixed-function fog) */
       FNVNX_EPI_COUNT };
extern const char *const fnvnx_epi_suffix[FNVNX_EPI_COUNT];   /* "__e0", "__ea", "__ef" */

typedef struct fnvnx_shader_opts {
    uint32_t flags;
    const char *backend;               /* NULL = default ("mojoshader-glsldk") */
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
    int num_variants;                  /* FNVNX_EPI_COUNT for a pixel program with an epilogue, else 0 */
    struct { uint8_t *dksh; size_t dksh_size; fnvnx_shader_stats stats; } variants[FNVNX_EPI_COUNT];  /* malloc'd */
} fnvnx_shader_result;

/* Cache key: FNV-1a 64 of the bytecode. */
uint64_t fnvnx_shader_hash(const void *bytecode, size_t n);

/* Returns result->ok. Never throws; result must be freed with fnvnx_shader_result_free. */
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
