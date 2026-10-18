// SPDX-License-Identifier: MIT
#include <unistd.h>
#include <errno.h>
#include <stdio.h>
static int horizon_rename(const char *a,const char *b){if(access(b,F_OK)==0){errno=EEXIST;return -1;}return rename(a,b);}
#define rename horizon_rename
#define __SWITCH__
#include "../worker/source/mailbox.c"
#include <assert.h>
#include <stdlib.h>
static int actions;
int sidecar_control(unsigned op,const unsigned char*d,unsigned len,unsigned char*out,int*e){(void)op;(void)d;(void)len;(void)out;*e=0;actions++;return 0;}
static void request(uint64_t seq){
 unsigned char p[128]={0};memcpy(p,"SCMAIL01",8);memcpy(p+8,&boot_token,8);memcpy(p+16,&seq,8);uint64_t deadline=20000;memcpy(p+24,&deadline,8);uint32_t op=40;memcpy(p+32,&op,4);uint64_t h=mailbox_checksum(p);memcpy(p+120,&h,8);FILE*f=fopen(BOX "/request.bin","wb");assert(f);assert(fwrite(p,1,128,f)==128);assert(!fclose(f));
}
int main(void){
 char dir[]="/tmp/sidecar-mailbox-test-XXXXXX";assert(mkdtemp(dir));assert(!chdir(dir));mkdir("sdmc:",0700);mkdir("sdmc:/config",0700);
 mailbox_tick(1000);assert(boot_token);request(1);assert(mailbox_tick(1250)==1);assert(actions==1);assert(!mailbox_tick(1500));assert(actions==1);
 /* Claim cannot be published: no button action and no later replay. */
 request(2);assert(!mkdir(BOX "/status.tmp",0700));assert(!mailbox_tick(1750));assert(actions==1);assert(!rmdir(BOX "/status.tmp"));assert(!mailbox_tick(2000));assert(actions==1);
 /* Old request must never execute after a new process starts. */
 request(3);boot_token=0;last_seq=0;next_poll=0;next_status=0;assert(!mailbox_tick(2250));assert(actions==1);
 /* Short transfer ignored; complete fresh request executes once. */
 FILE*f=fopen(BOX "/request.bin","wb");fputs("SCMAIL01",f);fclose(f);assert(!mailbox_tick(2500));assert(actions==1);request(1);assert(mailbox_tick(2750)==1);assert(actions==2);
 puts("PASS actual file loop: once-only, failed durable claim, old boot, short transfer, fresh request");
}
