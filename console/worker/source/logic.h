// SPDX-License-Identifier: MIT
#pragma once
#include <stdint.h>
/* Pure helpers shared by the worker and host tests; no libnx dependency. */
#define PAD_VALID_MASK 0xfffffULL
/* A,B,X,Y,RSTICK,R,ZR,PLUS,HOME(bit18) go to the right rail device; everything else left. */
#define PAD_RIGHT_MASK (0xfULL|1ULL<<5|1ULL<<7|1ULL<<9|1ULL<<10|1ULL<<18)
#define SEQ_MAX_STEPS 64
#define SEQ_MAX_TOTAL_MS 60000
typedef struct {uint64_t buttons;int32_t x,y;} PadSide;
typedef struct {uint64_t buttons;int32_t sticks[4];uint32_t ms;} PadStep;
void pad_route(uint64_t buttons,const int32_t sticks[4],PadSide *right,PadSide *left);
int pad_step_parse(const unsigned char *data,uint32_t max_ms,PadStep *step);
/* 0 with *count=0 means cancel; otherwise *count steps parsed. */
int seq_parse(const unsigned char *data,unsigned len,PadStep *steps,unsigned *count);
int r2c_encode(unsigned reason,unsigned idx,uint8_t *val1,uint8_t *val2);
int r2c_decode(uint8_t val1,uint8_t val2,unsigned *reason,unsigned *idx);
