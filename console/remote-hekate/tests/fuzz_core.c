/* SPDX-License-Identifier: GPL-2.0-only */
/* ASan/UBSan fuzz of remote_core_request: random requests must never touch memory outside the reply. */
#include "remote_core.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned char frame[REMOTE_FRAME_SIZE];
static uint64_t rng=0x20261015ULL;

static uint32_t rnd(void){rng^=rng<<13;rng^=rng>>7;rng^=rng<<17;return (uint32_t)rng;}

static uint32_t entries(void *ctx,char *out,uint32_t cap,uint32_t *len){
 static const remote_ini_sec_t secs[]={{3,"config"},{3,"4mosphere"},{0xFE,NULL},{3,"Stock (semi-stock)"}};
 (void)ctx;
 return remote_core_format_entries(secs,4,out,cap,len);
}

int main(int argc,char **argv){
 long n=argc>1?atol(argv[1]):100000;
 uint32_t now=1000,q[16];
 unsigned char *reply=malloc(REMOTE_REPLY_SIZE);

 remote_core_init(now,90,3);
 remote_core_set_entries(entries,NULL);

 for(long i=0;i<n;i++){
  uint32_t length=(rnd()%20)?64:rnd()%65,kind,arg;

  for(int j=0;j<16;j++)q[j]=(rnd()%4)?rnd()%8:rnd();
  if(rnd()%8)q[0]=REMOTE_MAGIC;
  if(rnd()%4)q[2]=1+rnd()%13;
  if(q[2]==2 && rnd()%256)q[2]=12; /* snapshot copies 3.6 MB; keep it rare */
  if(q[2]==3 && rnd()%2){q[3]=0;}

  now+=rnd()%300;
  remote_core_set_nested(rnd()%16==0);
  remote_core_request(q,length,reply,now,frame);

  if(((uint32_t *)reply)[0]!=REMOTE_MAGIC || ((uint32_t *)reply)[3]>REMOTE_DATA_SIZE){
   fprintf(stderr,"bad header at %ld\n",i);
   return 1;
  }

  if(rnd()%2)remote_core_reply_done(now);
  else if(rnd()%2)remote_core_reply_failed(now);
  kind=remote_core_take_action(now+rnd()%2500,&arg);
  if(kind>REMOTE_ACT_POWEROFF){fprintf(stderr,"bad action\n");return 1;}
  if(rnd()%64==0)remote_core_reset();
  if(rnd()%128==0){uint32_t x,y;int p;remote_core_touch(now,&x,&y,&p);}
 }

 free(reply);
 remote_core_reset();
 printf("fuzz_core: %ld requests OK\n",n);

 return 0;
}
