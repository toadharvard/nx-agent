/* SPDX-License-Identifier: GPL-2.0-only */
#include "remote_core.h"
#include <stdlib.h>
#include <string.h>

#define ACT_IDLE 0
#define ACT_AWAIT_REPLY 1
#define ACT_READY 2

static unsigned char *snapshot;
static uint32_t generation,x,y,release_at;
static int held,release_pending;

static uint32_t wd_period_ms,wd_deadline,host_commands,caps;
static int wd_enabled,nested;
static int act_kind,act_state,act_armed_now;
static uint32_t act_arg,act_since;
static remote_entries_fn entries_fn;
static void *entries_ctx;

void remote_core_reset(void){free(snapshot);snapshot=NULL;held=0;release_pending=1;}

void remote_core_init(uint32_t now,uint32_t watchdog_s,uint32_t device_caps){
 remote_core_reset();
 if(watchdog_s>REMOTE_WATCHDOG_MAX_S)watchdog_s=REMOTE_WATCHDOG_MAX_S;
 wd_enabled=watchdog_s!=0;
 wd_period_ms=watchdog_s*1000U;
 wd_deadline=now+wd_period_ms;
 host_commands=0;
 caps=device_caps;
 act_kind=REMOTE_ACT_NONE;
 act_state=ACT_IDLE;
 act_armed_now=0;
 act_arg=0;
 act_since=now;
 nested=0;
}

void remote_core_set_entries(remote_entries_fn fn,void *ctx){entries_fn=fn;entries_ctx=ctx;}

void remote_core_set_nested(int value){nested=value!=0;}

void remote_core_kick(uint32_t now){wd_deadline=now+wd_period_ms;}

uint32_t remote_core_parse_watchdog(const char *value){
 uint32_t v=0,digits=0;

 if(!value)
  return REMOTE_WATCHDOG_DEFAULT_S;

 for(;*value;value++){
  if(*value<'0' || *value>'9' || ++digits>7)
   return REMOTE_WATCHDOG_DEFAULT_S;

  v=v*10U+(uint32_t)(*value-'0');
 }

 if(!digits)
  return REMOTE_WATCHDOG_DEFAULT_S;

 if(!v)
  return 0;

 if(v<REMOTE_WATCHDOG_MIN_S)
  return REMOTE_WATCHDOG_MIN_S;

 if(v>REMOTE_WATCHDOG_MAX_S)
  return REMOTE_WATCHDOG_MAX_S;

 return v;
}

int remote_core_touch(uint32_t now,uint32_t *px,uint32_t *py,int *pressed){
 if(held && (int32_t)(now-release_at)>=0){held=0;release_pending=1;}
 if(!held && !release_pending)return 0;
 *px=x;*py=y;*pressed=held;release_pending=0;return 1;
}

static uint32_t _seconds_left(uint32_t now){
 int32_t left=(int32_t)(wd_deadline-now);

 if(!wd_enabled || left<=0)
  return 0;

 return ((uint32_t)left+999U)/1000U;
}

void remote_core_status(uint32_t now,uint32_t out[4]){
 out[0]=REMOTE_VERSION;
 out[1]=_seconds_left(now);
 out[2]=(uint32_t)wd_enabled;
 out[3]=host_commands;
}

static int _args_zero(const uint32_t *q,uint32_t first){
 for(uint32_t i=first;i<16;i++){
  if(q[i])
   return 0;
 }

 return 1;
}

static uint32_t _arm(int kind,uint32_t arg,uint32_t now){
 if(act_kind!=REMOTE_ACT_NONE)
  return REMOTE_E_BUSY;

 act_kind=kind;
 act_arg=arg;
 act_state=ACT_AWAIT_REPLY;
 act_since=now;
 act_armed_now=1;

 return REMOTE_OK;
}

static uint32_t _mutating(const uint32_t *q,uint32_t now){
 switch(q[2]){
 case 7:
  return _args_zero(q,3)?_arm(REMOTE_ACT_LAUNCH,0,now):REMOTE_E_ARG;
 case 8:
  return _args_zero(q,3)?_arm(REMOTE_ACT_UMS,0,now):REMOTE_E_ARG;
 case 9:
  if(q[3]>REMOTE_REBOOT_NORMAL || !_args_zero(q,4))
   return REMOTE_E_ARG;

  if(!(caps&(q[3]==REMOTE_REBOOT_OFW?REMOTE_CAP_REBOOT_OFW:REMOTE_CAP_REBOOT_NORMAL)))
   return REMOTE_E_UNAVAIL;

  return _arm(REMOTE_ACT_REBOOT,q[3],now);
 case 10:
  return _args_zero(q,3)?_arm(REMOTE_ACT_POWEROFF,0,now):REMOTE_E_ARG;
 }

 return REMOTE_E_OP;
}

static uint32_t _watchdog_op(const uint32_t *q,uint32_t now){
 if(!_args_zero(q,4))
  return REMOTE_E_ARG;

 if(!q[3]){
  wd_enabled=0;

  return REMOTE_OK;
 }

 if(q[3]<REMOTE_WATCHDOG_MIN_S || q[3]>REMOTE_WATCHDOG_MAX_S)
  return REMOTE_E_ARG;

 wd_enabled=1;
 wd_period_ms=q[3]*1000U;
 wd_deadline=now+wd_period_ms;

 return REMOTE_OK;
}

void remote_core_request(const void *request,uint32_t length,void *reply,uint32_t now,const void *framebuffer){
 uint32_t q[16]={0},h[8]={REMOTE_MAGIC,0,0,0,0,0,0,0};unsigned char *out=reply;
 memset(reply,0,REMOTE_REPLY_SIZE);
 act_armed_now=0;
 if(length!=64){h[2]=REMOTE_E_ARG;goto done;}
 memcpy(q,request,64);h[1]=q[1];h[4]=q[2];
 if(q[0]!=REMOTE_MAGIC){h[2]=REMOTE_E_ARG;goto done;}
 if(q[2]>=1 && q[2]<=12){
  /* Any well-formed request with a known op proves the host is alive. */
  host_commands++;
  remote_core_kick(now);
 }
 if(nested && q[2]>=6 && q[2]<=10){
  /* SD access and side effects wait for the main loop; a blocking tool owns the system. */
  h[2]=REMOTE_E_BUSY;
  goto done;
 }
 switch(q[2]){
 case 1:{uint32_t info[]={1,1280,720,4,REMOTE_FRAME_SIZE,500};memcpy(out+32,info,sizeof(info));h[3]=sizeof(info);break;}
 case 2:
  if(!snapshot)snapshot=malloc(REMOTE_FRAME_SIZE);
  if(!snapshot || !framebuffer){h[2]=REMOTE_E_NOMEM;break;}
  memcpy(snapshot,framebuffer,REMOTE_FRAME_SIZE);if(!++generation)++generation;
  h[5]=generation;h[3]=4;memcpy(out+32,&generation,4);break;
 case 3:
  if(!snapshot || q[3]!=generation){h[2]=REMOTE_E_STALE;break;}
  if(q[4]>=REMOTE_FRAME_SIZE || !q[5] || q[5]>REMOTE_DATA_SIZE || q[5]>REMOTE_FRAME_SIZE-q[4]){h[2]=REMOTE_E_ARG;break;}
  memcpy(out+32,snapshot+q[4],q[5]);h[3]=q[5];h[5]=generation;break;
 case 4:
  if(q[3]>=1280 || q[4]>=720 || !q[5] || q[5]>500){h[2]=REMOTE_E_ARG;break;}
  x=q[3];y=q[4];release_at=now+q[5];held=1;release_pending=0;break;
 case 5:held=0;release_pending=1;break;
 case 6:{
  uint32_t len=0;

  if(!entries_fn){h[2]=REMOTE_E_UNAVAIL;break;}

  h[2]=entries_fn(entries_ctx,(char *)out+32,REMOTE_DATA_SIZE,&len);

  if(h[2] || len>REMOTE_DATA_SIZE){
   if(!h[2])h[2]=REMOTE_E_TOOBIG;
   memset(out+32,0,REMOTE_DATA_SIZE);
   break;
  }

  h[3]=len;
  break;
 }
 case 7:
 case 8:
 case 9:
 case 10:
  h[2]=_mutating(q,now);
  break;
 case 11:
  h[2]=_watchdog_op(q,now);
  break;
 case 12:{
  uint32_t st[4];

  remote_core_status(now,st);
  memcpy(out+32,st,sizeof(st));
  h[3]=sizeof(st);
  break;
 }
 default:h[2]=REMOTE_E_OP;break;
 }
 done:memcpy(out,h,32);
}

/* Only the action armed by the request just answered moves forward. */
void remote_core_reply_done(uint32_t now){
 if(!act_armed_now || act_state!=ACT_AWAIT_REPLY)
  return;

 act_armed_now=0;
 act_state=ACT_READY;
 act_since=now;
}

void remote_core_reply_failed(uint32_t now){remote_core_reply_done(now);}

int remote_core_take_action(uint32_t now,uint32_t *arg){
 int kind=REMOTE_ACT_NONE;

 if(act_kind==REMOTE_ACT_NONE){
  if(wd_enabled && (int32_t)(now-wd_deadline)>=0){
   /* Expiry behaves exactly like op7 without a reply to wait for. */
   wd_deadline=now+wd_period_ms;
   if(arg)*arg=0;

   return REMOTE_ACT_LAUNCH;
  }

  return REMOTE_ACT_NONE;
 }

 if(act_state==ACT_READY && (uint32_t)(now-act_since)>=REMOTE_ACTION_DELAY_MS)
  kind=act_kind;
 else if(act_state==ACT_AWAIT_REPLY && (uint32_t)(now-act_since)>=REMOTE_ACTION_REPLY_TIMEOUT_MS)
  kind=act_kind;

 if(kind==REMOTE_ACT_NONE)
  return REMOTE_ACT_NONE;

 if(arg)*arg=act_arg;
 act_kind=REMOTE_ACT_NONE;
 act_state=ACT_IDLE;
 act_armed_now=0;
 wd_deadline=now+wd_period_ms;

 return kind;
}

static uint32_t _put(char *out,uint32_t cap,uint32_t *pos,const char *s,uint32_t n){
 if(n>cap-*pos)
  return REMOTE_E_TOOBIG;

 memcpy(out+*pos,s,n);
 *pos+=n;

 return REMOTE_OK;
}

static uint32_t _put_line(char *out,uint32_t cap,uint32_t *pos,uint32_t idx,const char *name){
 char num[11];
 uint32_t n=0,start=*pos,name_len=name?(uint32_t)strlen(name):0;

 do{num[sizeof(num)-1-n]=(char)('0'+idx%10U);idx/=10U;n++;}while(idx && n<sizeof(num));

 if(_put(out,cap,pos,num+sizeof(num)-n,n) || _put(out,cap,pos,"\t",1) || name_len>cap-*pos){
  *pos=start;

  return REMOTE_E_TOOBIG;
 }

 for(uint32_t i=0;i<name_len;i++){
  unsigned char c=(unsigned char)name[i];
  out[(*pos)++]=(c<0x20 || c==0x7f)?'?':(char)c;
 }

 if(_put(out,cap,pos,"\n",1)){
  *pos=start;

  return REMOTE_E_TOOBIG;
 }

 return REMOTE_OK;
}

/*
 * Mirrors bootloader/main.c _auto_launch(): every INI_CHOICE section increments the
 * index, the first [config] included; only sections after the first [config] can be
 * selected by autoboot, so only they are listed (with that index).
 */
uint32_t remote_core_format_entries(const remote_ini_sec_t *secs,uint32_t count,char *out,uint32_t cap,uint32_t *len){
 uint32_t idx=0,pos=0;
 int config_found=0;

 *len=0;

 for(uint32_t i=0;i<count;i++){
  if(secs[i].type!=REMOTE_INI_CHOICE)
   continue;

  if(!config_found && secs[i].name && !strcmp(secs[i].name,"config")){
   config_found=1;
   idx++;

   continue;
  }

  if(config_found && _put_line(out,cap,&pos,idx,secs[i].name))
   return REMOTE_E_TOOBIG;

  idx++;
 }

 *len=pos;

 return REMOTE_OK;
}

int remote_core_check_hekate(const void *image,uint32_t size){
 uint32_t magic;

 if(!image || size<=REMOTE_HEKATE_BCFG_OFF+REMOTE_HEKATE_BCFG_SIZE+8U || size>REMOTE_HEKATE_MAX_SIZE)
  return 0;

 memcpy(&magic,(const unsigned char *)image+REMOTE_HEKATE_BCFG_OFF+REMOTE_HEKATE_BCFG_SIZE,4);

 return magic==REMOTE_HEKATE_MAGIC;
}
