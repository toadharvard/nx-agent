// SPDX-License-Identifier: MIT
#include <switch.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <poll.h>
#include <stdatomic.h>
#include <dirent.h>
#include <sys/stat.h>
#include "fileops.h"
#include "control.h"
#include "listener.h"
#include "mailbox.h"

// Fixed-size request/reply frames: magic, opcode/status, length, reserved, data.
// No attach, memory changes or debugger commands are generated on the console.
static unsigned char rx[4096] __attribute__((aligned(4096)));
static unsigned char tx[4096] __attribute__((aligned(4096)));
static int fd = -1;
static atomic_uint commands;
static atomic_bool stopping;
static bool shutdown_requested;
static u64 last_command_tick;
static void startup_log(const char *stage,Result rc);

static int read_log(unsigned op,unsigned len,int *error) {
    unsigned prefix=op==16?4:8;
    if(len<=prefix || len-prefix>=1024) {*error=EINVAL;return 0;}
    char path[1024];memcpy(path,rx+16+prefix,len-prefix);path[len-prefix]=0;
    if(memchr(path,0,len-prefix) || !sidecar_path(path,false)) {*error=EACCES;return 0;}
    if(op==16) {
        unsigned index;memcpy(&index,rx+16,4);
        DIR *dir=opendir(path);if(!dir){*error=errno;return 0;}
        struct dirent *entry;unsigned i=0;int n=0;
        while((entry=readdir(dir))) {
            if(!strcmp(entry->d_name,".")||!strcmp(entry->d_name,".."))continue;
            if(i++!=index)continue;
            size_t size=strlen(entry->d_name);
            if(size>1000){*error=ENAMETOOLONG;break;}
            char full[2048];snprintf(full,sizeof(full),"%s/%s",path,entry->d_name);
            struct stat st;if(stat(full,&st)){*error=errno;break;}
            tx[16]=S_ISDIR(st.st_mode)?1:0;
            memcpy(tx+17,entry->d_name,size);n=(int)size+1;break;
        }
        closedir(dir);return n;
    }
    u64 offset;memcpy(&offset,rx+16,8);
    if(offset>0x7fffffffULL){*error=EFBIG;return 0;}
    FILE *f=fopen(path,"rb");if(!f){*error=errno;return 0;}
    int n=0;
    if(fseeko(f,(off_t)offset,SEEK_SET))*error=errno;
    else {n=fread(tx+16,1,4080,f);if(ferror(f))*error=EIO;}
    fclose(f);return n;
}
static int path_arg(unsigned offset,unsigned len,char out[768]) {
    if(!len || len>=768 || memchr(rx+16+offset,0,len))return EINVAL;
    memcpy(out,rx+16+offset,len);out[len]=0;return 0;
}
static int tree_op(unsigned op,unsigned len) {
    char from[768],to[768];
    if(op!=36)return path_arg(0,len,from)?EINVAL:op==37?sidecar_unlink(from):op==38?sidecar_rmdir(from):sidecar_mkdir(from);
    unsigned char *sep=len?memchr(rx+16,0,len):NULL;
    unsigned a=sep?(unsigned)(sep-(rx+16)):0;
    if(!a || path_arg(0,a,from) || path_arg(a+1,len-a-1,to))return EINVAL;
    return sidecar_rename(from,to);
}
static void dispatch(void) {
    unsigned *in=(unsigned *)rx, *out=(unsigned *)tx;
    memset(tx,0,sizeof(tx)); out[0]=0x31425255;
    if(in[0]!=out[0] || in[2]>4080) {out[1]=EINVAL;return;}
    unsigned op=in[1], len=in[2]; int n=0, error=0;
    char stage[48];bool trace=op>=40 && op<=62 && op!=44 && op!=48 && op!=54;
    if(trace){snprintf(stage,sizeof(stage),"command-%u-begin",op);startup_log(stage,0);}
    if(op==6) {
        unsigned info[]={7,0xfffffffeU,0,atomic_load(&commands)};
        memcpy(tx+16,info,sizeof(info));n=sizeof(info);
    } else if(op==9) {
        /* features: rail pair, sequences, R2C, wake alarm, wakes-at-power-change, extended state, autohold, power telemetry, file tree ops. */
        unsigned info[]={7,0x3f,256*1024*1024,1000,sidecar_pending(),511};
        memcpy(tx+16,info,sizeof(info));n=sizeof(info);
    } else if(op>=40 && op<=62) n=sidecar_control(op,rx+16,len,tx+16,&error);
    else if(op==18) {
        if(!len || len>=768 || memchr(rx+16,0,len))error=EINVAL;
        else {
            char path[768];memcpy(path,rx+16,len);path[len]=0;
            if(!sidecar_path(path,false))error=EACCES;
            else {uint64_t size=0;error=sidecar_hash(path,tx+24,&size);memcpy(tx+16,&size,8);if(!error)n=40;}
        }
    } else if(op==32) {
        if(len<=73 || len-73>=768 || rx[16]>1 || memchr(rx+16+73,0,len-73))error=EINVAL;
        else {
            char path[768];uint64_t size;memcpy(&size,rx+17,8);
            memcpy(path,rx+16+73,len-73);path[len-73]=0;
            error=sidecar_begin(path,size,rx+25,rx+57,rx[16]);
        }
    } else if(op==33) {
        if(len<8)error=EINVAL;
        else {uint64_t offset;memcpy(&offset,rx+16,8);error=sidecar_chunk(offset,rx+24,len-8);}
    } else if(op>=36 && op<=39) {error=tree_op(op,len);}
    else if(op==34) {error=len?EINVAL:sidecar_commit();}
    else if(op==35) {error=len?EINVAL:sidecar_abort();}
    else if(op==7) {if(sidecar_pending())error=EBUSY;else shutdown_requested=true;}
    else if(op==8) {error=EACCES;} else if(op==16 || op==17) n=read_log(op,len,&error);
    else if(op==1) {
        if(fd>=0) close(fd);
        fd=socket(AF_INET,SOCK_STREAM,0);
        if(fd<0) error=errno;
        else if(fcntl(fd,F_SETFL,O_NONBLOCK)<0) error=errno;
        else {
            struct sockaddr_in a={.sin_family=AF_INET,.sin_port=htons(22225)};
            a.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
            if(connect(fd,(struct sockaddr *)&a,sizeof(a))<0) error=errno;
        }
    } else if(op==4) {if(fd>=0) close(fd); fd=-1;}
    else if(fd<0) error=ENOTCONN;
    else if(op==2) {n=send(fd,rx+16,len,0);if(n<0) error=errno;}
    else if(op==3) {n=recv(fd,tx+16,4080,0);if(n<0) error=errno;else if(n==0) error=ECONNRESET;}
    else if(op==5) {
        struct pollfd p={.fd=fd,.events=POLLOUT};
        int r=poll(&p,1,0);
        if(r<0) error=errno;
        else if(!r) error=EINPROGRESS;
        else {socklen_t size=sizeof(error); if(getsockopt(fd,SOL_SOCKET,SO_ERROR,&error,&size)<0) error=errno;}
    } else error=EINVAL;
    if(trace){snprintf(stage,sizeof(stage),"command-%u-end",op);startup_log(stage,(Result)error);}
    out[1]=error; out[2]=n>0?(unsigned)n:0;
    // Expose platform errno constants, avoiding host/guest errno assumptions.
    out[3]=(error==EAGAIN||error==EWOULDBLOCK||error==EINPROGRESS)?1:0;
    atomic_fetch_add(&commands,1);
    last_command_tick=armGetSystemTick();
    sidecar_note_host();
}
static void worker_loop(void) {
    WorkerListener listener={.fd=-1,.port=22226};
    int last_listener_error=0;
    int peer=-1;size_t received=0,sent=0;bool replying=false;
    last_command_tick=armGetSystemTick();
    while(!atomic_load(&stopping)) {
        {
            int error=0;u64 now_ms=armTicksToNs(armGetSystemTick())/1000000ULL;
            int fresh=worker_listener_poll(&listener,now_ms,&error);
            if(error && error!=last_listener_error){startup_log("listener-retry",error);last_listener_error=error;}
            if(fresh>=0){
                /* Rescue keeps one connection; a new one means the old peer is dead (e.g. after sleep). */
                if(peer>=0){close(peer);startup_log("peer-replaced",0);}
                peer=fresh;
                if(last_listener_error){startup_log("listener-recovered",0);last_listener_error=0;}
                received=sent=0;replying=false;
            }
        }
        if(peer>=0){
            ssize_t n=replying?send(peer,tx+sent,sizeof(tx)-sent,0):recv(peer,rx+received,sizeof(rx)-received,0);
            if(n>0){
                if(replying){sent+=n;if(sent==sizeof(tx)){replying=false;received=sent=0;if(shutdown_requested)atomic_store(&stopping,true);}}
                else {received+=n;if(received==sizeof(rx)){dispatch();replying=true;sent=0;}}
            }else if(n==0 || (errno!=EAGAIN && errno!=EWOULDBLOCK && errno!=EINTR)){
                close(peer);peer=-1;
            }
        }
        /* Idle closes only the debug relay socket; the pad has its own 600 s lifetime. */
        if(fd>=0 && armTicksToNs(armGetSystemTick()-last_command_tick)>10000000000ULL){close(fd);fd=-1;}
        if(mailbox_tick(armTicksToNs(armGetSystemTick())/1000000ULL))last_command_tick=armGetSystemTick();
        sidecar_control_tick();svcSleepThread(1000000);
    }
    if(peer>=0)close(peer);
    worker_listener_close(&listener);sidecar_control_shutdown();
    if(fd>=0){close(fd);fd=-1;}
}

u32 __nx_applet_type=AppletType_None;
u32 __nx_fs_num_sessions=1;
static Result init_result;
static bool sm_ready,fs_ready,sd_ready;
void __libnx_initheap(void) {
    static u8 heap[0x200000];
    extern void *fake_heap_start,*fake_heap_end;
    fake_heap_start=heap;fake_heap_end=heap+sizeof(heap);
}
void __appInit(void) {
    init_result=smInitialize();if(R_FAILED(init_result))return;sm_ready=true;
    init_result=setsysInitialize();if(R_FAILED(init_result))return;
    SetSysFirmwareVersion fw;init_result=setsysGetFirmwareVersion(&fw);setsysExit();
    if(R_FAILED(init_result))return;
    hosversionSet(MAKEHOSVERSION(fw.major,fw.minor,fw.micro));
    init_result=fsInitialize();if(R_FAILED(init_result))return;fs_ready=true;
    sd_ready=fsdevMountSdmc()==0;
}
void __appExit(void) {
    if(sd_ready)fsdevUnmountAll();
    if(fs_ready)fsExit();
    if(sm_ready)smExit();
}
static void startup_log(const char *stage,Result rc) {
    if(!sd_ready)return;
    const char *path="sdmc:/atmosphere/binlogs/usb-worker-status.txt";
    struct stat st;if(!stat(path,&st) && st.st_size>1024*1024)rename(path,"sdmc:/atmosphere/binlogs/usb-worker-status.previous.txt");
    FILE *f=fopen(path,"a");
    if(f){fprintf(f,"tick=%llu stage=%s result=%08X\n",(unsigned long long)armGetSystemTick(),stage,rc);fclose(f);}
}
void worker_log(const char *stage,uint32_t rc){startup_log(stage,(Result)rc);}
int main(void) {
    if(R_FAILED(init_result)){startup_log("init",init_result);return 1;}
    int control_recovery=sidecar_control_boot();startup_log("sleep-recovery",(Result)control_recovery);
    int recovery=sidecar_recover();startup_log("transaction-recovery",(Result)recovery);
    const SocketInitConfig cfg={.tcp_tx_buf_size=0x4000,.tcp_rx_buf_size=0x4000,
        .udp_tx_buf_size=0x1000,.udp_rx_buf_size=0x1000,.sb_efficiency=2,
        .num_bsd_sessions=2,.bsd_service_type=BsdServiceType_System};
    Result rc=socketInitialize(&cfg);startup_log("socket",rc);
    if(R_FAILED(rc))return 2;
    worker_loop();
    socketExit();startup_log("exit",0);return 0;
}
