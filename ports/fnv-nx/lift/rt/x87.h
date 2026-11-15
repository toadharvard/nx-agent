/* Exact x87 arithmetic core (integer only). Values cross the API as bit patterns. */
#ifndef FNVL_X87_H
#define FNVL_X87_H
#ifndef FNVL_TYPES
#define FNVL_TYPES
typedef unsigned char u8; typedef unsigned short u16; typedef unsigned int u32; typedef unsigned long long u64;
#endif
#ifdef __cplusplus
extern "C" {
#endif
typedef struct { u64 m; u16 se; } fnvl_f80;   /* m: significand with explicit integer bit; se: sign<<15 | exp15 */
/* fcw: x87 control word (PC bits 8-9: 0=24, 2=53, 3=64; RC bits 10-11). *fsw: IE DE ZE OE UE PE (bits 0-5) are
   ORed in, C1 (bit 9) set/cleared where defined; TOP and C0/C2/C3 untouched except by the compares. All exceptions masked. */
fnvl_f80 fnvl_x_add(fnvl_f80 a, fnvl_f80 b, u32 fcw, u32 *fsw);
fnvl_f80 fnvl_x_sub(fnvl_f80 a, fnvl_f80 b, u32 fcw, u32 *fsw);   /* a - b */
fnvl_f80 fnvl_x_mul(fnvl_f80 a, fnvl_f80 b, u32 fcw, u32 *fsw);
fnvl_f80 fnvl_x_div(fnvl_f80 a, fnvl_f80 b, u32 fcw, u32 *fsw);   /* a / b */
fnvl_f80 fnvl_x_sqrt(fnvl_f80 a, u32 fcw, u32 *fsw);
fnvl_f80 fnvl_x_rndint(fnvl_f80 a, u32 fcw, u32 *fsw);
fnvl_f80 fnvl_x_from_f32(u32 bits, u32 *fsw);
fnvl_f80 fnvl_x_from_f64(u64 bits, u32 *fsw);
fnvl_f80 fnvl_x_from_i64(long long v);
u32 fnvl_x_to_f32(fnvl_f80 a, u32 fcw, u32 *fsw);
u64 fnvl_x_to_f64(fnvl_f80 a, u32 fcw, u32 *fsw);
long long fnvl_x_to_i64(fnvl_f80 a, u32 rc, u32 bits, u32 *fsw);  /* bits = 16, 32, 64; fist/fistp/fisttp */
u32 fnvl_x_cmp(fnvl_f80 a, fnvl_f80 b, u32 quiet, u32 *fsw);       /* C3<<14 | C2<<10 | C0<<8 */
u32 fnvl_x_xam(fnvl_f80 a, u32 empty);                             /* C3,C2,C1,C0 in fsw positions */
void fnvl_x_to_bcd(fnvl_f80 a, u32 fcw, u8 *out10, u32 *fsw);       /* fbstp: 18-digit packed BCD, indefinite on error */
fnvl_f80 fnvl_x_from_bcd(const u8 *in10);                         /* fbld */
#ifdef __cplusplus
}
#endif
#endif
