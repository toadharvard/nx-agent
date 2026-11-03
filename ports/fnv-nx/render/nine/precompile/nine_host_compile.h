/* fnv-nx S6d: run Gallium Nine's D3D9 -> TGSI -> nvc0 compile on the host (shared by nine-precompile
 * and tests/s6d). One context = fake nvc0 screen + a pipe_context whose create_vs/fs_state does what
 * nvc0_sp_state_create does (tgsi_dup_tokens + nvc0_program_translate, i.e. through patch 0003's
 * cache when one is configured). */
#ifndef NINE_HOST_COMPILE_H
#define NINE_HOST_COMPILE_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

struct nvc0_program;
struct nhc_ctx;

/* The inputs of nine_shader_info that NineVertexShader9/NinePixelShader9 derive from their variant
 * key (Mesa 20.1 vertexshader9.[ch], pixelshader9.[ch]); see render/nine/precompile/README.md "Variant keys". */
struct nhc_variant {
   uint16_t sampler_mask_shadow; /* vs key bits 0-3 / ps key bits 0-15 */
   uint32_t sampler_ps1xtypes;   /* ps < 2.0: 2 bits per sampler (0 2D, 1 1D/unbound, 2 cube, 3 volume) */
   uint8_t projected;            /* ps < 1.4: D3DTTFF_PROJECTED per stage */
   uint8_t fog_enable;           /* D3DRS_FOGENABLE (vs and ps < 3.0) */
   uint8_t fog_mode;             /* D3DRS_FOGTABLEMODE (ps < 3.0) */
   uint8_t force_color_in_centroid; /* ps: render target 0 is multisampled */
   float point_size_min, point_size_max; /* vs writing oPts */
   bool swvp;                    /* vs: software vertex processing */
   uint8_t num_ucps;             /* nvc0 vp: user clip planes (nvc0_check_program_ucps) */
   bool ctor;                    /* the shader-create compile (ctor info, not GetVariant info) */
};

struct nhc_result {
   long hr;                 /* nine_translate_shader HRESULT */
   uint8_t version;         /* (major << 4) | minor */
   unsigned type;           /* PIPE_SHADER_VERTEX / FRAGMENT */
   uint16_t sampler_mask;
   bool point_size;
   struct nvc0_program *prog; /* NULL if nine failed; prog->translated tells nvc0's result */
   unsigned tgsi_tokens;
   uint8_t key[20];         /* patch 0003 cache key */
   bool cacheable;
};

struct nhc_ctx *nhc_create(uint16_t chipset);
void nhc_destroy(struct nhc_ctx *);
uint16_t nhc_chipset(const struct nhc_ctx *);
/* tokens: D3D9 bytecode (version token .. END); ndw is only a bound for safety checks */
bool nhc_compile(struct nhc_ctx *, const uint32_t *tokens, size_t ndw, const struct nhc_variant *,
                 struct nhc_result *out);
void nhc_result_free(struct nhc_result *);
/* peek: version token -> type/version without compiling */
bool nhc_peek(const uint32_t *tokens, size_t ndw, unsigned *type, uint8_t *version);

/* byte-level comparison of two translated programs: every field translate writes, code, relocation
 * and fixup tables. Returns NULL if identical, else a static description of the first difference. */
const char *nhc_program_diff(const struct nvc0_program *a, const struct nvc0_program *b);

#endif
