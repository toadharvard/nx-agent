// SPDX-License-Identifier: MIT
#include "fileops.h"
#ifdef SIDECAR_HOST_TEST
#include <openssl/sha.h>
typedef SHA256_CTX Sha256Context;
#define sha256ContextCreate SHA256_Init
#define sha256ContextUpdate SHA256_Update
#define sha256ContextGetHash(c,h) SHA256_Final(h,c)
#else
#include <switch.h>
#endif
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <sys/stat.h>
#include <ctype.h>

#define BASE "sdmc:/config/usb-sidecar"
#define JOURNAL BASE "/pending.bin"
#define TEMP BASE "/upload.tmp"
#define BACKUP BASE "/previous.bin"
#define LIMIT (256*1024*1024ULL)
/* One recoverable transaction. Host archives previous content before BEGIN.
 * The persistent journal protects the rename gap on FAT; this is not a claim
 * of power-loss atomicity of the SD controller or filesystem itself. */
typedef struct {
    uint32_t magic, exists;
    uint64_t size;
    unsigned char old_hash[32], new_hash[32];
    char path[768];
} Journal;
static Journal current;
static uint64_t received;
static bool active;

static bool under(const char *path, const char *root) {
    size_t n=strlen(root);return !strncmp(path,root,n) && (!path[n] || path[n]=='/');
}
/* Whole SD except user secrets and data, the transaction area and the Worker/Rescue images. */
static const char *const PROTECTED[]={"sdmc:/config/usb-rescue","sdmc:/config/usb-sidecar",
    "sdmc:/atmosphere/contents/420000000000d06b","sdmc:/atmosphere/contents/420000000000d06c"};
static bool lowercase(const char *p,char lower[768]) {
    if(!p || strlen(p)>=768 || strncmp(p,"sdmc:/",6) || strstr(p,"..") ||
       strchr(p,'\\') || strstr(p,"//") || strchr(p+6,':'))return false;
    for(const char *s=p;*s;s++)if((unsigned char)*s<32)return false;
    size_t k=0;
    for(;p[k];k++)lower[k]=(char)tolower((unsigned char)p[k]);
    lower[k]=0;
    return !(strstr(lower,".keys") || strstr(lower,"/automatic_backups") || strstr(lower,"/saves") ||
       strstr(lower,"/backup") || strstr(lower,"/emummc") || strstr(lower,"/nintendo"));
}
bool sidecar_path(const char *p, bool write) {
    char lower[768];
    if(!lowercase(p,lower))return false;
    if(under(lower,BASE))return !write;
    if(!write)return true;
    size_t n=strlen(lower);
    if(n<=6 || lower[n-1]=='/')return false;
    for(size_t i=0;i<sizeof(PROTECTED)/sizeof(*PROTECTED);i++)if(under(lower,PROTECTED[i]))return false;
    return true;
}
/* Renaming or deleting must not move or remove a protected directory with its parent. */
bool sidecar_tree_path(const char *p) {
    char lower[768];
    if(!sidecar_path(p,true) || !lowercase(p,lower))return false;
    for(size_t i=0;i<sizeof(PROTECTED)/sizeof(*PROTECTED);i++)if(under(PROTECTED[i],lower))return false;
    return true;
}
int sidecar_rename(const char *from,const char *to) {
    if(!sidecar_tree_path(from) || !sidecar_tree_path(to))return EACCES;
    if(sidecar_pending())return EBUSY;
    struct stat st;
    if(stat(from,&st))return errno;
    if(!stat(to,&st))return EEXIST;
    if(errno!=ENOENT)return errno;
    return rename(from,to)?errno:0;
}
int sidecar_unlink(const char *p) {
    if(!sidecar_tree_path(p))return EACCES;
    if(sidecar_pending())return EBUSY;
    struct stat st;
    if(stat(p,&st))return errno;
    if(!S_ISREG(st.st_mode))return EISDIR;
    return unlink(p)?errno:0;
}
int sidecar_rmdir(const char *p) {
    if(!sidecar_tree_path(p))return EACCES;
    if(sidecar_pending())return EBUSY;
    struct stat st;
    if(stat(p,&st))return errno;
    if(!S_ISDIR(st.st_mode))return ENOTDIR;
    return rmdir(p)?errno:0;
}
int sidecar_mkdir(const char *p) {
    if(!sidecar_path(p,true))return EACCES;
    return mkdir(p,0777)?errno:0;
}
int sidecar_hash(const char *path,unsigned char hash[32],uint64_t *size) {
    FILE *f=fopen(path,"rb");if(!f)return errno;
    unsigned char buf[8192];Sha256Context ctx;sha256ContextCreate(&ctx);*size=0;
    size_t n;while((n=fread(buf,1,sizeof(buf),f))){sha256ContextUpdate(&ctx,buf,n);*size+=n;}
    int e=ferror(f)?EIO:0;fclose(f);if(!e)sha256ContextGetHash(&ctx,hash);return e;
}
static int flush_close(FILE *f) {
    int e=0;if(fflush(f) || fsync(fileno(f)))e=errno;
    if(fclose(f) && !e)e=errno;
    return e;
}
static int original_matches(const Journal *j) {
    unsigned char hash[32];uint64_t size;int e=sidecar_hash(j->path,hash,&size);
    if(!j->exists)return e==ENOENT?0:(e?e:EEXIST);
    return e?e:(memcmp(hash,j->old_hash,32)?ESTALE:0);
}
bool sidecar_pending(void){return active || access(JOURNAL,F_OK)==0;}
int sidecar_begin(const char *path,uint64_t size,const unsigned char old_hash[32],const unsigned char new_hash[32],bool exists) {
    if(!sidecar_path(path,true))return EACCES;
    if(size>LIMIT)return EFBIG;
    if(sidecar_pending())return EBUSY;
    Journal j={.magic=0x34584353,.exists=exists,.size=size};
    strcpy(j.path,path);memcpy(j.old_hash,old_hash,32);memcpy(j.new_hash,new_hash,32);
    int e=original_matches(&j);if(e)return e;
    if(mkdir(BASE,0700) && errno!=EEXIST)return errno;
    /* Previous transaction has been acknowledged by removing its journal. */
    if(unlink(BACKUP) && errno!=ENOENT)return errno;
    FILE *f=fopen(JOURNAL,"wb");if(!f)return errno;
    if(fwrite(&j,1,sizeof(j),f)!=sizeof(j)){fclose(f);return EIO;}
    e=flush_close(f);if(e)return e;
    f=fopen(TEMP,"wb");if(!f)return errno;
    e=flush_close(f);if(e)return e;
    current=j;received=0;active=true;return 0;
}
int sidecar_chunk(uint64_t offset,const void *data,size_t size) {
    if(!active)return ENOENT;
    if(offset!=received || size>4080 || size>current.size-received)return EINVAL;
    FILE *f=fopen(TEMP,"ab");if(!f)return errno;
    int e=fwrite(data,1,size,f)==size?0:EIO;
    if(fclose(f) && !e)e=errno;
    if(!e)received+=size;
    return e;
}
int sidecar_commit(void) {
    if(!active || received!=current.size)return EINVAL;
    unsigned char hash[32];uint64_t size;int e=sidecar_hash(TEMP,hash,&size);
    if(e)return e;
    if(size!=current.size || memcmp(hash,current.new_hash,32))return EBADMSG;
    FILE *f=fopen(TEMP,"rb+");if(!f)return errno;e=flush_close(f);if(e)return e;
    if((e=original_matches(&current)))return e;
    if(current.exists && rename(current.path,BACKUP))return errno;
    if(rename(TEMP,current.path)) {
        e=errno;if(current.exists)rename(BACKUP,current.path);return e;
    }
    e=sidecar_hash(current.path,hash,&size);
    if(e || size!=current.size || memcmp(hash,current.new_hash,32))return e?e:EIO;
    if(unlink(JOURNAL))return errno;
    active=false;return 0;
}
int sidecar_recover(void) {
    FILE *f=fopen(JOURNAL,"rb");if(!f)return errno==ENOENT?0:errno;
    Journal j;size_t n=fread(&j,1,sizeof(j),f);fclose(f);
    if(n!=sizeof(j) || j.magic!=0x34584353 || !memchr(j.path,0,sizeof(j.path)) ||
       !sidecar_path(j.path,true) || j.exists>1 || j.size>LIMIT)return EINVAL;
    struct stat st;
    if(stat(j.path,&st) && errno==ENOENT && j.exists) {
        unsigned char hash[32];uint64_t size;int e=sidecar_hash(BACKUP,hash,&size);
        /* No backup means failure happened before the original was moved. */
        if(e || memcmp(hash,j.old_hash,32))return e?e:EBADMSG;
        if(rename(BACKUP,j.path))return errno;
    }
    /* Accept only the known original or complete replacement; never erase
     * recovery evidence if some other writer touched the target. */
    unsigned char hash[32];uint64_t size;int e=sidecar_hash(j.path,hash,&size);
    bool old_ok=(!j.exists && e==ENOENT) || (j.exists && !e && !memcmp(hash,j.old_hash,32));
    bool new_ok=!e && size==j.size && !memcmp(hash,j.new_hash,32);
    if(!old_ok && !new_ok)return ESTALE;
    if(unlink(TEMP) && errno!=ENOENT)return errno;
    if(unlink(JOURNAL))return errno;
    active=false;return 0;
}
int sidecar_abort(void){return sidecar_recover();}
