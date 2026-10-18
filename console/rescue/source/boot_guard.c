// SPDX-License-Identifier: MIT
#include "boot_guard.h"
#include <stdio.h>
#include <string.h>
#include <strings.h>
#include <errno.h>
#include <ctype.h>
#include <stdbool.h>
#include <unistd.h>
#include <sys/stat.h>

#define GUARD_TEMP GUARD_DIR "/boot-guard.tmp"
#define GUARD_MAGIC 0x44524742U
#define LAST_MAGIC 0x54534c42U
#define SUFFIX ".sidecar-disabled"
typedef struct {char from[200],to[200];} GuardEntry;
typedef struct {uint32_t magic,boots,count,reserved;GuardEntry entries[GUARD_MAX_ENTRIES];} GuardJournal;
#define HEADER 16

static bool padded(const char *s) {
    const char *end=memchr(s,0,200);if(!end || end==s)return false;
    for(;end<s+200;end++)if(*end)return false;
    return true;
}
/* sdmc:/atmosphere/contents/<16 hex>/flags/boot2.flag[.sidecar-disabled], never Worker/Rescue. */
static bool flag_path(const char *p) {
    const char *root="sdmc:/atmosphere/contents/";size_t r=strlen(root);
    if(strncmp(p,root,r))return false;
    for(size_t i=r;i<r+16;i++)if(!isxdigit((unsigned char)p[i]))return false;
    if(!strncasecmp(p+r,"420000000000D06B",16) || !strncasecmp(p+r,"420000000000D06C",16))return false;
    if(strncmp(p+r+16,"/flags/boot2.flag",17))return false;
    const char *rest=p+r+33;
    return !*rest || !strcmp(rest,SUFFIX);
}
static bool entry_valid(const GuardEntry *e) {
    if(!padded(e->from) || !padded(e->to) || !flag_path(e->from) || !flag_path(e->to))return false;
    size_t a=strlen(e->from),b=strlen(e->to),k=strlen(SUFFIX);
    if(a+k==b)return !strncmp(e->from,e->to,a) && !strcmp(e->to+a,SUFFIX);
    if(b+k==a)return !strncmp(e->to,e->from,b) && !strcmp(e->from+b,SUFFIX);
    return false;
}
static int flush_close(FILE *f) {
    int e=0;if(fflush(f) || fsync(fileno(f)))e=errno;
    if(fclose(f) && !e)e=errno;
    return e;
}
static int load(GuardJournal *j) {
    FILE *f=fopen(GUARD_JOURNAL,"rb");if(!f)return errno;
    memset(j,0,sizeof(*j));size_t n=fread(j,1,sizeof(*j),f);int extra=fgetc(f);fclose(f);
    if(n<HEADER || extra!=EOF || j->magic!=GUARD_MAGIC || j->count<1 || j->count>GUARD_MAX_ENTRIES ||
       n!=HEADER+sizeof(GuardEntry)*j->count)return EINVAL;
    for(uint32_t i=0;i<j->count;i++)if(!entry_valid(&j->entries[i]))return EINVAL;
    return 0;
}
static void record_last(uint32_t result,uint32_t count) {
    uint32_t v[3]={LAST_MAGIC,result,count};
    FILE *f=fopen(GUARD_LAST,"wb");if(!f)return;
    if(fwrite(v,1,sizeof(v),f)!=sizeof(v)){fclose(f);return;}
    flush_close(f);
}
int guard_arm(const unsigned char *data,unsigned len) {
    GuardJournal j={.magic=GUARD_MAGIC};
    if(len<4)return EINVAL;
    memcpy(&j.count,data,4);
    if(j.count<1 || j.count>GUARD_MAX_ENTRIES || len!=4+sizeof(GuardEntry)*j.count)return EINVAL;
    memcpy(j.entries,data+4,sizeof(GuardEntry)*j.count);
    for(uint32_t i=0;i<j.count;i++)if(!entry_valid(&j.entries[i]))return EINVAL;
    if(!access(GUARD_JOURNAL,F_OK))return EEXIST;
    if(mkdir(GUARD_DIR,0700) && errno!=EEXIST)return errno;
    if(unlink(GUARD_TEMP) && errno!=ENOENT)return errno;
    FILE *f=fopen(GUARD_TEMP,"wb");if(!f)return errno;
    size_t size=HEADER+sizeof(GuardEntry)*j.count;
    if(fwrite(&j,1,size,f)!=size){fclose(f);unlink(GUARD_TEMP);return EIO;}
    int e=flush_close(f);if(e){unlink(GUARD_TEMP);return e;}
    /* Journal appears complete or not at all; Horizon rename never replaces. */
    if(rename(GUARD_TEMP,GUARD_JOURNAL)){e=errno;unlink(GUARD_TEMP);return e;}
    return 0;
}
int guard_confirm(void){return unlink(GUARD_JOURNAL)?errno:0;}
void guard_status(uint32_t out[6]) {
    GuardJournal j;int e=load(&j);
    out[0]=e!=ENOENT;out[1]=e?0:j.boots;out[2]=GUARD_THRESHOLD;out[3]=e?0:j.count;out[4]=out[5]=0;
    uint32_t v[3];FILE *f=fopen(GUARD_LAST,"rb");
    if(f){if(fread(v,1,sizeof(v),f)==sizeof(v) && v[0]==LAST_MAGIC){out[4]=v[1];out[5]=v[2];}fclose(f);}
}
int guard_boot(void) {
    GuardJournal j;int e=load(&j);
    if(e==ENOENT)return 0;
    /* A journal that fails validation is never acted on; confirm (op71) removes it. */
    if(e){record_last((uint32_t)e,0);return e;}
    j.boots++;
    FILE *f=fopen(GUARD_JOURNAL,"r+b");if(!f)return errno;
    if(fwrite(&j,1,HEADER,f)!=HEADER){fclose(f);return EIO;}
    if((e=flush_close(f)))return e;
    if(j.boots<GUARD_THRESHOLD)return 0;
    int result=0;uint32_t count=0;struct stat st;
    for(uint32_t i=0;i<j.count;i++) {
        const GuardEntry *g=&j.entries[i];
        if(stat(g->from,&st) || !access(g->to,F_OK))continue;
        if(rename(g->from,g->to)){if(!result)result=errno;}else count++;
    }
    record_last((uint32_t)result,count);
    if(unlink(GUARD_JOURNAL) && !result)result=errno;
    return result;
}
