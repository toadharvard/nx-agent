// SPDX-License-Identifier: MIT
#include "mailbox.h"
#include <string.h>
#include <errno.h>
#include <stdbool.h>
static uint64_t read64(const unsigned char*p){uint64_t v;memcpy(&v,p,8);return v;}
static uint32_t read32(const unsigned char*p){uint32_t v;memcpy(&v,p,4);return v;}
uint64_t mailbox_checksum(const unsigned char*p){uint64_t h=14695981039346656037ULL;for(int i=0;i<120;i++){h^=p[i];h*=1099511628211ULL;}return h;}
int mailbox_validate(const unsigned char*p,uint64_t token,uint64_t last,uint64_t now){
 if(memcmp(p,"SCMAIL01",8)||read64(p+120)!=mailbox_checksum(p))return EINVAL;
 if(read64(p+8)!=token||read64(p+16)<=last)return EACCES;
 uint64_t until=read64(p+24);if(until<now||until-now>30000)return ETIMEDOUT;
 unsigned op=read32(p+32),len=read32(p+36);
 /* op52 payload must fit the 80-byte data area before the checksum: count<=2. */
 bool seq=op==52&&len>=4&&len<=80&&(len-4)%28==0;
 if(!((op==40&&len<=1)||(op==41&&len==28)||((op==42||op==45||op==48||op==49||op==54)&&len==0)||(op==50&&len==4)||(op==56&&len==1)||seq))return EINVAL;
 return 0;
}
#ifdef __SWITCH__
#include <switch.h>
#include <stdio.h>
#include <unistd.h>
#include <sys/stat.h>
#include "control.h"
#define BOX "sdmc:/config/usb-worker-mailbox"
static uint64_t boot_token,last_seq,next_poll,next_status;
static char phase[16]="ready";
static int result,reply_len;
static unsigned char reply[64];
static int publish(uint64_t now){
 FILE*f=fopen(BOX "/status.tmp","wb");if(!f)return errno;
 int failed=fprintf(f,"{\"version\":1,\"token\":\"%016llx\",\"now_ms\":%llu,\"sequence\":%llu,\"phase\":\"%s\",\"result\":%d,\"reply\":\"",(unsigned long long)boot_token,(unsigned long long)now,(unsigned long long)last_seq,phase,result)<0;
 for(int i=0;i<reply_len;i++)if(fprintf(f,"%02x",reply[i])<0)failed=1;
 if(fputs("\"}\n",f)==EOF||fflush(f))failed=1;
 if(fsync(fileno(f)))failed=1;
 if(fclose(f))failed=1;
 if(failed)return EIO;
 /* Horizon rename does not replace an existing destination. During the
  * short gap readers must retry only the read, never the command. */
 if(unlink(BOX "/status.json") && errno!=ENOENT)return errno;
 if(rename(BOX "/status.tmp",BOX "/status.json"))return errno;
 return 0;
}
int mailbox_tick(uint64_t now){
 if(now<next_poll)return 0;
 next_poll=now+250;
 if(!boot_token){randomGet(&boot_token,sizeof(boot_token));if(!boot_token)boot_token=1;mkdir(BOX,0700);}
 if(now>=next_status){if(publish(now))return 0;next_status=now+5000;}
 FILE*f=fopen(BOX "/request.bin","rb");if(!f)return 0;
 unsigned char request[129];size_t n=fread(request,1,sizeof(request),f);int bad=ferror(f);fclose(f);
 if(bad||n!=128||mailbox_validate(request,boot_token,last_seq,now))return 0;
 last_seq=read64(request+16);strcpy(phase,"claimed");result=0;reply_len=0;
 /* Persist claim before any action. A new process has a different token. */
 if(publish(now))return 0;
 unsigned char out[4096]={0};
 int count=sidecar_control(read32(request+32),request+40,read32(request+36),out,&result);
 if(count>=0&&count<=64){reply_len=count;memcpy(reply,out,count);}else result=EOVERFLOW;
 strcpy(phase,"done");publish(now);next_status=now+5000;
 return 1;
}
#endif
