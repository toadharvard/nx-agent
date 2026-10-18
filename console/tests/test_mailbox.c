// SPDX-License-Identifier: MIT
#include "../worker/source/mailbox.h"
#include <assert.h>
#include <string.h>
#include <errno.h>
#include <stdio.h>
static void w64(unsigned char*p,uint64_t v){memcpy(p,&v,8);}
static void w32(unsigned char*p,uint32_t v){memcpy(p,&v,4);}
static void seal(unsigned char*p){w64(p+120,mailbox_checksum(p));}
int main(void){
 unsigned char p[128]={0};memcpy(p,"SCMAIL01",8);w64(p+8,123);w64(p+16,5);w64(p+24,20000);w32(p+32,41);w32(p+36,28);seal(p);
 assert(mailbox_validate(p,123,4,10000)==0);
 assert(mailbox_validate(p,123,5,10000)==EACCES);
 assert(mailbox_validate(p,124,4,10000)==EACCES);
 assert(mailbox_validate(p,123,4,20001)==ETIMEDOUT);
 w64(p+24,50000);seal(p);assert(mailbox_validate(p,123,4,10000)==ETIMEDOUT);
 w64(p+24,20000);w32(p+32,46);seal(p);assert(mailbox_validate(p,123,4,10000)==EINVAL);
 w32(p+32,41);w32(p+36,29);seal(p);assert(mailbox_validate(p,123,4,10000)==EINVAL);
 w32(p+36,28);seal(p);p[40]^=1;assert(mailbox_validate(p,123,4,10000)==EINVAL);
 struct {unsigned op,len;int want;} v7[]={{54,0,0},{54,4,EINVAL},{52,4,0},{52,32,0},{52,60,0},{52,88,EINVAL},{52,31,EINVAL},{52,0,EINVAL},{52,0xffffffe4u,EINVAL},
  {53,6,EINVAL},{55,4,EINVAL},{40,1,0},{40,2,EINVAL},{42,0,0},{45,0,0},{48,0,0},{49,0,0},{50,4,0}};
 for(unsigned i=0;i<sizeof(v7)/sizeof(*v7);i++){memset(p+40,0,80);w32(p+32,v7[i].op);w32(p+36,v7[i].len);seal(p);assert(mailbox_validate(p,123,4,10000)==v7[i].want);}
 puts("PASS valid, duplicate, old boot, expiry, excessive TTL, denied reboot, malformed length, partial/corrupt payload, v7 op list (54, 52 count<=2; 53/55 denied)");
}
