// SPDX-License-Identifier: MIT
#include "logic.h"
#include <string.h>
#include <errno.h>
void pad_route(uint64_t buttons,const int32_t sticks[4],PadSide *right,PadSide *left) {
    right->buttons=buttons&PAD_RIGHT_MASK;right->x=sticks[2];right->y=sticks[3];
    left->buttons=buttons&PAD_VALID_MASK&~PAD_RIGHT_MASK;left->x=sticks[0];left->y=sticks[1];
}
int pad_step_parse(const unsigned char *d,uint32_t max_ms,PadStep *s) {
    memcpy(&s->buttons,d,8);memcpy(s->sticks,d+8,16);memcpy(&s->ms,d+24,4);
    if(s->ms<1 || s->ms>max_ms || s->buttons&~PAD_VALID_MASK)return EINVAL;
    for(int i=0;i<4;i++)if(s->sticks[i]<-32767 || s->sticks[i]>32767)return EINVAL;
    return 0;
}
int seq_parse(const unsigned char *data,unsigned len,PadStep *steps,unsigned *count) {
    *count=0;if(len<4)return EINVAL;
    uint32_t n;memcpy(&n,data,4);
    if(!n)return len==4?0:EINVAL;
    if(n>SEQ_MAX_STEPS || len!=4+28*n)return EINVAL;
    uint32_t total=0;
    for(unsigned i=0;i<n;i++) {
        if(pad_step_parse(data+4+28*i,30000,&steps[i]))return EINVAL;
        total+=steps[i].ms;
    }
    if(total>SEQ_MAX_TOTAL_MS)return EINVAL;
    *count=n;return 0;
}
/* 12-bit word: reason bits 0-3, autoboot index 4-7, autoboot list 8, UMS index 9-11; split into two 6-bit RTC alarm regs. */
int r2c_encode(unsigned reason,unsigned idx,uint8_t *val1,uint8_t *val2) {
    unsigned v;
    if(reason==1 && idx>=1 && idx<=8)v=reason|idx<<4;
    else if(reason==2 && !idx)v=reason;
    else if(reason==3 && !idx)v=reason|idx<<9;
    else return EINVAL;
    *val1=v&0x3f;*val2=(v>>6)&0x3f;return 0;
}
int r2c_decode(uint8_t val1,uint8_t val2,unsigned *reason,unsigned *idx) {
    if(val1>0x3f || val2>0x3f)return EINVAL;
    unsigned v=val1|(unsigned)val2<<6,r=v&0xf,i=(v>>4)&0xf;
    if(v>>8)return EINVAL;
    if(r==1?(i<1 || i>8):((r!=2 && r!=3) || i))return EINVAL;
    *reason=r;*idx=i;return 0;
}
