/* L6 GL4 backend tests: shared helpers. The oracle is CPU code in these files (pattern generators, reference
 * filters, format table), never the backend. LP64 host build, identity guest address translation. */
#ifndef T_GL4_COMMON_H
#define T_GL4_COMMON_H
#include <math.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "gl4.h"

extern int g_checks, g_fail;
#define CHECK(c) do { g_checks++; if (!(c)) { g_fail++; if (g_fail < 25) printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); } } while (0)
#define CHECKM(c, ...) do { g_checks++; if (!(c)) { g_fail++; if (g_fail < 25) { printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } } while (0)

/* ---- the format table of the oracle: independent of the backend ------------------------------------------------ */
enum Kind { K_RAW, K_F16, K_F32, K_S8, K_S16, K_X8, K_Z32F, K_Z32FS8 };
typedef struct { uint8_t fmt; const char* name; uint8_t bpp, compressed, depth, kind, srgb; } TFmt;
extern const TFmt g_fmts[];
extern const int g_nfmts;   /* entries for NxFormat 1..NxFmt_Count-1 */

static inline uint32_t lvl(uint32_t v, uint32_t l) { v >>= l; return v ? v : 1; }
static inline size_t t_rowbytes(const TFmt* f, uint32_t w) { return (f->compressed ? (w + 3) / 4 : w) * f->bpp; }
static inline size_t t_rows(const TFmt* f, uint32_t h) { return f->compressed ? (h + 3) / 4 : h; }
uint32_t t_hash(uint32_t a);
/* fills n bytes of texel data of format f (n = whole texels or blocks) with a deterministic, representable pattern */
void t_pattern(const TFmt* f, uint8_t* p, size_t n, uint32_t seed);

/* ---- driving the backend through the ext_call dispatch ------------------------------------------------------------ */
uint64_t t_ext(uint32_t fn, int n, ...);          /* n uint64_t args */
typedef struct { uint32_t w[8192]; uint32_t n; } Stream;
void t_op(Stream* s, uint32_t opcode, int argc, ...);   /* argc uint32_t args */
uint64_t t_submit(Stream* s);                      /* NXGPU_FN_SUBMIT, resets the stream; returns its result */
int t_setup(uint32_t swapW, uint32_t swapH);       /* nxgl4_create + scratch memory block; 0 ok */
void t_teardown(void);
extern uint32_t g_mem;                             /* scratch block (id) */
extern uint8_t* g_scratch;                         /* its storage, 8 MiB */
#define SCRATCH_SIZE (8u << 20)

uint32_t t_image(uint32_t type, uint32_t fmt, uint32_t w, uint32_t h, uint32_t d, uint32_t mips, uint32_t samples);
/* ImageUpdate of one box from host data (rows rowPitch apart); returns the submit result */
uint64_t t_update(uint32_t img, uint32_t level, uint32_t layer, const uint32_t box[6], const void* data, size_t dataSize,
                  size_t rowPitch, size_t srcOffset);
/* image_read of one box into dst; returns the call result */
uint64_t t_read(uint32_t img, uint32_t level, uint32_t layer, const uint32_t box[6], void* dst, size_t dstSize, size_t rowPitch);
/* the whole level/layer as tight native bytes (malloc'd, NULL on failure) */
uint8_t* t_read_all(uint32_t img, uint32_t level, uint32_t layer, uint32_t w, uint32_t h, uint32_t d, const TFmt* f);

float t_half(uint16_t h);
uint16_t t_tohalf(float f);

/* parts */
void part_ab(char which);
void part_cd(char which);
void part_ef(char which);
#endif
