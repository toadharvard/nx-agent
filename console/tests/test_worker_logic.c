// SPDX-License-Identifier: MIT
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include "../common/r2c_layout.h"
#include "logic.h"
static void step(unsigned char *p,uint64_t b,int32_t lx,int32_t ly,int32_t rx,int32_t ry,uint32_t ms){
 int32_t s[4]={lx,ly,rx,ry};memcpy(p,&b,8);memcpy(p+8,s,16);memcpy(p+24,&ms,4);
}
static unsigned seq(unsigned char *p,uint32_t n,const uint32_t *ms){
 memcpy(p,&n,4);for(uint32_t i=0;i<n;i++)step(p+4+28*i,1,0,0,0,0,ms[i]);return 4+28*n;
}
int main(void){
 const int right[]={0,1,2,3,5,7,9,10,18},left[]={4,6,8,11,12,13,14,15,16,17,19};
 int32_t sticks[4]={-100,200,300,-400};PadSide r,l;
 for(unsigned i=0;i<sizeof(right)/sizeof(*right);i++){pad_route(1ULL<<right[i],sticks,&r,&l);assert(r.buttons==1ULL<<right[i]&&!l.buttons);}
 for(unsigned i=0;i<sizeof(left)/sizeof(*left);i++){pad_route(1ULL<<left[i],sticks,&r,&l);assert(l.buttons==1ULL<<left[i]&&!r.buttons);}
 pad_route(PAD_VALID_MASK|1ULL<<20,sticks,&r,&l);assert((r.buttons|l.buttons)==PAD_VALID_MASK&&!(r.buttons&l.buttons));
 assert(r.x==300&&r.y==-400&&l.x==-100&&l.y==200);
 unsigned char b[4+28*65];PadStep st,steps[SEQ_MAX_STEPS];unsigned count=99;
 step(b,1ULL<<18,0,0,0,0,1000);assert(!pad_step_parse(b,1000,&st)&&st.buttons==1ULL<<18&&st.ms==1000);
 step(b,1ULL<<20,0,0,0,0,10);assert(pad_step_parse(b,1000,&st)==EINVAL);
 step(b,1,0,0,0,0,0);assert(pad_step_parse(b,1000,&st)==EINVAL);
 step(b,1,0,0,0,0,1001);assert(pad_step_parse(b,1000,&st)==EINVAL);
 step(b,1,-32767,32767,0,0,5);assert(!pad_step_parse(b,1000,&st));
 step(b,1,0,0,32768,0,5);assert(pad_step_parse(b,1000,&st)==EINVAL);
 uint32_t zero=0;memcpy(b,&zero,4);assert(!seq_parse(b,4,steps,&count)&&count==0);
 assert(seq_parse(b,32,steps,&count)==EINVAL);assert(seq_parse(b,3,steps,&count)==EINVAL);
 uint32_t ms[65];for(int i=0;i<65;i++)ms[i]=900;
 unsigned n=seq(b,64,ms);assert(!seq_parse(b,n,steps,&count)&&count==64&&steps[63].ms==900);
 assert(seq_parse(b,n-1,steps,&count)==EINVAL);
 n=seq(b,65,ms);assert(seq_parse(b,n,steps,&count)==EINVAL);
 uint32_t big[3]={30000,30000,1};n=seq(b,3,big);assert(seq_parse(b,n,steps,&count)==EINVAL);
 uint32_t exact[2]={30000,30000};n=seq(b,2,exact);assert(!seq_parse(b,n,steps,&count)&&count==2);
 uint32_t over[1]={30001};n=seq(b,1,over);assert(seq_parse(b,n,steps,&count)==EINVAL);
 struct {unsigned reason,idx;uint8_t v1,v2;} ok[]={{1,1,0x11,0},{1,2,0x21,0},{1,3,0x31,0},{1,4,0x01,0x01},{1,5,0x11,0x01},
  {1,6,0x21,0x01},{1,7,0x31,0x01},{1,8,0x01,0x02},{2,0,0x02,0},{3,0,0x03,0}};
 for(unsigned i=0;i<sizeof(ok)/sizeof(*ok);i++){
  uint8_t v1=0xff,v2=0xff;assert(!r2c_encode(ok[i].reason,ok[i].idx,&v1,&v2));assert(v1==ok[i].v1&&v2==ok[i].v2);
  unsigned w=r2c_word(v1,v2);
  assert(r2c_field(w,R2C_REASON_SHIFT,R2C_REASON_MASK)==ok[i].reason&&r2c_field(w,R2C_LIST_SHIFT,R2C_LIST_MASK)==0);
  assert(r2c_field(w,R2C_UMS_SHIFT,R2C_UMS_MASK)==0&&r2c_field(w,R2C_IDX_SHIFT,R2C_IDX_MASK)==(ok[i].reason==R2C_REASON_AUTOBOOT?ok[i].idx:0));
  unsigned reason=99,idx=99;assert(!r2c_decode(v1,v2,&reason,&idx)&&reason==ok[i].reason&&idx==ok[i].idx);
 }
 unsigned valid=0;
 for(unsigned v1=0;v1<256;v1++)for(unsigned v2=0;v2<256;v2++){
  unsigned reason,idx;if(r2c_decode(v1,v2,&reason,&idx))continue;
  valid++;assert(v1<64&&v2<64);uint8_t e1,e2;assert(!r2c_encode(reason,idx,&e1,&e2)&&e1==v1&&e2==v2);
 }
 assert(valid==sizeof(ok)/sizeof(*ok));
 struct {unsigned reason,idx;} bad[]={{0,0},{1,0},{1,9},{2,1},{3,1},{3,2},{4,0},{5,0},{1,255}};
 for(unsigned i=0;i<sizeof(bad)/sizeof(*bad);i++){uint8_t v1,v2;assert(r2c_encode(bad[i].reason,bad[i].idx,&v1,&v2)==EINVAL);}
 puts("PASS rail routing, step/sequence limits, R2C golden bytes, encode/decode round-trip");
}
