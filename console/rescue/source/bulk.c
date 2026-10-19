// SPDX-License-Identifier: MIT
#include "bulk.h"
#ifdef SIDECAR_HOST_TEST
#include <openssl/sha.h>
typedef SHA256_CTX Sha256Context;
#define sha256ContextCreate SHA256_Init
#define sha256ContextUpdate SHA256_Update
#define sha256ContextGetHash(c,h) SHA256_Final(h,c)
#else
#include <switch.h>
#endif
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define BASE "sdmc:/config/usb-rescue"
#define JOURNAL BASE "/bulk-pending.bin"
#define TEMP BASE "/bulk.tmp"
#define BACKUP BASE "/bulk-previous.bin"
#define JOURNAL_MAGIC 0x4b4c5542u
#define HASH_CHUNK (128*1024)

/* Persistent journal of the one open transaction; it protects the rename gap on FAT. */
typedef struct {
    uint32_t magic, exists;
    uint64_t size;
    unsigned char old_hash[32], new_hash[32];
    char path[768];
} Journal;

static Journal current;
static bool active;
static int temp_fd=-1, read_fd=-1;
static uint64_t received;
static int write_error;
static Sha256Context running;
static unsigned char hash_buf[HASH_CHUNK];

/* Same rules as the Worker's sidecar_path: the whole SD except secrets, user data, the transaction areas and
 * the nx-agent modules. console/tests compares both on one path list. */
static const char *const PROTECTED[]={"sdmc:/config/usb-rescue","sdmc:/config/usb-sidecar",
    "sdmc:/atmosphere/contents/420000000000d06b","sdmc:/atmosphere/contents/420000000000d06c"};
static bool under(const char *path,const char *root) {
    size_t n=strlen(root);return !strncmp(path,root,n) && (!path[n] || path[n]=='/');
}
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
bool bulk_path(const char *p,bool write) {
    char lower[768];
    if(!lowercase(p,lower))return false;
    if(under(lower,"sdmc:/config/usb-sidecar"))return !write;
    if(!write)return true;
    size_t n=strlen(lower);
    if(n<=6 || lower[n-1]=='/')return false;
    for(size_t i=0;i<sizeof(PROTECTED)/sizeof(*PROTECTED);i++)if(under(lower,PROTECTED[i]))return false;
    return true;
}

int bulk_hash(const char *path,unsigned char hash[32],uint64_t *size) {
    int fd=open(path,O_RDONLY);if(fd<0)return errno;
    Sha256Context ctx;sha256ContextCreate(&ctx);*size=0;
    ssize_t n;
    while((n=read(fd,hash_buf,sizeof(hash_buf)))>0){sha256ContextUpdate(&ctx,hash_buf,(size_t)n);*size+=(uint64_t)n;}
    int e=n<0?errno:0;close(fd);if(!e)sha256ContextGetHash(&ctx,hash);return e;
}
static int write_all(int fd,const unsigned char *data,size_t size) {
    while(size) {
        ssize_t n=write(fd,data,size);
        if(n<=0)return n<0?errno:EIO;
        data+=n;size-=(size_t)n;
    }
    return 0;
}
static int original_matches(const Journal *j) {
    unsigned char hash[32];uint64_t size;int e=bulk_hash(j->path,hash,&size);
    if(!j->exists)return e==ENOENT?0:(e?e:EEXIST);
    return e?e:(memcmp(hash,j->old_hash,32)?ESTALE:0);
}
static bool pending(void){return active || access(JOURNAL,F_OK)==0;}

int bulk_begin(const char *path,uint64_t size,const unsigned char old_hash[32],const unsigned char new_hash[32],bool exists) {
    if(!bulk_path(path,true))return EACCES;
    if(size>BULK_MAX)return EFBIG;
    if(pending())return EBUSY;
    struct stat st;
    if(!stat(path,&st) && S_ISDIR(st.st_mode))return EISDIR;
    Journal j={.magic=JOURNAL_MAGIC,.exists=exists,.size=size};
    strcpy(j.path,path);memcpy(j.old_hash,old_hash,32);memcpy(j.new_hash,new_hash,32);
    int e=original_matches(&j);if(e)return e;
    if(mkdir(BASE,0700) && errno!=EEXIST)return errno;
    if(unlink(BACKUP) && errno!=ENOENT)return errno;
    int fd=open(JOURNAL,O_WRONLY|O_CREAT|O_TRUNC,0600);if(fd<0)return errno;
    e=write_all(fd,(const unsigned char *)&j,sizeof(j));
    if(!e && fsync(fd))e=errno;
    if(close(fd) && !e)e=errno;
    if(e){unlink(JOURNAL);return e;}
    temp_fd=open(TEMP,O_WRONLY|O_CREAT|O_TRUNC,0600);
    if(temp_fd<0){e=errno;unlink(JOURNAL);return e;}
    if(size && ftruncate(temp_fd,(off_t)size)){e=errno?errno:EIO;close(temp_fd);temp_fd=-1;unlink(TEMP);unlink(JOURNAL);return e;}
    current=j;received=0;write_error=0;active=true;sha256ContextCreate(&running);
    return 0;
}

/* Accepts every byte of the announced size even after a local write error, so the caller can drain USB and
 * report the error at commit. */
int bulk_write(const void *data,size_t size) {
    if(!active || temp_fd<0 || size>current.size-received)return EINVAL;
    sha256ContextUpdate(&running,data,size);
    received+=size;
    if(!write_error)write_error=write_all(temp_fd,data,size);
    return write_error;
}

int bulk_commit(void) {
    if(!active)return EINVAL;
    int e=write_error;
    if(temp_fd>=0){if(!e && fsync(temp_fd))e=errno;if(close(temp_fd) && !e)e=errno;temp_fd=-1;}
    if(!e && received!=current.size)e=EIO;
    unsigned char hash[32];
    if(!e){sha256ContextGetHash(&running,hash);if(memcmp(hash,current.new_hash,32))e=EBADMSG;}
    if(!e)e=original_matches(&current);
    if(!e && current.exists && rename(current.path,BACKUP))e=errno;
    if(!e && rename(TEMP,current.path)){e=errno;if(current.exists)rename(BACKUP,current.path);}
    struct stat st;
    if(!e && (stat(current.path,&st) || (uint64_t)st.st_size!=current.size))e=EIO;
    if(!e && unlink(JOURNAL))e=errno;
    if(e){bulk_recover();return e;}
    active=false;return 0;
}

/* Boot and failure path: keep either the original or the complete new file, never a mix. */
int bulk_recover(void) {
    if(temp_fd>=0){close(temp_fd);temp_fd=-1;}
    active=false;
    int fd=open(JOURNAL,O_RDONLY);if(fd<0)return errno==ENOENT?0:errno;
    Journal j;ssize_t n=read(fd,&j,sizeof(j));close(fd);
    if(n!=(ssize_t)sizeof(j) || j.magic!=JOURNAL_MAGIC || !memchr(j.path,0,sizeof(j.path)) ||
       !bulk_path(j.path,true) || j.exists>1)return EINVAL;
    struct stat st;
    if(stat(j.path,&st) && errno==ENOENT && j.exists) {
        unsigned char hash[32];uint64_t size;int e=bulk_hash(BACKUP,hash,&size);
        if(e || memcmp(hash,j.old_hash,32))return e?e:EBADMSG;
        if(rename(BACKUP,j.path))return errno;
    }
    unsigned char hash[32];uint64_t size;int e=bulk_hash(j.path,hash,&size);
    bool old_ok=(!j.exists && e==ENOENT) || (j.exists && !e && !memcmp(hash,j.old_hash,32));
    bool new_ok=!e && size==j.size && !memcmp(hash,j.new_hash,32);
    if(!old_ok && !new_ok)return ESTALE;
    if(unlink(TEMP) && errno!=ENOENT)return errno;
    if(unlink(JOURNAL))return errno;
    return 0;
}

int bulk_open_read(const char *path,uint64_t *size,unsigned char hash[32]) {
    bulk_close_read();
    if(!bulk_path(path,false))return EACCES;
    struct stat st;
    if(stat(path,&st))return errno;
    if(!S_ISREG(st.st_mode))return EISDIR;
    int e=bulk_hash(path,hash,size);if(e)return e;
    read_fd=open(path,O_RDONLY);
    return read_fd<0?errno:0;
}
long bulk_read(void *data,size_t size) {
    if(read_fd<0)return -EBADF;
    ssize_t n=read(read_fd,data,size);
    return n<0?-errno:(long)n;
}
void bulk_close_read(void){if(read_fd>=0)close(read_fd);read_fd=-1;}
