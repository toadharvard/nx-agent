/* fnv-nx S6d: Gallium Nine + nvc0 shader compile on the host. See nine_host_compile.h. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pipe/p_context.h"
#include "pipe/p_screen.h"
#include "pipe/p_state.h"
#include "tgsi/tgsi_parse.h"
#include "tgsi/tgsi_dump.h"
#include "util/u_memory.h"
#include "util/u_math.h"

#include "device9.h"
#include "nine_shader.h"
#include "nine_state.h"

#include "nvc0/nvc0_program.h"
#include "nvc0/nvc0_program_cache.h"
#include "codegen/nv50_ir_driver.h"

#include "nine_host_screen.h"
#include "nine_host_compile.h"

/* nvc0_context.h would pull the whole driver; these are the two entry points we use */
bool nvc0_program_translate(struct nvc0_program *, uint16_t chipset, struct pipe_debug_callback *);

struct nhc_ctx {
   struct pipe_context pipe; /* first: create_*_state get it back */
   struct pipe_screen *screen;
   struct NineDevice9 *device;
   uint16_t chipset;
   struct nvc0_program *last;
};

/* what nvc0_sp_state_create does (nvc0_state.c), minus the NIR branch compiled out on Switch */
static void *
nhc_sp_state_create(struct pipe_context *pipe, const struct pipe_shader_state *cso, unsigned type)
{
   struct nhc_ctx *c = (struct nhc_ctx *)pipe;
   struct nvc0_program *prog = CALLOC_STRUCT(nvc0_program);
   if (!prog)
      return NULL;
   prog->type = type;
   prog->pipe.type = cso->type;
   if (cso->type != PIPE_SHADER_IR_TGSI) {
      FREE(prog);
      return NULL;
   }
   prog->pipe.tokens = tgsi_dup_tokens(cso->tokens);
   if (cso->stream_output.num_outputs)
      prog->pipe.stream_output = cso->stream_output;
   if (getenv("NHC_DUMP_TGSI"))
      tgsi_dump(prog->pipe.tokens, 0);
   c->last = prog; /* before translate: a crash report can still find it */
   prog->translated = nvc0_program_translate(prog, c->chipset, NULL);
   return prog;
}

static void *
nhc_create_vs_state(struct pipe_context *pipe, const struct pipe_shader_state *cso)
{
   return nhc_sp_state_create(pipe, cso, PIPE_SHADER_VERTEX);
}

static void *
nhc_create_fs_state(struct pipe_context *pipe, const struct pipe_shader_state *cso)
{
   return nhc_sp_state_create(pipe, cso, PIPE_SHADER_FRAGMENT);
}

/* nvc0_program_destroy without the context (we never have tfb state bound) */
static void
nhc_program_reset(struct nvc0_program *prog)
{
   const struct pipe_shader_state pipe = prog->pipe;
   const ubyte type = prog->type;
   FREE(prog->code);
   FREE(prog->relocs);
   FREE(prog->fixups);
   FREE(prog->tfb);
   memset(prog, 0, sizeof(*prog));
   prog->pipe = pipe;
   prog->type = type;
}

static void
nhc_program_free(struct nvc0_program *prog)
{
   if (!prog)
      return;
   nhc_program_reset(prog);
   FREE((void *)prog->pipe.tokens);
   FREE(prog);
}

struct nhc_ctx *
nhc_create(uint16_t chipset)
{
   struct nhc_ctx *c = CALLOC_STRUCT(nhc_ctx);
   if (!c)
      return NULL;
   c->chipset = chipset;
   c->screen = nine_host_fake_nvc0_screen(chipset);
   c->device = CALLOC_STRUCT(NineDevice9);
   if (!c->screen || !c->device) {
      FREE(c->device);
      FREE(c);
      return NULL;
   }
   c->pipe.screen = c->screen;
   c->pipe.create_vs_state = nhc_create_vs_state;
   c->pipe.create_fs_state = nhc_create_fs_state;
   c->device->screen = c->screen;
   c->device->screen_sw = c->screen;
   /* NineDevice9_ctor, "Create constant buffers" */
   unsigned max_const_vs = MIN2(c->screen->get_shader_param(c->screen, PIPE_SHADER_VERTEX,
                                   PIPE_SHADER_CAP_MAX_CONST_BUFFER_SIZE) / sizeof(float[4]),
                                NINE_MAX_CONST_ALL);
   unsigned max_const_ps = NINE_MAX_CONST_F_PS3 + (NINE_MAX_CONST_I + NINE_MAX_CONST_B / 4);
   c->device->max_vs_const_f = max_const_vs - (NINE_MAX_CONST_I + NINE_MAX_CONST_B / 4);
   c->device->max_ps_const_f = max_const_ps - (NINE_MAX_CONST_I + NINE_MAX_CONST_B / 4);
   return c;
}

void
nhc_destroy(struct nhc_ctx *c)
{
   if (!c)
      return;
   FREE(c->device);
   FREE(c);
}

uint16_t
nhc_chipset(const struct nhc_ctx *c)
{
   return c->chipset;
}

bool
nhc_peek(const uint32_t *tok, size_t ndw, unsigned *type, uint8_t *version)
{
   if (ndw < 2)
      return false;
   uint32_t t = tok[0] >> 16;
   if (t == 0xfffe)
      *type = PIPE_SHADER_VERTEX;
   else if (t == 0xffff)
      *type = PIPE_SHADER_FRAGMENT;
   else
      return false;
   unsigned major = (tok[0] >> 8) & 0xff, minor = tok[0] & 0xff;
   if (major < 1 || major > 3)
      return false;
   *version = (major << 4) | minor;
   /* must contain END */
   for (size_t i = 1; i < ndw; ++i)
      if (tok[i] == 0x0000ffff)
         return true;
   return false;
}

bool
nhc_compile(struct nhc_ctx *c, const uint32_t *tokens, size_t ndw, const struct nhc_variant *v,
            struct nhc_result *out)
{
   struct NineDevice9 *device = c->device;
   struct nine_shader_info info;
   memset(out, 0, sizeof(*out));
   if (!nhc_peek(tokens, ndw, &out->type, &out->version))
      return false;

   /* fields Nine leaves uninitialised in its ctor/variant paths are zero here */
   memset(&info, 0, sizeof(info));
   info.type = out->type;
   info.byte_code = (const DWORD *)tokens;
   info.add_constants_defs.c_combination = NULL;
   info.add_constants_defs.int_const_added = NULL;
   info.add_constants_defs.bool_const_added = NULL;
   info.process_vertices = false;
   if (out->type == PIPE_SHADER_VERTEX) {
      /* NineVertexShader9_ctor / NineVertexShader9_GetVariant */
      info.const_i_base = NINE_CONST_I_BASE(device->max_vs_const_f) / 16;
      info.const_b_base = NINE_CONST_B_BASE(device->max_vs_const_f) / 16;
      info.sampler_ps1xtypes = 0;
      info.swvp_on = v->swvp;
      if (v->ctor) {
         info.sampler_mask_shadow = 0;
         info.fog_enable = 0;
         info.point_size_min = 0;
         info.point_size_max = 0;
      } else {
         info.sampler_mask_shadow = v->sampler_mask_shadow & 0xf;
         info.fog_enable = v->fog_enable;
         info.point_size_min = v->point_size_min;
         info.point_size_max = v->point_size_max;
      }
   } else {
      /* NinePixelShader9_ctor / NinePixelShader9_GetVariant */
      info.const_i_base = NINE_CONST_I_BASE(device->max_ps_const_f) / 16;
      info.const_b_base = NINE_CONST_B_BASE(device->max_ps_const_f) / 16;
      info.swvp_on = false;
      if (v->ctor) {
         info.sampler_mask_shadow = 0;
         info.sampler_ps1xtypes = 0;
         info.fog_enable = 0;
         info.projected = 0;
      } else {
         info.sampler_mask_shadow = v->sampler_mask_shadow;
         if (out->version < 0x20) {
            info.sampler_ps1xtypes = v->sampler_ps1xtypes;
            info.projected = out->version < 0x14 ? v->projected : 0;
         }
         info.fog_enable = v->fog_enable;
         info.fog_mode = v->fog_mode;
         info.force_color_in_centroid = v->force_color_in_centroid;
      }
   }

   c->last = NULL;
   out->hr = nine_translate_shader(device, &info, &c->pipe);
   if (FAILED(out->hr) || !info.cso) {
      if (c->last)
         nhc_program_free(c->last);
      return false;
   }
   struct nvc0_program *prog = (struct nvc0_program *)info.cso;
   FREE(info.lconstf.data);
   FREE(info.lconstf.ranges);
   FREE(info.const_ranges);
   out->sampler_mask = info.sampler_mask;
   out->point_size = info.point_size;

   /* nvc0_check_program_ucps: clip planes enabled -> destroy and re-translate with num_ucps */
   if (out->type == PIPE_SHADER_VERTEX && prog->translated && v->num_ucps > prog->vp.num_ucps) {
      nhc_program_reset(prog);
      prog->vp.num_ucps = v->num_ucps;
      prog->translated = nvc0_program_translate(prog, c->chipset, NULL);
   }
   out->prog = prog;
   out->tgsi_tokens = tgsi_num_tokens(prog->pipe.tokens);
   out->cacheable = nvc0_pcache_key(prog, c->chipset, out->key);
   return true;
}

void
nhc_result_free(struct nhc_result *r)
{
   nhc_program_free(r->prog);
   r->prog = NULL;
}

#define CMP(field) do { if (memcmp(&a->field, &b->field, sizeof(a->field))) return #field; } while (0)

const char *
nhc_program_diff(const struct nvc0_program *a, const struct nvc0_program *b)
{
   CMP(type);
   CMP(translated);
   CMP(need_tls);
   CMP(num_gprs);
   CMP(code_size);
   CMP(parm_size);
   CMP(hdr);
   CMP(flags);
   CMP(vp.clip_mode);
   CMP(vp.clip_enable);
   CMP(vp.cull_enable);
   CMP(vp.num_ucps);
   CMP(vp.edgeflag);
   CMP(vp.need_vertex_id);
   CMP(vp.need_draw_parameters);
   CMP(vp.layer_viewport_relative);
   CMP(fp.early_z);
   CMP(fp.colors);
   CMP(fp.color_interp);
   CMP(fp.sample_mask_in);
   CMP(fp.force_persample_interp);
   CMP(fp.flatshade);
   CMP(fp.reads_framebuffer);
   CMP(fp.post_depth_coverage);
   CMP(tp.tess_mode);
   CMP(cp.lmem_size);
   CMP(cp.smem_size);
   CMP(num_barriers);
   if (!a->code != !b->code)
      return "code (presence)";
   if (a->code && memcmp(a->code, b->code, a->code_size))
      return "code (bytes)";
   if (!a->relocs != !b->relocs)
      return "relocs (presence)";
   if (a->relocs) {
      uint32_t n = nv50_ir_relocs_count(a->relocs);
      if (n != nv50_ir_relocs_count(b->relocs))
         return "relocs (count)";
      uint32_t *wa = calloc(5 * n + 1, 4), *wb = calloc(5 * n + 1, 4);
      nv50_ir_relocs_serialize(a->relocs, wa);
      nv50_ir_relocs_serialize(b->relocs, wb);
      int d = memcmp(wa, wb, 20 * n);
      free(wa);
      free(wb);
      if (d)
         return "relocs (entries)";
   }
   if (!a->fixups != !b->fixups)
      return "fixups (presence)";
   if (a->fixups) {
      /* FixupInfo { u32 count; FixupEntry { fn*, u32 } entry[] }: compare raw, the fn pointers
       * must be the very same functions in this process */
      uint32_t n = *(const uint32_t *)a->fixups;
      if (n != *(const uint32_t *)b->fixups)
         return "fixups (count)";
      size_t hdr = sizeof(void *) > 4 ? 8 : 4, esz = 2 * sizeof(void *);
      for (uint32_t i = 0; i < n; ++i) {
         const uint8_t *ea = (const uint8_t *)a->fixups + hdr + i * esz;
         const uint8_t *eb = (const uint8_t *)b->fixups + hdr + i * esz;
         if (memcmp(ea, eb, sizeof(void *) + 4))
            return "fixups (entries)";
      }
   }
   if (!a->tfb != !b->tfb)
      return "tfb";
   return NULL;
}

unsigned
nhc_tgsi_count_subroutine_ops(const struct nvc0_program *prog)
{
   struct tgsi_parse_context ctx;
   unsigned n = 0;
   if (!prog || tgsi_parse_init(&ctx, prog->pipe.tokens) != TGSI_PARSE_OK)
      return ~0u;
   while (!tgsi_parse_end_of_tokens(&ctx)) {
      tgsi_parse_token(&ctx);
      if (ctx.FullToken.Token.Type != TGSI_TOKEN_TYPE_INSTRUCTION)
         continue;
      switch (ctx.FullToken.FullInstruction.Instruction.Opcode) {
      case TGSI_OPCODE_CAL:
      case TGSI_OPCODE_BGNSUB:
      case TGSI_OPCODE_ENDSUB:
         n++;
         break;
      default:
         break;
      }
   }
   tgsi_parse_free(&ctx);
   return n;
}

bool
nhc_bytecode_has_call(const uint32_t *tok, size_t ndw)
{
   if (ndw < 2 || ((tok[0] >> 8) & 0xff) < 2)
      return false;
   for (size_t i = 1; i < ndw && tok[i] != 0x0000ffff;) {
      uint32_t op = tok[i] & 0xffff;
      if (op == 0xfffe) { /* comment */
         i += 1 + ((tok[i] >> 16) & 0x7fff);
         continue;
      }
      if (op == 25 || op == 26) /* D3DSIO_CALL, D3DSIO_CALLNZ */
         return true;
      i += 1 + ((tok[i] >> 24) & 0xf);
   }
   return false;
}
